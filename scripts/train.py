"""P2 train: walk-forward backtest (365-day gap), GBDT + logistic regression vs three baselines.

For eval year Y: train = resolved obs with asof <= (Y-01-01 − 365d); test = obs with asof in Y and resolved.
Top-k coverage is measured on quarter-end snapshots inside Y (one ranking per snapshot, averaged).
A model 'beats_baseline' only if BOTH mean AUC and mean top-100 coverage exceed the count baseline
(架構定調 3). Everything is seeded; feature_hash/eval_year/seed recorded in app_models.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import pickle
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, balanced_accuracy_score, brier_score_loss, cohen_kappa_score, confusion_matrix,
                             f1_score, log_loss, matthews_corrcoef, precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, ROOT, connect  # noqa: E402
from errors import PipelineError  # noqa: E402
from features import EVENT_FEATURES, FEATURE_SETS, feature_frame, feature_hash, is_quarter_end, rule_score  # noqa: E402

SEED = 42
MIN_POS = 50
MODEL_DIR = ROOT / "data" / "models"
GAP_DAYS = 365
BEATS_MARGIN = 0.02  # a 'win' inside this margin is noise, not evidence (架構定調 3)

BASELINES = {
    "count": lambda d: d["n_events_total"].astype(float),
    "recency": lambda d: -d["days_since_last"].astype(float),
    "rule": rule_score,
}


def make_model(algo: str):
    if algo == "gbdt":
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                              min_samples_leaf=30, random_state=SEED)
    if algo == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500, random_state=SEED))
    raise PipelineError(f"未知演算法 {algo}", "algo 只支援 gbdt / logreg", "檢查 --algo 參數", stage="train")


def _topk_coverage(test: pd.DataFrame, score: pd.Series, k: int) -> float:
    """Mean over quarter-end snapshots of (positives inside top-k) / (all positives at that snapshot)."""
    t = test.assign(_s=score.values)
    covs = []
    for a, g in t.groupby("asof"):
        if not is_quarter_end(a):
            continue
        pos = g["label"].sum()
        if pos == 0:
            continue
        top = g.sort_values("_s", ascending=False).head(k)
        covs.append(top["label"].sum() / pos)
    return float(np.mean(covs)) if covs else float("nan")


def classification_metrics(y, prob, thresholds: dict[str, float]) -> dict:
    """Confusion-matrix metrics at each operating threshold (Verdandi-AutoML style), plus proper-scoring metrics."""
    y = np.asarray(y).astype(int); prob = np.clip(np.asarray(prob, dtype=float), 1e-6, 1 - 1e-6)
    out = {"brier": float(brier_score_loss(y, prob)), "log_loss": float(log_loss(y, prob)), "n": int(len(y)), "n_pos": int(y.sum()),
           "base_rate": float(y.mean()), "at": {}}
    for name, thr in thresholds.items():
        pred = (prob >= thr).astype(int)
        tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
        out["at"][name] = {"threshold": thr, "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
                           "accuracy": float((tp + tn) / max(len(y), 1)), "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
                           "precision": float(precision_score(y, pred, zero_division=0)), "recall": float(recall_score(y, pred, zero_division=0)),
                           "specificity": float(tn / max(tn + fp, 1)), "f1": float(f1_score(y, pred, zero_division=0)),
                           "kappa": float(cohen_kappa_score(y, pred)), "mcc": float(matthews_corrcoef(y, pred)) if len(set(pred)) > 1 else 0.0,
                           "flagged": int(pred.sum())}
    return out


def _metrics(test: pd.DataFrame, score: pd.Series, thresholds: dict[str, float] | None = None) -> dict:
    y = test["label"].astype(int)
    cm = classification_metrics(y, score, thresholds) if thresholds else None
    return {"metrics": cm,
        "auc": float(roc_auc_score(y, score)) if y.nunique() > 1 else float("nan"),
        "pr_auc": float(average_precision_score(y, score)) if y.nunique() > 1 else float("nan"),
        "top50": _topk_coverage(test, score, 50),
        "top100": _topk_coverage(test, score, 100),
        "top200": _topk_coverage(test, score, 200),
        "lead_days_median": float(test.loc[y == 1, "next_event_days"].median()) if y.sum() else float("nan"),
    }


def walk_forward(df: pd.DataFrame, algo: str, years: list[int], feats: list[str] | None = None, thresholds: dict[str, float] | None = None) -> list[dict]:
    feats = feats or EVENT_FEATURES
    thresholds = thresholds or {"mid": 0.18, "high": 0.30}
    rows = []
    for y in years:
        cut = date(y, 1, 1) - timedelta(days=GAP_DAYS)
        train = df[(df["label_resolved"] == 1) & (df["asof"] <= cut)]
        test = df[(df["label_resolved"] == 1) & (df["asof"].map(lambda d: d.year) == y)]
        if len(test) == 0 or train["label"].sum() < MIN_POS:
            continue
        model = make_model(algo).fit(train[feats], train["label"].astype(int))
        pred = pd.Series(model.predict_proba(test[feats])[:, 1], index=test.index)
        rows.append({"obs_year": y, "n_obs": len(test), "n_pos": int(test["label"].sum()), "baseline": None, **_metrics(test, pred, thresholds)})
        for name, fn in BASELINES.items():
            rows.append({"obs_year": y, "n_obs": len(test), "n_pos": int(test["label"].sum()), "baseline": name, **_metrics(test, fn(test))})
    return rows


def train_and_record(con: sqlite3.Connection, algo: str, data_asof: str, years: list[int] | None = None, feature_set: str = "events") -> int:
    feats = FEATURE_SETS[feature_set]
    df = feature_frame(con, data_asof, feature_set=feature_set)
    resolved = df[df["label_resolved"] == 1]
    if resolved["label"].sum() < MIN_POS:
        raise PipelineError("正例不足，不產生模型版本", f"已解析正例 {int(resolved['label'].sum())} < {MIN_POS}",
                            "累積更多裁罰事件後再訓練", stage="train")
    years = years or sorted({d.year for d in resolved["asof"]})[3:]  # first three years are training-only
    st = dict(con.execute("SELECT key, value FROM app_settings").fetchall())
    thresholds = {"mid": float(st.get("mid_threshold", 0.18)), "high": float(st.get("high_threshold", 0.30))}
    bt = walk_forward(df, algo, years, feats, thresholds)
    if not bt:
        raise PipelineError("回測無可用年份", "每個測試年都缺訓練正例或測試觀察點", "調整 --years", stage="train")
    model_rows = [r for r in bt if r["baseline"] is None]
    count_rows = [r for r in bt if r["baseline"] == "count"]
    rec_rows = [r for r in bt if r["baseline"] == "recency"]
    mean = lambda rows, k: float(np.nanmean([r[k] for r in rows]))  # noqa: E731
    auc, top100 = mean(model_rows, "auc"), mean(model_rows, "top100")
    b_auc, b_top100 = mean(count_rows, "auc"), mean(count_rows, "top100")
    # win on one metric by the margin without losing on the other (架構定調 3, refined 2026-09-12: Peter wants the score to be a model)
    beats = int((auc > b_auc + BEATS_MARGIN and top100 >= b_top100 - BEATS_MARGIN) or (top100 > b_top100 + BEATS_MARGIN and auc >= b_auc - BEATS_MARGIN))

    final = make_model(algo).fit(resolved[feats], resolved["label"].astype(int))
    params = json.dumps({"algo": algo, "years": years, "gap_days": GAP_DAYS, "min_pos": MIN_POS, "feature_set": feature_set}, sort_keys=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_DIR / f"model_pending_{os.getpid()}.pkl"
    with open(tmp, "wb") as f:
        pickle.dump({"model": final, "features": feats, "feature_set": feature_set, "feature_hash": feature_hash(feature_set), "algo": algo}, f)
        f.flush(); os.fsync(f.fileno())
    clash = con.execute("SELECT model_id, status FROM app_models WHERE data_asof=? AND feature_hash=? AND params=?", (data_asof, feature_hash(feature_set), params)).fetchone()
    if clash and clash[1] == "active":
        tmp.unlink(missing_ok=True)
        raise PipelineError("同參數版本已是 active，不覆寫", f"model_id {clash[0]}", "先撤銷該版本，或改資料日期／特徵組再訓練", stage="train")
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute(
            """INSERT INTO app_models(trained_at, algo, params, seed, feature_hash, data_asof, eval_year, n_train_obs,
                 auc, pr_auc, top100_cov, baseline_count_auc, baseline_count_top100, baseline_recency_auc, beats_baseline, status, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'trained',?)
               ON CONFLICT(data_asof, feature_hash, params) DO UPDATE SET trained_at=excluded.trained_at, auc=excluded.auc,
                 pr_auc=excluded.pr_auc, top100_cov=excluded.top100_cov, baseline_count_auc=excluded.baseline_count_auc,
                 baseline_count_top100=excluded.baseline_count_top100, baseline_recency_auc=excluded.baseline_recency_auc,
                 beats_baseline=excluded.beats_baseline, n_train_obs=excluded.n_train_obs""",
            (date.today().isoformat(), algo, params, SEED, feature_hash(feature_set), data_asof, years[-1], len(resolved),
             auc, mean(model_rows, "pr_auc"), top100, b_auc, b_top100, mean(rec_rows, "auc"), beats,
             f"walk-forward {years[0]}–{years[-1]} · {feature_set}"),
        )
        model_id = con.execute("SELECT model_id FROM app_models WHERE data_asof=? AND feature_hash=? AND params=?",
                               (data_asof, feature_hash(feature_set), params)).fetchone()[0]
        os.replace(tmp, MODEL_DIR / f"model_{model_id}.pkl")   # artifact exists before the row becomes visible
        con.execute("DELETE FROM app_backtests WHERE model_id=?", (model_id,))
        con.executemany(
            "INSERT INTO app_backtests(model_id, obs_year, n_obs, n_pos, auc, pr_auc, top50, top100, top200, lead_days_median, baseline, metrics) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(model_id, r["obs_year"], r["n_obs"], r["n_pos"], r["auc"], r["pr_auc"], r["top50"], r["top100"], r["top200"], r["lead_days_median"], r["baseline"], json.dumps(r["metrics"]) if r.get("metrics") else None) for r in bt],
        )
        con.execute("INSERT INTO app_model_events(model_id, at, from_status, to_status, actor, reason) VALUES (?,?,?,?,?,?)",
                    (model_id, date.today().isoformat(), None, "trained", "train.py", f"beats_baseline={beats}"))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        tmp.unlink(missing_ok=True)
        raise
    return model_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest + train final model")
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--algo", default="gbdt", choices=["gbdt", "logreg"])
    ap.add_argument("--years", default=None, help="comma-separated eval years")
    ap.add_argument("--features", default="events", choices=list(FEATURE_SETS))
    args = ap.parse_args(argv)
    con = connect(args.db)
    data_asof = con.execute("SELECT value FROM app_settings WHERE key='data_asof'").fetchone()[0]
    years = [int(y) for y in args.years.split(",")] if args.years else None
    try:
        mid = train_and_record(con, args.algo, data_asof, years, args.features)
    except PipelineError as e:
        print(e.format(), file=sys.stderr)
        return e.exit_code
    row = con.execute("SELECT algo, auc, top100_cov, baseline_count_auc, baseline_count_top100, baseline_recency_auc, beats_baseline FROM app_models WHERE model_id=?", (mid,)).fetchone()
    print(f"model_id={mid} algo={row[0]} auc={row[1]:.3f} top100={row[2]:.3f} | count auc={row[3]:.3f} top100={row[4]:.3f} | recency auc={row[5]:.3f} | beats={row[6]}")
    print(pd.read_sql_query("SELECT obs_year, COALESCE(baseline,'model') AS who, n_obs, n_pos, ROUND(auc,3) auc, ROUND(top100,3) top100, lead_days_median FROM app_backtests WHERE model_id=? ORDER BY obs_year, who", con, params=(mid,)).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""P2 train: walk-forward backtest (365-day gap), GBDT + logistic regression vs three baselines.

For eval year Y: train = resolved obs with asof <= (Y-01-01 − 365d); test = obs with asof in Y and resolved.
Top-k coverage is measured on quarter-end snapshots inside Y (one ranking per snapshot, averaged).
A model 'beats_baseline' only if BOTH mean AUC and mean top-100 coverage exceed the count baseline
(架構定調 3). Everything is seeded; feature_hash/eval_year/seed recorded in app_models.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import pickle
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, ROOT, connect  # noqa: E402
from errors import PipelineError  # noqa: E402
from features import EVENT_FEATURES, feature_frame, feature_hash, is_quarter_end, rule_score  # noqa: E402

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


def _metrics(test: pd.DataFrame, score: pd.Series) -> dict:
    y = test["label"].astype(int)
    return {
        "auc": float(roc_auc_score(y, score)) if y.nunique() > 1 else float("nan"),
        "pr_auc": float(average_precision_score(y, score)) if y.nunique() > 1 else float("nan"),
        "top50": _topk_coverage(test, score, 50),
        "top100": _topk_coverage(test, score, 100),
        "top200": _topk_coverage(test, score, 200),
        "lead_days_median": float(test.loc[y == 1, "next_event_days"].median()) if y.sum() else float("nan"),
    }


def walk_forward(df: pd.DataFrame, algo: str, years: list[int]) -> list[dict]:
    rows = []
    for y in years:
        cut = date(y, 1, 1) - timedelta(days=GAP_DAYS)
        train = df[(df["label_resolved"] == 1) & (df["asof"] <= cut)]
        test = df[(df["label_resolved"] == 1) & (df["asof"].map(lambda d: d.year) == y)]
        if len(test) == 0 or train["label"].sum() < MIN_POS:
            continue
        model = make_model(algo).fit(train[EVENT_FEATURES], train["label"].astype(int))
        pred = pd.Series(model.predict_proba(test[EVENT_FEATURES])[:, 1], index=test.index)
        rows.append({"obs_year": y, "n_obs": len(test), "n_pos": int(test["label"].sum()), "baseline": None, **_metrics(test, pred)})
        for name, fn in BASELINES.items():
            rows.append({"obs_year": y, "n_obs": len(test), "n_pos": int(test["label"].sum()), "baseline": name, **_metrics(test, fn(test))})
    return rows


def train_and_record(con: sqlite3.Connection, algo: str, data_asof: str, years: list[int] | None = None) -> int:
    df = feature_frame(con, data_asof)
    resolved = df[df["label_resolved"] == 1]
    if resolved["label"].sum() < MIN_POS:
        raise PipelineError("正例不足，不產生模型版本", f"已解析正例 {int(resolved['label'].sum())} < {MIN_POS}",
                            "累積更多裁罰事件後再訓練", stage="train")
    years = years or sorted({d.year for d in resolved["asof"]})[3:]  # first three years are training-only
    bt = walk_forward(df, algo, years)
    if not bt:
        raise PipelineError("回測無可用年份", "每個測試年都缺訓練正例或測試觀察點", "調整 --years", stage="train")
    model_rows = [r for r in bt if r["baseline"] is None]
    count_rows = [r for r in bt if r["baseline"] == "count"]
    rec_rows = [r for r in bt if r["baseline"] == "recency"]
    mean = lambda rows, k: float(np.nanmean([r[k] for r in rows]))  # noqa: E731
    auc, top100 = mean(model_rows, "auc"), mean(model_rows, "top100")
    b_auc, b_top100 = mean(count_rows, "auc"), mean(count_rows, "top100")
    beats = int(auc > b_auc + BEATS_MARGIN and top100 > b_top100 + BEATS_MARGIN)

    final = make_model(algo).fit(resolved[EVENT_FEATURES], resolved["label"].astype(int))
    params = json.dumps({"algo": algo, "years": years, "gap_days": GAP_DAYS, "min_pos": MIN_POS}, sort_keys=True)
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
            (date.today().isoformat(), algo, params, SEED, feature_hash(), data_asof, years[-1], len(resolved),
             auc, mean(model_rows, "pr_auc"), top100, b_auc, b_top100, mean(rec_rows, "auc"), beats,
             f"walk-forward {years[0]}–{years[-1]}"),
        )
        model_id = con.execute("SELECT model_id FROM app_models WHERE data_asof=? AND feature_hash=? AND params=?",
                               (data_asof, feature_hash(), params)).fetchone()[0]
        con.execute("DELETE FROM app_backtests WHERE model_id=?", (model_id,))
        con.executemany(
            "INSERT INTO app_backtests(model_id, obs_year, n_obs, n_pos, auc, pr_auc, top50, top100, top200, lead_days_median, baseline) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(model_id, r["obs_year"], r["n_obs"], r["n_pos"], r["auc"], r["pr_auc"], r["top50"], r["top100"], r["top200"], r["lead_days_median"], r["baseline"]) for r in bt],
        )
        con.execute("INSERT INTO app_model_events(model_id, at, from_status, to_status, actor, reason) VALUES (?,?,?,?,?,?)",
                    (model_id, date.today().isoformat(), None, "trained", "train.py", f"beats_baseline={beats}"))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_DIR / f"model_{model_id}.pkl", "wb") as f:
        pickle.dump({"model": final, "features": EVENT_FEATURES, "feature_hash": feature_hash(), "algo": algo}, f)
    return model_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest + train final model")
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--algo", default="gbdt", choices=["gbdt", "logreg"])
    ap.add_argument("--years", default=None, help="comma-separated eval years")
    args = ap.parse_args(argv)
    con = connect(args.db)
    data_asof = con.execute("SELECT value FROM app_settings WHERE key='data_asof'").fetchone()[0]
    years = [int(y) for y in args.years.split(",")] if args.years else None
    try:
        mid = train_and_record(con, args.algo, data_asof, years)
    except PipelineError as e:
        print(e.format(), file=sys.stderr)
        return e.exit_code
    row = con.execute("SELECT algo, auc, top100_cov, baseline_count_auc, baseline_count_top100, baseline_recency_auc, beats_baseline FROM app_models WHERE model_id=?", (mid,)).fetchone()
    print(f"model_id={mid} algo={row[0]} auc={row[1]:.3f} top100={row[2]:.3f} | count auc={row[3]:.3f} top100={row[4]:.3f} | recency auc={row[5]:.3f} | beats={row[6]}")
    print(pd.read_sql_query("SELECT obs_year, COALESCE(baseline,'model') AS who, n_obs, n_pos, ROUND(auc,3) auc, ROUND(top100,3) top100, lead_days_median FROM app_backtests WHERE model_id=? ORDER BY obs_year, who", con, params=(mid,)).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

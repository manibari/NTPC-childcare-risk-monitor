"""P3 score: rule (回頭客燈號) → model only if an approved model beats the baseline → 「無紀錄」.

prob_12m for the rule is the empirical 12-month recidivism rate of the school's rule bucket
(events-36m × recency × child-safety) measured on resolved historical observation points, so the
absolute thresholds in app_settings mean the same thing whichever method is active.
Scores are written under a new score_batch and switched to current in one transaction.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import pickle
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, ROOT, connect  # noqa: E402
from errors import PipelineError  # noqa: E402
from features import EVENT_FEATURES, feature_frame, feature_hash, rule_reason, rule_score  # noqa: E402

MODEL_DIR = ROOT / "data" / "models"
SMOOTH = 20  # Laplace-style shrinkage of bucket rates toward the global rate


def bucket(df: pd.DataFrame) -> pd.Series:
    ev = df["n_events_36m"].clip(upper=4).astype(int).astype(str)
    rec = pd.cut(df["days_since_last"], [-1, 180, 365, 730, 10**6], labels=["r0", "r1", "r2", "r3"]).astype(str)
    cs = (df["n_child_safety_36m"] > 0).astype(int).astype(str)
    return "e" + ev + "|" + rec + "|c" + cs


def bucket_rates(resolved: pd.DataFrame) -> tuple[dict[str, float], float]:
    g = resolved.assign(b=bucket(resolved)).groupby("b")["label"].agg(["sum", "count"])
    base = float(resolved["label"].mean())
    rates = ((g["sum"] + SMOOTH * base) / (g["count"] + SMOOTH)).to_dict()
    return rates, base


FEATURE_ZH = {"n_events_total": "歷年事件數", "n_events_12m": "近 12 月事件", "n_events_36m": "近 36 月事件", "days_since_last": "距上次裁罰天數",
              "years_since_first": "首次裁罰至今年數", "events_per_year": "每年事件率", "n_child_safety_total": "歷年不當對待／安全",
              "n_child_safety_36m": "近 36 月不當對待／安全", "has_stop_enroll": "曾停止招生", "has_person_actor": "曾有個人行為人",
              "n_rows_total": "歷年裁罰列數", "n_articles_last": "最近一次條款數", "n_events_prev_12m": "前一年事件",
              "is_private": "私立", "is_nonprofit": "非營利", "count_approved": "核定人數", "years_since_reg": "立案年數", "is_pre_public": "準公共",
              "town_event_rate": "行政區 36 月事件率", "town_n_schools": "行政區園數"}


def _contributions(model, X: pd.DataFrame) -> list[list]:
    """Per-observation feature contributions: coef × standardised value for the logistic pipeline;
    for tree models fall back to the model's global feature importance weighted by the standardised value."""
    import numpy as _np
    steps = getattr(model, "named_steps", None)
    if steps and "logisticregression" in steps:
        Z = steps["standardscaler"].transform(X); coef = steps["logisticregression"].coef_[0]
        C = Z * coef
    else:
        Z = (X - X.mean()) / (X.std() + 1e-9); imp = getattr(model, "feature_importances_", _np.ones(X.shape[1]) / X.shape[1])
        C = Z.values * imp
    out = []
    for row in C:
        pairs = sorted(zip(list(X.columns), row), key=lambda kv: -abs(kv[1]))[:5]
        out.append([[FEATURE_ZH.get(k, k), round(float(v), 3)] for k, v in pairs])
    return out


def _levels(prob: pd.Series, settings: dict) -> pd.Series:
    hi, mid = float(settings["high_threshold"]), float(settings["mid_threshold"])
    return pd.Series(np.where(prob >= hi, "高", np.where(prob >= mid, "中", "低")), index=prob.index)


def score_all(con: sqlite3.Connection, asof: str | None = None, city: str = "新北市") -> dict:
    settings = dict(con.execute("SELECT key, value FROM app_settings").fetchall())
    asof = asof or settings["data_asof"]
    df = feature_frame(con, asof, city)
    cur = df[df["asof"].map(str) == asof].copy()
    resolved = df[df["label_resolved"] == 1]
    if cur.empty or resolved.empty:
        raise PipelineError("沒有可評分的觀察點", f"asof={asof} 無事件史園或無已解析標籤", "檢查 data_asof 與 src_penalty_events", stage="score")

    active = con.execute("SELECT model_id, algo, beats_baseline FROM app_models WHERE status='active'").fetchone()
    cur["rule"] = rule_score(cur)
    rates, base = bucket_rates(resolved)
    cur["prob_rule"] = bucket(cur).map(rates).fillna(base)
    method, model_id = "rule", None
    warning = None
    bundle = None
    if active and active[2]:
        pkl = MODEL_DIR / f"model_{active[0]}.pkl"
        if pkl.exists():
            with open(pkl, "rb") as f:
                bundle = pickle.load(f)
            fs = bundle.get("feature_set", "events")
            if bundle.get("feature_hash") != feature_hash(fs):
                warning = f"active 模型 #{active[0]} 的特徵版本不符（{bundle.get('feature_hash')} ≠ {feature_hash(fs)}），改用規則"
                bundle = None
        else:
            warning = f"active 模型 #{active[0]} 的模型檔不存在（{pkl.name}），改用規則；請重新訓練或撤銷"
    if bundle is not None:
        feats = bundle.get("features", EVENT_FEATURES)
        if fs != "events":
            cur = feature_frame(con, asof, city, feature_set=fs); cur = cur[cur["asof"].map(str) == asof].copy()
            cur["rule"] = rule_score(cur); cur["prob_rule"] = bucket(cur).map(rates).fillna(base)
        cur["prob"] = bundle["model"].predict_proba(cur[feats])[:, 1]
        method, model_id = f"model:{active[1]}", active[0]
    else:
        cur["prob"] = cur["prob_rule"]
    if warning:
        print("[score] 警告：" + warning, file=sys.stderr)
        con.execute("INSERT INTO app_pipeline_runs(run_id, stage, started_at, seconds, n_rows, n_failed, ok, message) VALUES ('score','score-warning',?,0,0,1,0,?)",
                    (date.today().isoformat(), warning))
    cur = cur.sort_values(["prob", "rule"], ascending=[False, False]).reset_index(drop=True)
    cur["rank"] = np.arange(1, len(cur) + 1)
    contrib = _contributions(bundle["model"], cur[bundle.get("features", EVENT_FEATURES)]) if model_id is not None else None
    if not (cur["prob"].max() > 0):
        raise PipelineError("所有再犯機率為 0，不寫入評分批次", "已解析標籤沒有任何正例或模型輸出全零", "檢查標籤窗與模型檔", stage="score")
    cur["risk_01"] = cur["prob"] / cur["prob"].max()
    cur["level"] = _levels(cur["prob"], settings)
    # display score 0–100 = relative 12-month recidivism probability (monotonic with rank)
    cur["score"] = (100 * cur["risk_01"]).round().astype(int)

    schools = pd.read_sql_query("SELECT id, is_active FROM src_preschools WHERE city=?", con, params=(city,))
    scored = set(cur["preschool_id"])
    now = date.today().isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        cur_b = con.execute("INSERT INTO app_score_batches(asof_date, model_id, method, created_at, status) VALUES (?,?,?,?,'building')",
                            (asof, model_id, method, now))
        bid = cur_b.lastrowid
        rows = []
        for r in cur.itertuples(index=True):
            top = {"n_events_36m": int(r.n_events_36m), "days_since_last": int(r.days_since_last),
                   "n_child_safety_36m": int(r.n_child_safety_36m), "has_stop_enroll": int(r.has_stop_enroll),
                   "rule_score": round(float(r.rule), 2), "prob_rule": round(float(r.prob_rule), 3)}
            if model_id is not None:
                top["contributions"] = contrib[r.Index if hasattr(r, "Index") else 0]
            rows.append((bid, r.preschool_id, method, float(r.prob), float(r.risk_01), int(r.score), int(r.rank), r.level,
                         rule_reason(pd.Series(r._asdict())), json.dumps(top, ensure_ascii=False)))
        for s in schools.itertuples(index=False):
            if s.id in scored:
                continue
            lvl = "停辦" if not s.is_active else "無紀錄"
            rows.append((bid, s.id, "none", None, None, None, None, lvl, "無裁罰紀錄，不出屬性分" if s.is_active else "已停辦", None))
        con.executemany("INSERT INTO app_scores(score_batch_id, preschool_id, method, prob_12m, risk_01, score, rank, level, reason, top_features) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("UPDATE app_score_batches SET is_current=0 WHERE is_current=1")
        con.execute("UPDATE app_score_batches SET is_current=1, status='ready' WHERE score_batch_id=?", (bid,))
        con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    levels = cur["level"].value_counts().to_dict()
    return {"score_batch_id": bid, "asof": asof, "method": method, "warning": warning, "scored": len(cur), "unscored": len(schools) - len(cur),
            "levels": levels, "base_rate": round(base, 3), "top_prob": round(float(cur["prob"].max()), 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args(argv)
    try:
        print(score_all(connect(a.db), a.asof))
    except PipelineError as e:
        print(e.format(), file=sys.stderr)
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())

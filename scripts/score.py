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
from features import EVENT_FEATURES, feature_frame, rule_reason, rule_score  # noqa: E402

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
    if active and active[2]:
        pkl = MODEL_DIR / f"model_{active[0]}.pkl"
        if not pkl.exists():
            raise PipelineError("active 模型檔不存在", str(pkl), "重新訓練或撤銷核准", stage="score")
        with open(pkl, "rb") as f:
            bundle = pickle.load(f)
        cur["prob"] = bundle["model"].predict_proba(cur[EVENT_FEATURES])[:, 1]
        method, model_id = f"model:{active[1]}", active[0]
    else:
        cur["prob"] = cur["prob_rule"]
    cur = cur.sort_values(["prob", "rule"], ascending=[False, False]).reset_index(drop=True)
    cur["rank"] = np.arange(1, len(cur) + 1)
    cur["risk_01"] = cur["prob"] / cur["prob"].max()
    cur["level"] = _levels(cur["prob"], settings)
    cur["score"] = (cur["risk_01"] * 100).round().astype(int)

    schools = pd.read_sql_query("SELECT id, is_active FROM src_preschools WHERE city=?", con, params=(city,))
    scored = set(cur["preschool_id"])
    now = date.today().isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        cur_b = con.execute("INSERT INTO app_score_batches(asof_date, model_id, method, created_at, status) VALUES (?,?,?,?,'building')",
                            (asof, model_id, method, now))
        bid = cur_b.lastrowid
        rows = []
        for r in cur.itertuples(index=False):
            top = {"n_events_36m": int(r.n_events_36m), "days_since_last": int(r.days_since_last),
                   "n_child_safety_36m": int(r.n_child_safety_36m), "has_stop_enroll": int(r.has_stop_enroll),
                   "rule_score": round(float(r.rule), 2), "prob_rule": round(float(r.prob_rule), 3)}
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
    return {"score_batch_id": bid, "asof": asof, "method": method, "scored": len(cur), "unscored": len(schools) - len(cur),
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

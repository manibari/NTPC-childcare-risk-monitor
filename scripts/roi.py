"""P2 ROI: replay a past year — visit capacity spent by rule ranking vs round-robin rotation.

For each quarter-end in the year, capacity = n_inspectors × visits_per_inspector_week × quarter_weeks.
Positives = ALL city schools with a new event 31–365 days after the quarter-end (first-time offenders count
against the rule strategy, which can only rank schools with history; spare capacity falls back to rotation).
Round-robin visits schools in fixed order, continuing where it left off (the status quo).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, connect  # noqa: E402
from features import LABEL_MAX_DAYS, LABEL_MIN_DAYS, feature_frame, is_quarter_end, rule_score  # noqa: E402


def replay(con, year: int, data_asof: str) -> dict:
    s = dict(con.execute("SELECT key, value FROM app_settings").fetchall())
    cap = int(s["n_inspectors"]) * int(s["visits_per_inspector_week"]) * int(s["quarter_weeks"])
    all_ids = sorted(r[0] for r in con.execute("SELECT id FROM src_preschools WHERE city='新北市'"))
    events = pd.read_sql_query(
        "SELECT e.preschool_id, e.date FROM src_penalty_events e JOIN src_preschools p ON p.id=e.preschool_id WHERE p.city='新北市'", con)
    events["date"] = pd.to_datetime(events["date"]).dt.date
    df = feature_frame(con, data_asof)
    df = df[df["asof"].map(lambda d: d.year == year and is_quarter_end(d))]
    df = df.assign(rs=rule_score(df))
    rr_ptr, rows = 0, []
    for q in sorted(df["asof"].unique()):
        if q + timedelta(days=LABEL_MAX_DAYS) > date.fromisoformat(data_asof):
            continue
        lo, hi = q + timedelta(days=LABEL_MIN_DAYS - 1), q + timedelta(days=LABEL_MAX_DAYS)
        pos_ids = set(events.loc[(events["date"] > lo) & (events["date"] <= hi), "preschool_id"])
        g = df[df["asof"] == q]
        ranked = g.sort_values("rs", ascending=False)["preschool_id"].tolist()[:cap]
        rotation = [all_ids[(rr_ptr + i) % len(all_ids)] for i in range(cap)]
        rr_ptr = (rr_ptr + cap) % len(all_ids)
        # rule strategy: ranked history schools first, then rotation among the rest for spare capacity
        spare = [pid for pid in rotation if pid not in set(ranked)][: max(cap - len(ranked), 0)]
        rule_set = set(ranked) | set(spare)
        rows.append({"asof": q, "positives": len(pos_ids), "pool": len(g), "rule_hits": len(rule_set & pos_ids),
                     "rotation_hits": len(set(rotation) & pos_ids),
                     "first_timers": len(pos_ids - set(g["preschool_id"]))})
    if not rows:
        return {"year": year, "capacity_per_quarter": cap, "quarters": 0}
    t = pd.DataFrame(rows)
    P = max(int(t["positives"].sum()), 1)
    return {"year": year, "capacity_per_quarter": cap, "quarters": len(t), "positives": int(t["positives"].sum()),
            "first_timers": int(t["first_timers"].sum()), "history_pool_mean": round(float(t["pool"].mean()), 1),
            "rule_hits": int(t["rule_hits"].sum()), "rotation_hits": int(t["rotation_hits"].sum()),
            "rule_coverage": round(float(t["rule_hits"].sum()) / P, 3),
            "rotation_coverage": round(float(t["rotation_hits"].sum()) / P, 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--years", default="2022,2023,2024")
    a = ap.parse_args(argv)
    con = connect(a.db, readonly=True)
    asof = con.execute("SELECT value FROM app_settings WHERE key='data_asof'").fetchone()[0]
    for y in (int(x) for x in a.years.split(",")):
        print(replay(con, y, asof))
    return 0


if __name__ == "__main__":
    sys.exit(main())

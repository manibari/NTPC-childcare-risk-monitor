"""P3b schedule: CP-SAT quarter plan — x[school, week, inspector] (架構定調 4).

Hard: capacity per (week, inspector) ≤ visits_per_inspector_week; each school ≤ 1 visit; season list
must-visit; pinned fixed; excluded & inactive removed. Soft: maximise Σ risk_01 × visited (+ earlier
weeks for higher risk), bonus for same-linker schools in the same week, penalty per distinct town an
inspector covers in a week (clustering). max_time 20 s, fixed seed, 8 workers. INFEASIBLE → PipelineError
(API maps it to 422).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from datetime import date

from ortools.sat.python import cp_model

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, connect  # noqa: E402
from errors import PipelineError  # noqa: E402

SEED = 7
CANDIDATE_TOP_N = 300
LINKED_WEIGHT = 0.30      # a 連坐 lead without its own score
TOWN_PENALTY = 0.05       # per distinct town per inspector-week
LINK_BONUS = 0.10         # per same-linker pair visited in the same week
EARLY_BONUS = 0.002       # × risk × (W − week)


def load_problem(con: sqlite3.Connection, city: str = "新北市") -> dict:
    s = dict(con.execute("SELECT key, value FROM app_settings").fetchall())
    batch = con.execute("SELECT score_batch_id FROM app_score_batches WHERE is_current=1").fetchone()
    if not batch:
        raise PipelineError("沒有目前的評分批次", "score.py 尚未執行", "先跑 python scripts/score.py", stage="schedule")
    bid = batch[0]
    rows = con.execute(
        """SELECT s.preschool_id, p.town, s.risk_01, s.rank, s.level
           FROM app_scores s JOIN src_preschools p ON p.id=s.preschool_id
           WHERE s.score_batch_id=? AND p.city=? AND p.is_active=1 AND s.rank IS NOT NULL AND s.rank<=? ORDER BY s.rank""",
        (bid, city, CANDIDATE_TOP_N)).fetchall()
    cand = {r[0]: {"town": r[1], "risk": float(r[2]), "rank": r[3], "level": r[4], "why": f"排名 {r[3]}（{r[4]}）"} for r in rows}
    for pid, town, reason in con.execute(
            "SELECT w.preschool_id, p.town, w.reason FROM app_watchlist w JOIN src_preschools p ON p.id=w.preschool_id "
            "WHERE w.is_current=1 AND w.tier='linked' AND p.is_active=1"):
        cand.setdefault(pid, {"town": town, "risk": LINKED_WEIGHT, "rank": None, "level": "同負責人", "why": reason})
    must = set()
    for pid, town in con.execute("SELECT s.preschool_id, p.town FROM app_season_list s JOIN src_preschools p ON p.id=s.preschool_id WHERE s.status<>'removed' AND p.is_active=1"):
        cand.setdefault(pid, {"town": town, "risk": 0.5, "rank": None, "level": "名單", "why": "本季名單"})
        must.add(pid)
    for pid, in con.execute("SELECT preschool_id FROM app_watchlist WHERE is_current=1 AND tier='penalized'"):
        if pid in cand:
            must.add(pid); cand[pid]["why"] = "近 12 月裁罰（必訪）；" + cand[pid]["why"]
    links = {}
    for pid, lid in con.execute("SELECT preschool_id, linker_id FROM app_preschool_linkers WHERE excluded_by_user=0 AND same_name_flag=0"):
        if pid in cand:
            links.setdefault(lid, []).append(pid)
    pairs = [(a, b) for grp in links.values() for i, a in enumerate(grp) for b in grp[i + 1:]]
    return {"score_batch_id": bid, "n_inspectors": int(s["n_inspectors"]), "visits_per_week": int(s["visits_per_inspector_week"]),
            "weeks": int(s["quarter_weeks"]), "candidates": cand, "must": must, "pairs": pairs}


PRESETS = {  # objective presets (Verdandi-OR style: the operator picks the goal, the solver picks the plan)
    "risk": {"town_penalty": TOWN_PENALTY, "link_bonus": LINK_BONUS, "early_bonus": EARLY_BONUS, "label": "風險優先"},
    "cluster": {"town_penalty": 0.20, "link_bonus": 0.25, "early_bonus": EARLY_BONUS, "label": "同區同負責人併訪"},
    "balanced": {"town_penalty": 0.02, "link_bonus": LINK_BONUS, "early_bonus": EARLY_BONUS, "label": "各區均衡"},
}


def solve(prob: dict, pinned: dict[str, tuple[int, int]] | None = None, excluded: set[str] | None = None,
          max_time: float = 20.0, town_min: dict[str, int] | None = None, town_max: dict[str, int] | None = None,
          objective: str = "risk") -> dict:
    pinned, excluded = pinned or {}, excluded or set()
    town_min, town_max = town_min or {}, town_max or {}
    wt = PRESETS.get(objective, PRESETS["risk"])
    I, V, W = prob["n_inspectors"], prob["visits_per_week"], prob["weeks"]
    cand = {k: v for k, v in prob["candidates"].items() if k not in excluded}
    must = {p for p in prob["must"] if p in cand}
    cap = I * V * W
    if cap <= 0:
        raise PipelineError("產能為 0，無法排程", f"{I} 人 × {V} 次/週 × {W} 週", "到設定頁調整人力", stage="schedule")
    if len(must) > cap:
        raise PipelineError("必訪園超過本季產能", f"必訪 {len(must)} > 產能 {cap}", "增加人力或縮減本季名單", stage="schedule")
    by_town: dict[str, list[str]] = {}
    for pid, c in cand.items():
        by_town.setdefault(c["town"], []).append(pid)
    if objective == "balanced":  # proportional floor: 80% of each town's fair share, capped by its candidates
        for tn, ps in by_town.items():
            town_min.setdefault(tn, min(len(ps), int(0.8 * cap * len(ps) / max(len(cand), 1))))
    for tn, n in town_min.items():
        if n > len(by_town.get(tn, [])):
            raise PipelineError("某區下限超過候選園數", f"{tn} 下限 {n} > 候選 {len(by_town.get(tn, []))}", "降低該區下限或把園加入本季名單", stage="schedule")
    if sum(town_min.values()) > cap:
        raise PipelineError("各區下限總和超過產能", f"{sum(town_min.values())} > {cap}", "降低下限或增加人力", stage="schedule")
    m = cp_model.CpModel()
    ids = list(cand)
    x = {(p, w, i): m.NewBoolVar(f"x_{n}_{w}_{i}") for n, p in enumerate(ids) for w in range(W) for i in range(I)}
    vis = {p: m.NewBoolVar(f"v_{n}") for n, p in enumerate(ids)}
    for p in ids:
        m.Add(sum(x[p, w, i] for w in range(W) for i in range(I)) == vis[p])
        if p in must:
            m.Add(vis[p] == 1)
    for w in range(W):
        for i in range(I):
            m.Add(sum(x[p, w, i] for p in ids) <= V)
    for p, (w, i) in pinned.items():
        if p in cand and 0 <= w < W and 0 <= i < I:
            m.Add(x[p, w, i] == 1)
    for tn, n in town_min.items():
        if tn in by_town:
            m.Add(sum(vis[p] for p in by_town[tn]) >= n)
    for tn, n in town_max.items():
        if tn in by_town:
            m.Add(sum(vis[p] for p in by_town[tn]) <= n)
    towns = sorted({c["town"] for c in cand.values()})
    t = {(tn, w, i): m.NewBoolVar(f"t_{tn}_{w}_{i}") for tn in towns for w in range(W) for i in range(I)}
    for p in ids:
        for w in range(W):
            for i in range(I):
                m.AddImplication(x[p, w, i], t[cand[p]["town"], w, i])
    week_of = {}
    for p in ids:
        week_of[p] = {w: m.NewBoolVar(f"wk_{p}_{w}") for w in range(W)}
        for w in range(W):
            m.Add(sum(x[p, w, i] for i in range(I)) == week_of[p][w])
    same = []
    for a, b in prob["pairs"]:
        if a in cand and b in cand:
            for w in range(W):
                y = m.NewBoolVar(f"y_{a}_{b}_{w}")
                m.AddImplication(y, week_of[a][w]); m.AddImplication(y, week_of[b][w])
                same.append(y)
    S = 1000
    obj = [int(S * cand[p]["risk"]) * vis[p] for p in ids]
    obj += [int(S * wt["early_bonus"] * cand[p]["risk"] * (W - wk)) * x[p, wk, i] for p in ids for wk in range(W) for i in range(I)]
    obj += [int(S * wt["link_bonus"]) * y for y in same]
    obj += [-int(S * wt["town_penalty"]) * tv for tv in t.values()]
    m.Maximize(sum(obj))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time
    solver.parameters.random_seed = SEED
    solver.parameters.num_workers = 8
    status = solver.Solve(m)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise PipelineError("排程不可行", f"CP-SAT 狀態 {name}", "檢查釘選衝突、必訪數與產能", stage="schedule")
    visits = []
    for p in ids:
        for w in range(W):
            for i in range(I):
                if solver.Value(x[p, w, i]):
                    visits.append({"preschool_id": p, "week_no": w + 1, "inspector_no": i + 1, "rank": cand[p]["rank"],
                                   "reason": cand[p]["why"], "pinned": int(p in pinned)})
    town_visits = {tn: sum(int(solver.Value(vis[p])) for p in ps) for tn, ps in by_town.items()}
    tot = sum(c["risk"] for c in cand.values()) or 1
    cov = sum(cand[v["preschool_id"]]["risk"] for v in visits) / tot
    by_level = {}
    for p in ids:
        lv = cand[p]["level"]; by_level.setdefault(lv, [0, 0]); by_level[lv][1] += 1
        if solver.Value(vis[p]): by_level[lv][0] += 1
    return {"status": name, "objective": solver.ObjectiveValue(), "visits": visits, "coverage_pct": round(cov * 100, 1),
            "capacity": cap, "candidates": len(cand), "must": len(must), "by_level": by_level,
            "wall_s": round(solver.WallTime(), 1), "approx": name == "FEASIBLE", "objective_preset": objective,
            "town_visits": town_visits, "town_candidates": {tn: len(ps) for tn, ps in by_town.items()}, "town_min": town_min}


def save(con: sqlite3.Connection, prob: dict, res: dict, params: dict) -> int:
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute("INSERT INTO app_schedules(score_batch_id, asof_date, params, solver_status, objective, coverage_pct, created_at, is_current) VALUES (?,?,?,?,?,?,?,0)",
                          (prob["score_batch_id"], date.today().isoformat(), json.dumps(params, ensure_ascii=False), res["status"], res["objective"], res["coverage_pct"], date.today().isoformat()))
        sid = cur.lastrowid
        con.executemany("INSERT INTO app_schedule_visits(schedule_id, preschool_id, week_no, inspector_no, rank, reason, pinned) VALUES (?,?,?,?,?,?,?)",
                        [(sid, v["preschool_id"], v["week_no"], v["inspector_no"], v["rank"], v["reason"], v["pinned"]) for v in res["visits"]])
        con.execute("UPDATE app_schedules SET is_current=0 WHERE is_current=1")
        con.execute("UPDATE app_schedules SET is_current=1, is_stale=0 WHERE schedule_id=?", (sid,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise
    return sid


def run(con: sqlite3.Connection, pinned=None, excluded=None, max_time: float = 20.0, town_min=None, town_max=None, objective: str = "risk") -> dict:
    prob = load_problem(con)
    res = solve(prob, pinned, excluded, max_time, town_min, town_max, objective)
    sid = save(con, prob, res, {"pinned": pinned or {}, "excluded": sorted(excluded or []), "max_time": max_time, "objective": objective,
                                "town_min": res["town_min"], "town_max": town_max or {}, "town_visits": res["town_visits"], "town_candidates": res["town_candidates"],
                                "n_inspectors": prob["n_inspectors"], "visits_per_week": prob["visits_per_week"], "weeks": prob["weeks"]})
    res["schedule_id"] = sid
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--max-time", type=float, default=20.0)
    a = ap.parse_args(argv)
    try:
        r = run(connect(a.db), max_time=a.max_time)
    except PipelineError as e:
        print(e.format(), file=sys.stderr); return e.exit_code
    print({k: v for k, v in r.items() if k != "visits"})
    return 0


if __name__ == "__main__":
    sys.exit(main())

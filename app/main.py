"""P4 FastAPI — /api/v1/*. Reads only v_* views; writes only to app_* tables. Serves web/ as the UI."""
from __future__ import annotations

import csv
import io
import json
import os
import pathlib
import sqlite3
import sys
import uuid
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from db import DEFAULT_DB  # noqa: E402
from errors import PipelineError  # noqa: E402
sys.path.insert(0, str(ROOT / "app"))
from agent import AgentService  # noqa: E402
from law_labels import describe, label, short_label  # noqa: E402

DB_PATH = pathlib.Path(os.environ.get("WATCHDOG_DB", DEFAULT_DB))
CITY = "新北市"
FIN_KEYS = ["人事費率", "每核定名額收入(千)", "餘絀率", "流動比", "負債比", "現金月數"]


def fnum(v):
    try:
        return None if v in (None, "", "nan") else float(v)
    except (TypeError, ValueError):
        return None

app = FastAPI(title="Smart Watchdog API", version="1.0")
agent = AgentService(DB_PATH)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, hint: str = "", retryable: bool = False, state: str | None = None):
        self.status, self.code, self.message, self.hint, self.retryable, self.state = status, code, message, hint, retryable, state


@app.exception_handler(ApiError)
async def _api_err(request: Request, e: ApiError):
    body = {"error": {"code": e.code, "message": e.message, "hint": e.hint, "retryable": e.retryable, "request_id": uuid.uuid4().hex[:12]}}
    if e.state:
        body["state"] = e.state
    return JSONResponse(status_code=e.status, content=body)


@app.exception_handler(PipelineError)
async def _pipe_err(request: Request, e: PipelineError):
    return JSONResponse(status_code=422, content={"error": {"code": "PIPELINE", "message": e.problem, "hint": e.fix, "detail": e.cause, "retryable": False, "request_id": uuid.uuid4().hex[:12]}})


def ro() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise ApiError(409, "DB_MISSING", "資料庫不存在", "執行 make bootstrap", state="no_db")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rw() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def settings(con) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM v_settings")}


def require_scores(con):
    if not one(con, "SELECT 1 FROM v_scores LIMIT 1"):
        raise ApiError(409, "NO_SCORES", "尚未評分", "執行 python scripts/score.py 或 make bootstrap", state="no_scores")


def stale(con, s) -> bool:
    asof = s.get("data_asof")
    return bool(asof) and (date.today() - date.fromisoformat(asof)).days > int(s.get("stale_days", 90))


# ----------------------------------------------------------------------------- overview
@app.get("/api/v1/overview")
def overview():
    con = ro(); require_scores(con); s = settings(con)
    asof = s["data_asof"]; d12 = (date.fromisoformat(asof).replace(year=date.fromisoformat(asof).year - 1)).isoformat()
    d36 = (date.fromisoformat(asof).replace(year=date.fromisoformat(asof).year - 3)).isoformat()
    lv = {r["level"]: r["n"] for r in rows(con, "SELECT level, COUNT(*) n FROM v_scores GROUP BY level")}
    watch = {r["tier"]: r["n"] for r in rows(con, "SELECT tier, COUNT(*) n FROM v_watchlist GROUP BY tier")}
    pen = one(con, "SELECT COUNT(DISTINCT e.preschool_id) n_pen, SUM(CASE WHEN e.date>? THEN 1 ELSE 0 END) ev12, COUNT(DISTINCT CASE WHEN e.date>? THEN e.preschool_id END) s12 FROM v_penalty_events e JOIN v_preschools p ON p.id=e.preschool_id WHERE p.city=?", (d12, d12, CITY))
    rep = one(con, "SELECT COUNT(*) n FROM (SELECT preschool_id FROM v_penalty_events e JOIN v_preschools p ON p.id=e.preschool_id WHERE p.city=? GROUP BY preschool_id HAVING COUNT(*)>=2)", (CITY,))["n"]
    towns = rows(con, """SELECT p.town, COUNT(*) schools, SUM(s.level='高') high, SUM(s.level='中') mid,
                         (SELECT COUNT(*) FROM v_penalty_events e JOIN v_preschools q ON q.id=e.preschool_id WHERE q.town=p.town AND q.city=? AND e.date>?) events_36m
                         FROM v_preschools p JOIN v_scores s ON s.preschool_id=p.id WHERE p.city=? GROUP BY p.town ORDER BY high DESC, events_36m DESC""", (CITY, d36, CITY))
    points = rows(con, "SELECT p.id, p.title, p.town, p.type, p.lng, p.lat, s.level, s.rank FROM v_preschools p JOIN v_scores s ON s.preschool_id=p.id WHERE p.city=? AND p.lng>0", (CITY,))
    monthly = rows(con, "SELECT substr(e.date,1,7) m, COUNT(*) n FROM v_penalty_events e JOIN v_preschools p ON p.id=e.preschool_id WHERE p.city=? AND e.date>? GROUP BY m ORDER BY m", (CITY, d36))
    heat = rows(con, """SELECT p.town, substr(e.articles, 1, instr(e.articles||'第','條')) art, COUNT(*) n FROM v_penalty_events e JOIN v_preschools p ON p.id=e.preschool_id
                        WHERE p.city=? AND e.date>? GROUP BY p.town, art""", (CITY, d36))
    for h in heat:
        h["label"] = label(h["art"]) if h["art"] else "未載明"
        h["short"] = short_label(h["art"])
    types = rows(con, "SELECT type, COUNT(*) n FROM v_preschools WHERE city=? GROUP BY type", (CITY,))
    model = one(con, "SELECT model_id, algo, auc, beats_baseline FROM v_models WHERE status='active'")
    return {"data_asof": asof, "stale": stale(con, s), "n_schools": sum(t["n"] for t in types), "types": types, "levels": lv, "watchlist": watch,
            "n_penalized": pen["n_pen"], "n_repeat": rep, "ev_12m": pen["ev12"], "schools_12m": pen["s12"], "by_town": towns, "points": points,
            "monthly": monthly, "heat": heat, "method": one(con, "SELECT batch_method FROM v_scores LIMIT 1")["batch_method"], "model": model}


# ----------------------------------------------------------------------------- rankings
@app.get("/api/v1/rankings")
def rankings(town: str | None = None, type: str | None = None, level: str | None = None, q: str | None = None,
             linked: str | None = None, top_n: int | None = None, page: int = 1, size: int = 25):
    con = ro(); require_scores(con); s = settings(con)
    if type and type not in ("私立", "公立", "非營利"):
        raise ApiError(400, "BAD_FILTER", f"未知立案別 {type}")
    where, args = ["p.city=?"], [CITY]
    if town: where.append("p.town=?"); args.append(town)
    if type: where.append("p.type=?"); args.append(type)
    if level:
        lv = level.split(",")
        if not set(lv) <= {"高", "中", "低", "無紀錄", "停辦"}: raise ApiError(400, "BAD_FILTER", f"未知等級 {level}")
        where.append(f"s.level IN ({','.join('?'*len(lv))})"); args += lv
    if q: where.append("p.title LIKE ?"); args.append(f"%{q}%")
    if top_n: where.append("s.rank<=?"); args.append(top_n)
    link_sub = "(SELECT code FROM v_preschool_linkers l WHERE l.preschool_id=s.preschool_id AND l.kind='owner' AND l.n_schools>1 AND l.excluded_by_user=0 LIMIT 1)"
    name_sub = "(SELECT l.name FROM v_preschool_linkers l WHERE l.preschool_id=s.preschool_id AND l.kind='owner' AND l.n_schools>1 AND l.excluded_by_user=0 LIMIT 1)"
    if linked == "yes": where.append(f"{link_sub} IS NOT NULL")
    if linked == "no": where.append(f"{link_sub} IS NULL")
    w = " AND ".join(where)
    total = one(con, f"SELECT COUNT(*) n FROM v_scores s JOIN v_preschools p ON p.id=s.preschool_id WHERE {w}", args)["n"]
    items = rows(con, f"""SELECT s.preschool_id, p.title, p.town, p.type, p.count_approved, s.rank, s.score, s.prob_12m, s.risk_01, s.level, s.method, s.reason, s.top_features,
                          (SELECT COUNT(*) FROM v_penalty_events e WHERE e.preschool_id=s.preschool_id) n_events,
                          (SELECT MAX(date) FROM v_penalty_events e WHERE e.preschool_id=s.preschool_id) last_event,
                          {link_sub} linker_code, {name_sub} linker_name,
                          EXISTS(SELECT 1 FROM v_season_list z WHERE z.preschool_id=s.preschool_id AND z.status<>'removed') in_season_list,
                          (SELECT tier FROM v_watchlist w WHERE w.preschool_id=s.preschool_id LIMIT 1) watch_tier,
                          m.n_items news_n, m.n_negative news_neg, m.n_12m news_12m, m.rating, m.n_ratings
                          FROM v_scores s JOIN v_preschools p ON p.id=s.preschool_id LEFT JOIN v_sentiment m ON m.preschool_id=s.preschool_id WHERE {w}
                          ORDER BY s.rank IS NULL, s.rank, s.level='停辦', p.title LIMIT ? OFFSET ?""", args + [size, (page - 1) * size])
    for it in items:
        it["top_features"] = json.loads(it["top_features"]) if it["top_features"] else None
    levels = {r["level"]: r["n"] for r in rows(con, "SELECT level, COUNT(*) n FROM v_scores GROUP BY level")}
    gaps = rows(con, """WITH g AS (SELECT preschool_id, julianday(date) - julianday(LAG(date) OVER (PARTITION BY preschool_id ORDER BY date)) d FROM v_penalty_events e WHERE preschool_id IN (SELECT id FROM v_preschools WHERE city=?))
                        SELECT CASE WHEN d<90 THEN '<3月' WHEN d<180 THEN '3–6月' WHEN d<365 THEN '6–12月' WHEN d<730 THEN '1–2年' WHEN d<1095 THEN '2–3年' ELSE '>3年' END b, COUNT(*) n FROM g WHERE d IS NOT NULL GROUP BY b""", (CITY,))
    n_season = one(con, "SELECT COUNT(*) n FROM v_watchlist")["n"] + one(con, "SELECT COUNT(*) n FROM v_season_list WHERE status<>'removed'")["n"]
    return {"data_asof": s["data_asof"], "stale": stale(con, s), "total": total, "page": page, "size": size, "items": items, "levels": levels, "gaps": gaps,
            "n_season": n_season, "thresholds": {"high": float(s["high_threshold"]), "mid": float(s["mid_threshold"]), "top_n": int(s["top_n_default"])}}


# ----------------------------------------------------------------------------- preschool detail
@app.get("/api/v1/preschools/{pid}")
def preschool(pid: str):
    con = ro()
    p = one(con, "SELECT * FROM v_preschools WHERE id=?", (pid,))
    if not p: raise ApiError(404, "NOT_FOUND", "查無此園")
    sc = one(con, "SELECT * FROM v_scores WHERE preschool_id=?", (pid,))
    if sc and sc.get("top_features"): sc["top_features"] = json.loads(sc["top_features"])
    pens = rows(con, "SELECT penalty_id, date, law, law_article, punishment, actor_role, is_child_safety, event_id FROM v_penalties WHERE preschool_id=? ORDER BY date DESC", (pid,))
    for x in pens:
        x["violation"] = describe(x["law_article"], x["law"])
    events = rows(con, "SELECT * FROM v_penalty_events WHERE preschool_id=? ORDER BY date DESC", (pid,))
    for e in events:
        e["violations"] = [{"violation": x["violation"], "punishment": x["punishment"], "is_child_safety": x["is_child_safety"]} for x in pens if x["event_id"] == e["event_id"]]
    links = rows(con, "SELECT * FROM v_preschool_linkers WHERE preschool_id=?", (pid,))
    watch = rows(con, "SELECT w.*, q.title source_title FROM v_watchlist w LEFT JOIN v_preschools q ON q.id=w.source_preschool_id WHERE w.preschool_id=?", (pid,))
    fin = one(con, "SELECT fiscal_year, payload FROM v_ratios WHERE preschool_id=? ORDER BY fiscal_year DESC LIMIT 1", (pid,))
    finance = None
    if fin:
        pl = json.loads(fin["payload"]); finance = {"fiscal_year": fin["fiscal_year"], "ratios": {k: fnum(pl.get(k)) for k in FIN_KEYS}}
        finance["history"] = [{"fiscal_year": r["fiscal_year"], **{k: fnum(json.loads(r["payload"]).get(k)) for k in FIN_KEYS[:2]}} for r in rows(con, "SELECT fiscal_year, payload FROM v_ratios WHERE preschool_id=? ORDER BY fiscal_year", (pid,))]
    visit = one(con, "SELECT week_no, inspector_no, pinned, reason FROM v_schedule_visits WHERE preschool_id=?", (pid,))
    season = bool(one(con, "SELECT 1 FROM v_season_list WHERE preschool_id=? AND status<>'removed'", (pid,)))
    gap = None
    if len(events) >= 2:
        gap = (date.fromisoformat(events[0]["date"]) - date.fromisoformat(events[1]["date"])).days
    return {"preschool": p, "score": sc, "penalties": pens, "events": events, "linkers": links, "watch": watch, "finance": finance,
            "visit": visit, "in_season_list": season, "last_gap_days": gap}


@app.get("/api/v1/preschools/{pid}/sentiment")
def preschool_sentiment(pid: str, refresh: int = 0):
    from sentiment import get_or_refresh
    con = rw()
    p = one(con, "SELECT title, town FROM v_preschools WHERE id=?", (pid,))
    if not p: raise ApiError(404, "NOT_FOUND", "查無此園")
    return get_or_refresh(con, pid, p["title"], p["town"], refresh=bool(refresh))


# ----------------------------------------------------------------------------- linkers
@app.get("/api/v1/linkers/{code}/graph")
def linker_graph(code: str):
    con = ro()
    l = one(con, "SELECT * FROM v_linkers WHERE code=?", (code,))
    if not l: raise ApiError(404, "NOT_FOUND", "查無此代碼")
    nodes = rows(con, """SELECT pl.preschool_id, p.title, p.town, p.type, pl.same_name_flag, pl.excluded_by_user, s.level, s.rank,
                         (SELECT COUNT(*) FROM v_penalty_events e WHERE e.preschool_id=pl.preschool_id) n_events,
                         (SELECT MAX(date) FROM v_penalty_events e WHERE e.preschool_id=pl.preschool_id) last_event,
                         (SELECT tier FROM v_watchlist w WHERE w.preschool_id=pl.preschool_id LIMIT 1) watch_tier
                         FROM v_preschool_linkers pl JOIN v_preschools p ON p.id=pl.preschool_id LEFT JOIN v_scores s ON s.preschool_id=pl.preschool_id WHERE pl.code=?""", (code,))
    return {"linker": l, "nodes": nodes, "window_months": int(settings(con)["watch_window_months"])}


class Excl(BaseModel):
    excluded: bool


@app.put("/api/v1/linkers/{code}/schools/{pid}")
def linker_exclude(code: str, pid: str, body: Excl):
    con = rw()
    r = one(con, "SELECT pl.linker_id, pl.same_name_flag FROM app_preschool_linkers pl JOIN app_linkers l ON l.linker_id=pl.linker_id WHERE l.code=? AND pl.preschool_id=?", (code, pid))
    if not r: raise ApiError(404, "NOT_FOUND", "查無此關聯")
    con.execute("UPDATE app_preschool_linkers SET excluded_by_user=? WHERE linker_id=? AND preschool_id=?", (int(body.excluded), r["linker_id"], pid))
    from linker import refresh_watchlist
    refresh_watchlist(con)
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    return {"ok": True, "watchlist_recomputed": True}


# ----------------------------------------------------------------------------- schedule
@app.get("/api/v1/schedule")
def schedule_get():
    con = ro(); s = settings(con)
    sch = one(con, "SELECT * FROM v_schedule")
    cap = int(s["n_inspectors"]) * int(s["visits_per_inspector_week"]) * int(s["quarter_weeks"])
    if not sch:
        raise ApiError(409, "NO_SCHEDULE", "尚未排程", "按「重新求解」或執行 python scripts/schedule.py", state="no_schedule")
    sch["params"] = json.loads(sch["params"])
    visits = rows(con, "SELECT v.*, p.title, p.town, s.level FROM v_schedule_visits v JOIN v_preschools p ON p.id=v.preschool_id LEFT JOIN v_scores s ON s.preschool_id=v.preschool_id ORDER BY week_no, inspector_no, rank", ())
    by_level = {r["level"]: r["n"] for r in rows(con, "SELECT COALESCE(s.level,'同負責人') level, COUNT(*) n FROM v_schedule_visits v LEFT JOIN v_scores s ON s.preschool_id=v.preschool_id GROUP BY 1")}
    totals = {r["level"]: r["n"] for r in rows(con, "SELECT level, COUNT(*) n FROM v_scores WHERE rank IS NOT NULL GROUP BY level")}
    by_town = rows(con, "SELECT p.town, COUNT(*) n FROM v_schedule_visits v JOIN v_preschools p ON p.id=v.preschool_id GROUP BY p.town ORDER BY n DESC")
    cand_by_town = {r["town"]: r["n"] for r in rows(con, "SELECT p.town, COUNT(*) n FROM v_scores s JOIN v_preschools p ON p.id=s.preschool_id WHERE s.rank IS NOT NULL AND s.rank<=300 AND p.is_active=1 GROUP BY p.town")}
    for t in by_town:
        t["candidates"] = cand_by_town.get(t["town"], 0)
    for tn, n in cand_by_town.items():
        if not any(t["town"] == tn for t in by_town):
            by_town.append({"town": tn, "n": 0, "candidates": n})
    from schedule import PRESETS
    return {"schedule": sch, "capacity": cap, "settings": {k: int(s[k]) for k in ("n_inspectors", "visits_per_inspector_week", "quarter_weeks")},
            "visits": visits, "by_level": by_level, "level_totals": totals, "by_town": by_town, "presets": {k: v["label"] for k, v in PRESETS.items()}}


class SolveReq(BaseModel):
    pinned: dict[str, list[int]] = Field(default_factory=dict)   # pid -> [week_no, inspector_no] (1-based)
    excluded: list[str] = Field(default_factory=list)
    max_time: float = 20.0
    town_min: dict[str, int] = Field(default_factory=dict)
    town_max: dict[str, int] = Field(default_factory=dict)
    objective: str = "risk"


@app.post("/api/v1/schedule/solve")
def schedule_solve(body: SolveReq):
    from schedule import run
    con = rw()
    if not one(con, "SELECT 1 FROM v_scores LIMIT 1"):
        raise ApiError(409, "NO_SCORES", "尚未評分", state="no_scores")
    pinned = {k: (v[0] - 1, v[1] - 1) for k, v in body.pinned.items()}
    if body.objective not in ("risk", "cluster", "balanced"): raise ApiError(400, "BAD_VALUE", "objective 需為 risk / cluster / balanced")
    res = run(con, pinned=pinned, excluded=set(body.excluded), max_time=min(body.max_time, 60),
              town_min={k: int(v) for k, v in body.town_min.items() if int(v) > 0}, town_max={k: int(v) for k, v in body.town_max.items() if int(v) > 0}, objective=body.objective)
    return {k: v for k, v in res.items() if k != "visits"} | {"n_visits": len(res["visits"])}


@app.get("/api/v1/schedule/capacity-curve")
def capacity_curve():
    """Coverage of 高+中 and of the season list as inspectors go 1..6 (cheap: greedy by rank, no solver)."""
    con = ro(); s = settings(con)
    per = int(s["visits_per_inspector_week"]) * int(s["quarter_weeks"])
    ranked = rows(con, "SELECT preschool_id, level FROM v_scores WHERE rank IS NOT NULL ORDER BY rank")
    must = {r["preschool_id"] for r in rows(con, "SELECT preschool_id FROM v_watchlist WHERE tier='penalized'")} | {r["preschool_id"] for r in rows(con, "SELECT preschool_id FROM v_season_list WHERE status<>'removed'")}
    hm = [r["preschool_id"] for r in ranked if r["level"] in ("高", "中")]
    out = []
    for n in range(1, 7):
        cap = n * per
        chosen = list(must)[:cap]
        rest = [r["preschool_id"] for r in ranked if r["preschool_id"] not in must][: max(cap - len(chosen), 0)]
        got = set(chosen) | set(rest)
        allc = {r["preschool_id"] for r in ranked} | must
        out.append({"inspectors": n, "capacity": cap, "must_cov": round(len(set(chosen)) / max(len(must), 1), 3), "hm_cov": round(len(got & set(hm)) / max(len(hm), 1), 3),
                    "cand_cov": round(len(got & allc) / max(len(allc), 1), 3)})
    return {"curve": out, "n_must": len(must), "n_high_mid": len(hm), "n_candidates": len({r["preschool_id"] for r in ranked} | must)}


# ----------------------------------------------------------------------------- season list / watchlist
@app.get("/api/v1/season-list")
def season_list():
    con = ro()
    base = """SELECT w.preschool_id, p.title, p.town, p.type, w.tier, w.reason, w.source_preschool_id, q.title source_title, w.linker_id, l.code linker_code, l.name linker_name,
              s.level, s.rank, s.score, s.prob_12m, s.top_features, v.week_no, v.inspector_no,
              (SELECT same_name_flag FROM v_preschool_linkers x WHERE x.preschool_id=w.preschool_id AND x.linker_id=w.linker_id) same_name_flag
              FROM v_watchlist w JOIN v_preschools p ON p.id=w.preschool_id LEFT JOIN v_preschools q ON q.id=w.source_preschool_id
              LEFT JOIN v_linkers l ON l.linker_id=w.linker_id LEFT JOIN v_scores s ON s.preschool_id=w.preschool_id LEFT JOIN v_schedule_visits v ON v.preschool_id=w.preschool_id
              WHERE w.tier=? ORDER BY s.rank"""
    manual = rows(con, "SELECT z.*, p.title, p.town, s.level, s.rank, s.score, s.prob_12m FROM v_season_list z JOIN v_preschools p ON p.id=z.preschool_id LEFT JOIN v_scores s ON s.preschool_id=z.preschool_id WHERE z.status<>'removed'")
    d36 = (date.fromisoformat(settings(con)["data_asof"]).replace(year=date.fromisoformat(settings(con)["data_asof"]).year - 3)).isoformat()
    def enrich(lst):
        for r in lst:
            pen = rows(con, "SELECT law_article, law, punishment, date, is_child_safety FROM v_penalties WHERE preschool_id=? AND date>? ORDER BY date DESC", (r["preschool_id"], d36))
            counts: dict[str, int] = {}
            for x in pen:
                k = short_label(x["law_article"]) if x["law_article"] else "未載明"
                counts[k] = counts.get(k, 0) + 1
            r["causes"] = sorted(({"label": k, "n": n} for k, n in counts.items()), key=lambda c: -c["n"])[:4]
            r["last_violation"] = describe(pen[0]["law_article"], pen[0]["law"]) if pen else None
            tf = json.loads(r["top_features"]) if r.get("top_features") else {}
            focus = []
            if any(x["is_child_safety"] for x in pen): focus.append("到班觀察教保人員與幼兒互動、調閱監視影像與通報紀錄")
            if any(c["label"] in ("超收/設施", "師生比", "人員配置") for c in r["causes"]): focus.append("核對到園幼兒數、班級師生比與人員名冊")
            if any(c["label"] in ("收費", "契約退費") for c in r["causes"]): focus.append("抽查收費明細與備查數額、退費紀錄")
            if any(c["label"] in ("人員資格", "人員備查") for c in r["causes"]): focus.append("核對教保人員資格證書與異動備查")
            if any(c["label"] == "幼童車" for c in r["causes"]): focus.append("檢查幼童專用車核准、載運人數與隨車人員")
            if tf.get("has_stop_enroll"): focus.append("確認停止招生期間是否仍有收托")
            if r.get("tier") == "linked": focus.insert(0, "同負責人園所曾裁罰：比對人員調動與管理方式是否相同")
            r["focus"] = focus[:3] or ["依最近一次違規類型複查改善情形"]
        return lst
    penalized = enrich(rows(con, base, ("penalized",))); linked = enrich(rows(con, base, ("linked",))); manual = enrich(manual)
    curve = rows(con, """WITH e AS (SELECT preschool_id, date, LEAD(date) OVER (PARTITION BY preschool_id ORDER BY date) nxt FROM v_penalty_events WHERE preschool_id IN (SELECT id FROM v_preschools WHERE city=?))
                         SELECT m, ROUND(1.0*SUM(CASE WHEN nxt IS NOT NULL AND julianday(nxt)-julianday(date)<=m*30.44 THEN 1 ELSE 0 END)/COUNT(*),3) rate
                         FROM e, (SELECT 3 m UNION SELECT 6 UNION SELECT 9 UNION SELECT 12 UNION SELECT 18 UNION SELECT 24 UNION SELECT 36) WHERE julianday(?)-julianday(date) > m*30.44 GROUP BY m ORDER BY m""", (CITY, settings(con)["data_asof"]))
    return {"penalized": penalized, "linked": linked, "manual": manual, "recidivism_curve": curve,
            "window_months": int(settings(con)["watch_window_months"])}


class SeasonReq(BaseModel):
    preschool_id: str
    note: str | None = None


@app.post("/api/v1/season-list")
def season_add(body: SeasonReq):
    con = rw()
    if not one(con, "SELECT 1 FROM v_preschools WHERE id=?", (body.preschool_id,)): raise ApiError(404, "NOT_FOUND", "查無此園")
    con.execute("INSERT INTO app_season_list(preschool_id, added_at, note, status) VALUES (?,?,?,'draft') ON CONFLICT(preschool_id) DO UPDATE SET status='draft', note=excluded.note", (body.preschool_id, date.today().isoformat(), body.note))
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    return {"ok": True, "n_season": one(con, "SELECT COUNT(*) n FROM app_season_list WHERE status<>'removed'")["n"]}


@app.delete("/api/v1/season-list/{pid}")
def season_remove(pid: str):
    con = rw()
    con.execute("UPDATE app_season_list SET status='removed' WHERE preschool_id=?", (pid,))
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    return {"ok": True, "n_season": one(con, "SELECT COUNT(*) n FROM app_season_list WHERE status<>'removed'")["n"]}


# ----------------------------------------------------------------------------- finance
@app.get("/api/v1/finance")
def finance():
    con = ro()
    latest = rows(con, "SELECT r.preschool_id, r.title, r.fiscal_year, r.payload, (SELECT COUNT(*) FROM v_penalty_events e WHERE e.preschool_id=r.preschool_id) n_events FROM v_ratios r WHERE r.fiscal_year=(SELECT MAX(fiscal_year) FROM v_ratios x WHERE x.preschool_id=r.preschool_id)")
    items = []
    for r in latest:
        pl = json.loads(r["payload"])
        items.append({"preschool_id": r["preschool_id"], "title": r["title"], "fiscal_year": r["fiscal_year"], "n_events": r["n_events"], **{k: fnum(pl.get(k)) for k in FIN_KEYS}, "internal_control": None})
    trend = rows(con, "SELECT fiscal_year, payload FROM v_ratios")
    by_year: dict[int, list] = {}
    for r in trend:
        by_year.setdefault(r["fiscal_year"], []).append(json.loads(r["payload"]))
    def med(xs):
        xs = sorted(x for x in (fnum(v) for v in xs) if x is not None); return xs[len(xs) // 2] if xs else None
    trend_out = [{"fiscal_year": y, "人事費率": med([p.get("人事費率") for p in ps]), "每核定名額收入(千)": med([p.get("每核定名額收入(千)") for p in ps])} for y, ps in sorted(by_year.items())]
    pen = [i for i in items if i["n_events"]]; non = [i for i in items if not i["n_events"]]
    cmp = {k: {"penalized": med([i[k] for i in pen]), "clean": med([i[k] for i in non])} for k in FIN_KEYS[:2]}
    return {"items": items, "n_schools": len(items), "n_school_years": len(trend), "trend": trend_out, "compare": cmp,
            "lamps": {"人事費率": {"red": 0.70, "yellow": 0.65}, "每核定名額收入(千)": {"red": 100, "yellow": 120}}}


# ----------------------------------------------------------------------------- backtest
@app.get("/api/v1/backtest")
def backtest():
    con = ro()
    models = rows(con, "SELECT * FROM v_models ORDER BY model_id")
    if not models: raise ApiError(409, "NO_MODEL", "尚未訓練模型", "python scripts/train.py", state="no_model")
    ref = max(models, key=lambda m: (m["algo"] == "gbdt", m["model_id"]))
    bt = rows(con, "SELECT * FROM v_backtests WHERE model_id=? ORDER BY obs_year, baseline", (ref["model_id"],))
    return {"models": models, "reference_model_id": ref["model_id"], "by_year": bt, "active": one(con, "SELECT * FROM v_models WHERE status='active'")}


# ----------------------------------------------------------------------------- models (train / approve / retire)
import threading
TRAIN_STATE: dict = {"running": False, "algo": None, "started_at": None, "result": None, "error": None}


def _train_worker(algo: str):
    from train import train_and_record
    con = rw()
    try:
        asof = settings(con)["data_asof"]
        mid = train_and_record(con, algo, asof)
        row = one(con, "SELECT model_id, algo, auc, top100_cov, beats_baseline FROM v_models WHERE model_id=?", (mid,))
        TRAIN_STATE.update(result=row, error=None)
    except Exception as e:
        TRAIN_STATE.update(error=str(e)[:300], result=None)
    finally:
        TRAIN_STATE["running"] = False


@app.get("/api/v1/models")
def models_list():
    con = ro()
    models = rows(con, "SELECT * FROM v_models ORDER BY model_id DESC")
    for m in models:
        m["params"] = json.loads(m["params"]) if m["params"] else {}
        m["backtests"] = rows(con, "SELECT obs_year, baseline, n_obs, n_pos, auc, top100, lead_days_median FROM v_backtests WHERE model_id=? ORDER BY obs_year, baseline", (m["model_id"],))
    events = rows(con, "SELECT e.*, m.algo FROM app_model_events e JOIN v_models m ON m.model_id=e.model_id ORDER BY e.event_id DESC LIMIT 30") if False else rows(con, "SELECT * FROM v_model_events ORDER BY event_id DESC LIMIT 30")
    from features import EVENT_FEATURES, feature_hash
    from score import FEATURE_ZH
    from train import BEATS_MARGIN, GAP_DAYS, MIN_POS, SEED
    return {"models": models, "events": events, "train": TRAIN_STATE, "active": one(con, "SELECT model_id, algo FROM v_models WHERE status='active'"),
            "features": [{"key": f, "zh": FEATURE_ZH.get(f, f)} for f in EVENT_FEATURES], "feature_hash": feature_hash(),
            "gate": {"margin": BEATS_MARGIN, "gap_days": GAP_DAYS, "min_pos": MIN_POS, "seed": SEED},
            "current_method": (one(con, "SELECT batch_method FROM v_scores LIMIT 1") or {}).get("batch_method")}


class TrainReq(BaseModel):
    algo: str = "logreg"


@app.post("/api/v1/models/train")
def models_train(body: TrainReq):
    if body.algo not in ("gbdt", "logreg"): raise ApiError(400, "BAD_VALUE", "algo 需為 gbdt 或 logreg")
    if TRAIN_STATE["running"]: raise ApiError(409, "TRAIN_RUNNING", "已有訓練在進行", state="training")
    TRAIN_STATE.update(running=True, algo=body.algo, started_at=datetime.now().isoformat(timespec="seconds"), result=None, error=None)
    threading.Thread(target=_train_worker, args=(body.algo,), daemon=True).start()
    return {"ok": True, "state": "training"}


class ApproveReq(BaseModel):
    actor: str = "demo"
    force: bool = False


@app.post("/api/v1/models/{model_id}/approve")
def models_approve(model_id: int, body: ApproveReq):
    from approve import approve as do_approve
    from score import score_all
    con = rw()
    do_approve(con, model_id, body.actor, body.force)
    r = score_all(con)
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    return {"ok": True, "active": model_id, "rescored": r["levels"], "method": r["method"]}


@app.post("/api/v1/models/{model_id}/retire")
def models_retire(model_id: int, body: ApproveReq):
    from approve import retire
    from score import score_all
    con = rw()
    if not one(con, "SELECT 1 FROM v_models WHERE model_id=? AND status='active'", (model_id,)): raise ApiError(409, "NOT_ACTIVE", "此版本不是 active", state="not_active")
    retire(con, model_id, body.actor, "manual")
    r = score_all(con)
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    return {"ok": True, "rescored": r["levels"], "method": r["method"]}


# ----------------------------------------------------------------------------- data quality / settings / feedback
@app.get("/api/v1/data-quality")
def data_quality():
    con = ro(); s = settings(con)
    counts = {t: one(con, f"SELECT COUNT(*) n FROM {t}")["n"] for t in ("v_preschools", "v_penalties", "v_penalty_events", "v_ratios", "v_linkers", "v_scores", "v_watchlist")}
    ntpc = one(con, "SELECT COUNT(*) n, SUM(lng>0 AND lat>0) coords, SUM(is_active=0) inactive FROM v_preschools WHERE city=?", (CITY,))
    ev = one(con, "SELECT COUNT(*) events, (SELECT COUNT(*) FROM v_penalties x JOIN v_preschools q ON q.id=x.preschool_id WHERE q.city=?) rows_ FROM v_penalty_events e JOIN v_preschools p ON p.id=e.preschool_id WHERE p.city=?", (CITY, CITY))
    link = one(con, "SELECT COUNT(*) n, SUM(n_schools>1) multi, SUM(kind='operator') ops FROM v_linkers")
    flags = one(con, "SELECT SUM(same_name_flag) same_name, SUM(excluded_by_user) excluded FROM v_preschool_linkers")
    no_owner = one(con, "SELECT COUNT(*) n FROM v_preschools p WHERE p.city=? AND p.type<>'公立' AND NOT EXISTS (SELECT 1 FROM v_preschool_linkers l WHERE l.preschool_id=p.id AND l.kind='owner')", (CITY,))["n"]
    runs = rows(con, "SELECT * FROM v_pipeline_runs ORDER BY started_at DESC LIMIT 30")
    return {"data_asof": s["data_asof"], "stale": stale(con, s), "counts": counts, "ntpc": ntpc, "events": ev, "linkers": link, "flags": flags, "no_owner": no_owner, "runs": runs}


@app.get("/api/v1/settings")
def settings_get():
    con = ro(); s = settings(con)
    return {"settings": s, "agent_enabled": agent.enabled, "active_model": one(con, "SELECT model_id, algo FROM v_models WHERE status='active'")}


ALLOWED = {"n_inspectors": (int, 0, 50), "visits_per_inspector_week": (int, 0, 100), "quarter_weeks": (int, 1, 26), "high_threshold": (float, 0, 1), "mid_threshold": (float, 0, 1),
           "top_n_default": (int, 1, 2000), "watch_window_months": (int, 1, 60), "stale_days": (int, 1, 3650), "anonymize_titles": (int, 0, 1)}


@app.put("/api/v1/settings")
def settings_put(body: dict):
    con = rw(); cur = settings(con)
    new = dict(cur)
    for k, v in body.items():
        if k not in ALLOWED: raise ApiError(400, "BAD_VALUE", f"不可設定 {k}")
        typ, lo, hi = ALLOWED[k]
        try: val = typ(v)
        except (TypeError, ValueError): raise ApiError(400, "BAD_VALUE", f"{k} 格式錯誤")
        if not lo <= val <= hi: raise ApiError(400, "BAD_VALUE", f"{k} 需在 {lo}–{hi}")
        new[k] = str(val)
    if float(new["high_threshold"]) <= float(new["mid_threshold"]): raise ApiError(400, "BAD_VALUE", "高門檻需大於中門檻")
    con.execute("BEGIN IMMEDIATE")
    for k in ALLOWED:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES (?,?)", (k, new[k]))
    con.execute("UPDATE app_schedules SET is_stale=1 WHERE is_current=1")
    con.execute("COMMIT")
    rescored = None
    if any(k in body for k in ("high_threshold", "mid_threshold")):
        from score import score_all
        rescored = score_all(con)["levels"]
    return {"settings": settings(con), "rescored": rescored}


class FeedbackReq(BaseModel):
    page: str
    preschool_id: str | None = None
    text: str = Field(min_length=1, max_length=2000)


@app.post("/api/v1/feedback")
def feedback(body: FeedbackReq):
    con = rw()
    cur = con.execute("INSERT INTO app_feedback(page, preschool_id, text, created_at) VALUES (?,?,?,?)", (body.page, body.preschool_id, body.text, datetime.now().isoformat(timespec="seconds")))
    return {"feedback_id": cur.lastrowid}


# ----------------------------------------------------------------------------- export
@app.get("/api/v1/export")
def export(scope: str = "season", format: str = "xlsx", town: str | None = None, level: str | None = None, top_n: int | None = None):
    if format not in ("xlsx", "csv"): raise ApiError(400, "BAD_FORMAT", "format 需為 xlsx 或 csv")
    con = ro()
    if scope == "season":
        data = season_list(); items = [{**r, "層": "已裁罰"} for r in data["penalized"]] + [{**r, "層": "同負責人待確認"} for r in data["linked"]]
        for it in items:
            it["causes_text"] = "、".join(f"{c['label']}×{c['n']}" for c in it.get("causes", [])); it["focus_text"] = "；".join(it.get("focus", []))
    elif scope == "schedule":
        items = schedule_get()["visits"]
    else:
        items = rankings(town=town, level=level or "高,中,低", top_n=top_n or int(settings(con)["top_n_default"]), size=2000)["items"]
    if not items: raise ApiError(422, "EMPTY_LIST", "無資料可匯出")
    cols = ["title", "town", "type", "score", "level", "rank", "reason", "causes_text", "focus_text", "tier", "層", "linker_name", "linker_code", "week_no", "inspector_no", "n_events", "last_event"]
    cols = [c for c in cols if any(c in it for it in items)]
    head = {"title": "園名", "town": "行政區", "type": "立案別", "score": "分數", "causes_text": "根因（36 月違規類型）", "focus_text": "稽查重點", "level": "等級", "rank": "排名", "reason": "理由", "tier": "層別", "層": "層", "linker_name": "負責人", "linker_code": "負責人代碼", "week_no": "週", "inspector_no": "稽查員", "n_events": "裁罰事件數", "last_event": "最近裁罰"}
    fname = f"watchdog-{scope}-{date.today().isoformat()}"
    if format == "csv":
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(head[c] for c in cols)
        for it in items: w.writerow(it.get(c, "") for c in cols)
        return StreamingResponse(iter([("﻿" + buf.getvalue()).encode("utf-8")]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = scope; ws.append([head[c] for c in cols])
    for it in items: ws.append([it.get(c, "") for c in cols])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})


# ----------------------------------------------------------------------------- ask
class AskReq(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: str = ""
    session_id: str = "demo"


@app.post("/api/v1/ask")
def ask(body: AskReq):
    if not agent.enabled:
        raise ApiError(409, "AGENT_DISABLED", "未設定 ANTHROPIC_API_KEY，問答停用", "在 .env 設定金鑰後重啟", state="agent_disabled")
    try:
        return agent.ask(body.question, body.page, body.session_id)
    except Exception as e:  # LLM outage → degrade, never 500
        raise ApiError(503, "AGENT_UNAVAILABLE", "問答服務暫時無法使用", str(e)[:200], retryable=True)


@app.get("/api/v1/health")
def health():
    return {"ok": True, "db": DB_PATH.exists(), "agent": agent.enabled}


WEB = ROOT / "web"
if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")

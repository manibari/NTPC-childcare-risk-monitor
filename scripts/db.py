"""DBBuilder: rebuild source tables (src_*) inside one transaction, never touch app_* tables.

Table families (autoplan Eng A1):
  src_*   rebuilt on every update from data/*.json + data/*.csv (kiang mirror, OCR output)
  app_*   persistent: models, scores, schedules, watchlist, season list, feedback, settings, runs
  v_*     de-identified views — the ONLY thing the API and the Q&A agent read

Safety rails: WAL + busy_timeout, required-column assertions, >20% row-count drop aborts before
anything is dropped, orphan check after rebuild, penalty events de-duplicated (school × date).
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sqlite3
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from errors import PipelineError  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/watchdog.sqlite"
DEFAULT_DATA = ROOT / "data"
SCHEMA_VERSION = 1
CHILD_SAFETY_ARTICLES = {"第30條", "第33條", "第43條"}
ROW_DROP_THRESHOLD = 0.20

REQUIRED_PRESCHOOL_FIELDS = ["id", "title", "city", "town", "type", "owner", "count_approved"]
REQUIRED_PENALTY_FIELDS = ["date", "law", "punishment", "id"]

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_pipeline_runs(
  run_id TEXT NOT NULL, stage TEXT NOT NULL, started_at TEXT NOT NULL, seconds REAL,
  n_rows INTEGER, n_failed INTEGER DEFAULT 0, ok INTEGER NOT NULL, message TEXT);
CREATE INDEX IF NOT EXISTS ix_runs ON app_pipeline_runs(run_id);
CREATE TABLE IF NOT EXISTS app_linkers(
  linker_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key_name TEXT NOT NULL,
  code TEXT NOT NULL UNIQUE, n_schools INTEGER DEFAULT 0, created_at TEXT NOT NULL,
  UNIQUE(kind, key_name));
CREATE TABLE IF NOT EXISTS app_preschool_linkers(
  preschool_id TEXT NOT NULL, linker_id INTEGER NOT NULL, same_name_flag INTEGER DEFAULT 0,
  excluded_by_user INTEGER DEFAULT 0, PRIMARY KEY(preschool_id, linker_id));
CREATE INDEX IF NOT EXISTS ix_pl_linker ON app_preschool_linkers(linker_id);
CREATE TABLE IF NOT EXISTS app_watchlist(
  preschool_id TEXT NOT NULL, asof_date TEXT NOT NULL, reason TEXT NOT NULL,
  source_preschool_id TEXT, source_event_id INTEGER, linker_id INTEGER, tier TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_watch ON app_watchlist(preschool_id, is_current);
CREATE TABLE IF NOT EXISTS app_models(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT, trained_at TEXT NOT NULL, algo TEXT NOT NULL,
  params TEXT NOT NULL, seed INTEGER NOT NULL, feature_hash TEXT NOT NULL, data_asof TEXT NOT NULL,
  eval_year INTEGER, n_train_obs INTEGER, auc REAL, pr_auc REAL, top100_cov REAL,
  baseline_count_auc REAL, baseline_count_top100 REAL, baseline_recency_auc REAL,
  beats_baseline INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'trained', notes TEXT,
  UNIQUE(data_asof, feature_hash, params));
CREATE UNIQUE INDEX IF NOT EXISTS ux_models_active ON app_models(status) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS app_model_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, at TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS app_backtests(
  model_id INTEGER NOT NULL, obs_year INTEGER NOT NULL, n_obs INTEGER, n_pos INTEGER,
  auc REAL, pr_auc REAL, top50 REAL, top100 REAL, top200 REAL, lead_days_median REAL, baseline TEXT);
CREATE INDEX IF NOT EXISTS ix_backtests ON app_backtests(model_id);
CREATE TABLE IF NOT EXISTS app_score_batches(
  score_batch_id INTEGER PRIMARY KEY AUTOINCREMENT, asof_date TEXT NOT NULL, model_id INTEGER,
  method TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'building',
  is_current INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS app_scores(
  score_batch_id INTEGER NOT NULL, preschool_id TEXT NOT NULL, method TEXT NOT NULL,
  prob_12m REAL, risk_01 REAL, score INTEGER, rank INTEGER, level TEXT NOT NULL,
  reason TEXT, top_features TEXT, PRIMARY KEY(score_batch_id, preschool_id));
CREATE INDEX IF NOT EXISTS ix_scores_rank ON app_scores(score_batch_id, rank);
CREATE TABLE IF NOT EXISTS app_schedules(
  schedule_id INTEGER PRIMARY KEY AUTOINCREMENT, score_batch_id INTEGER NOT NULL, asof_date TEXT NOT NULL,
  params TEXT NOT NULL, solver_status TEXT NOT NULL, objective REAL, coverage_pct REAL,
  created_at TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 0, is_stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS app_schedule_visits(
  schedule_id INTEGER NOT NULL, preschool_id TEXT NOT NULL, week_no INTEGER NOT NULL,
  inspector_no INTEGER NOT NULL, rank INTEGER, reason TEXT, pinned INTEGER DEFAULT 0,
  PRIMARY KEY(schedule_id, preschool_id));
CREATE INDEX IF NOT EXISTS ix_visits_week ON app_schedule_visits(schedule_id, week_no);
CREATE TABLE IF NOT EXISTS app_season_list(
  preschool_id TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_by TEXT NOT NULL DEFAULT 'demo',
  note TEXT, status TEXT NOT NULL DEFAULT 'draft');
CREATE TABLE IF NOT EXISTS app_feedback(
  feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, page TEXT NOT NULL, preschool_id TEXT,
  text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_sentiment(
  preschool_id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, query TEXT NOT NULL, n_items INTEGER NOT NULL,
  n_negative INTEGER NOT NULL, n_12m INTEGER NOT NULL, items TEXT NOT NULL,
  rating REAL, n_ratings INTEGER, reviews TEXT, place_id TEXT);
CREATE TABLE IF NOT EXISTS app_agent_turns(
  turn_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, page TEXT,
  question TEXT NOT NULL, answer TEXT, tool_calls TEXT, latency_ms INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_turns ON app_agent_turns(session_id);
"""

DEFAULT_SETTINGS = {
    "n_inspectors": "3",
    "visits_per_inspector_week": "8",
    "quarter_weeks": "13",
    "high_threshold": "0.30",   # prob_12m ≥ → 高
    "mid_threshold": "0.18",    # prob_12m ≥ → 中
    "top_n_default": "100",
    "watch_window_months": "12",
    "stale_days": "90",
    "anonymize_titles": "0",
    "data_asof": "",
}

SRC_DDL = """
CREATE TABLE src_preschools(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT, operator TEXT, city TEXT, town TEXT, type TEXT,
  address TEXT, tel TEXT, url TEXT, reg_no TEXT, reg_date TEXT, count_approved INTEGER, monthly INTEGER,
  pre_public TEXT, is_pre_public INTEGER, is_free5 TEXT, is_after TEXT, size_in REAL, size_out REAL,
  shuttle TEXT, is_active INTEGER, lng REAL, lat REAL);
CREATE INDEX ix_pre_city ON src_preschools(city, type);
CREATE TABLE src_penalties(
  penalty_id INTEGER PRIMARY KEY AUTOINCREMENT, preschool_id TEXT NOT NULL, date TEXT NOT NULL,
  law TEXT, law_article TEXT, punishment TEXT, actor TEXT, actor_role TEXT, actor_name TEXT,
  is_child_safety INTEGER NOT NULL DEFAULT 0, event_id INTEGER);
CREATE INDEX ix_pen_school ON src_penalties(preschool_id, date);
CREATE TABLE src_penalty_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, preschool_id TEXT NOT NULL, date TEXT NOT NULL,
  n_rows INTEGER NOT NULL, n_articles INTEGER NOT NULL, articles TEXT, is_child_safety INTEGER NOT NULL,
  has_stop_enroll INTEGER NOT NULL, has_person_actor INTEGER NOT NULL, UNIQUE(preschool_id, date));
CREATE INDEX ix_ev_school ON src_penalty_events(preschool_id, date);
CREATE TABLE src_statements(
  preschool_id TEXT, code TEXT, name TEXT, title TEXT, fiscal_year INTEGER, n_sources INTEGER,
  bs_ok INTEGER, is_ok INTEGER, payload TEXT);
CREATE INDEX ix_stmt ON src_statements(preschool_id, fiscal_year);
CREATE TABLE src_ratios(
  preschool_id TEXT, code TEXT, name TEXT, title TEXT, fiscal_year INTEGER, capacity INTEGER, payload TEXT);
CREATE INDEX ix_ratio ON src_ratios(preschool_id, fiscal_year);
CREATE TABLE src_evaluations(preschool_id TEXT PRIMARY KEY, result TEXT, fetched_at TEXT);
CREATE TABLE src_finance_flags(
  preschool_id TEXT, code TEXT, fiscal_year INTEGER, level TEXT NOT NULL,
  level_revenue TEXT, level_cost TEXT, level_balance TEXT, level_surplus TEXT,
  is_latest INTEGER NOT NULL DEFAULT 0, direction TEXT, history TEXT,
  reasons TEXT NOT NULL, dims TEXT NOT NULL, metrics TEXT NOT NULL);
CREATE INDEX ix_fin_school ON src_finance_flags(preschool_id, fiscal_year);
"""

VIEWS_DDL = """
CREATE VIEW IF NOT EXISTS v_preschools AS
  SELECT id, title, city, town, type, reg_date, count_approved, monthly, pre_public, is_pre_public,
         is_after, size_in, size_out, is_active, lng, lat FROM src_preschools;
CREATE VIEW IF NOT EXISTS v_penalties AS
  SELECT penalty_id, preschool_id, date, law, law_article, punishment, actor_role, is_child_safety, event_id
  FROM src_penalties;
CREATE VIEW IF NOT EXISTS v_penalty_events AS SELECT * FROM src_penalty_events;
CREATE VIEW IF NOT EXISTS v_ratios AS SELECT preschool_id, code, title, fiscal_year, capacity, payload FROM src_ratios;
CREATE VIEW IF NOT EXISTS v_finance_flags AS
  SELECT preschool_id, code, fiscal_year, level, level_revenue, level_cost, level_balance, level_surplus,
         is_latest, direction, history, reasons, dims FROM src_finance_flags;
CREATE VIEW IF NOT EXISTS v_linkers AS SELECT linker_id, kind, code, key_name AS name, n_schools FROM app_linkers;
CREATE VIEW IF NOT EXISTS v_preschool_linkers AS
  SELECT pl.preschool_id, pl.linker_id, l.kind, l.code, l.key_name AS name, l.n_schools, pl.same_name_flag, pl.excluded_by_user
  FROM app_preschool_linkers pl JOIN app_linkers l ON l.linker_id = pl.linker_id;
CREATE VIEW IF NOT EXISTS v_scores AS
  SELECT s.* , b.asof_date, b.method AS batch_method FROM app_scores s
  JOIN app_score_batches b ON b.score_batch_id = s.score_batch_id WHERE b.is_current = 1;
CREATE VIEW IF NOT EXISTS v_watchlist AS SELECT * FROM app_watchlist WHERE is_current = 1;
CREATE VIEW IF NOT EXISTS v_schedule AS SELECT * FROM app_schedules WHERE is_current = 1;
CREATE VIEW IF NOT EXISTS v_schedule_visits AS
  SELECT v.* FROM app_schedule_visits v JOIN app_schedules s ON s.schedule_id = v.schedule_id WHERE s.is_current = 1;
CREATE VIEW IF NOT EXISTS v_season_list AS SELECT * FROM app_season_list;
CREATE VIEW IF NOT EXISTS v_models AS SELECT * FROM app_models;
CREATE VIEW IF NOT EXISTS v_backtests AS SELECT * FROM app_backtests;
CREATE VIEW IF NOT EXISTS v_model_events AS SELECT * FROM app_model_events;
CREATE VIEW IF NOT EXISTS v_settings AS SELECT * FROM app_settings;
CREATE VIEW IF NOT EXISTS v_pipeline_runs AS SELECT * FROM app_pipeline_runs;
CREATE VIEW IF NOT EXISTS v_sentiment AS SELECT * FROM app_sentiment;
CREATE VIEW IF NOT EXISTS v_ntpc_penalty_summary AS
  SELECT p.id, p.title, p.type, p.town, p.count_approved, p.is_active,
         COUNT(e.event_id) AS n_events, MIN(e.date) AS first_event, MAX(e.date) AS last_event,
         SUM(e.is_child_safety) AS n_child_safety
  FROM src_preschools p LEFT JOIN src_penalty_events e ON e.preschool_id = p.id
  WHERE p.city = '新北市' GROUP BY p.id;
"""

SRC_TABLES = ["src_preschools", "src_penalties", "src_penalty_events", "src_statements", "src_ratios", "src_evaluations", "src_finance_flags"]
APP_TABLES_WITH_PRESCHOOL = ["app_preschool_linkers", "app_watchlist", "app_scores", "app_schedule_visits", "app_season_list"]


def connect(db_path: pathlib.Path = DEFAULT_DB, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _int(v):
    n = _num(v)
    return None if n is None else int(n)


class DBBuilder:
    def __init__(self, db_path: pathlib.Path = DEFAULT_DB, data_dir: pathlib.Path = DEFAULT_DATA):
        self.db_path, self.data_dir = pathlib.Path(db_path), pathlib.Path(data_dir)

    # ---------------------------------------------------------------- app schema
    @staticmethod
    def ensure_app_schema(con: sqlite3.Connection) -> None:
        con.executescript(APP_SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)", (k, v))
        cols = {r[1] for r in con.execute("PRAGMA table_info(app_sentiment)")}
        for col, typ in (("rating", "REAL"), ("n_ratings", "INTEGER"), ("reviews", "TEXT"), ("place_id", "TEXT")):
            if col not in cols:
                con.execute(f"ALTER TABLE app_sentiment ADD COLUMN {col} {typ}")
        cur = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if cur is None or cur < SCHEMA_VERSION:
            con.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, _now()))

    # ---------------------------------------------------------------- load sources
    def load_sources(self) -> dict:
        pre_path = self.data_dir / "kiang_preschools.json"
        pen_path = self.data_dir / "kiang_punish_all.json"
        if not pre_path.exists() or not pen_path.exists():
            raise PipelineError("缺少公開資料快照", f"找不到 {pre_path.name} 或 {pen_path.name}",
                                "執行 `python scripts/update.py --from ingest`，或從 https://kiang.github.io/ap.ece.moe.edu.tw/ 下載兩個 JSON 放進 data/", stage="build")
        P = json.load(open(pre_path))
        X = json.load(open(pen_path))
        feats = P.get("features") if isinstance(P, dict) else P
        if not feats:
            raise PipelineError("園所主檔為空", f"{pre_path.name} 沒有 features", "重新下載快照", stage="build")
        props0 = feats[0].get("properties", feats[0])
        missing = [f for f in REQUIRED_PRESCHOOL_FIELDS if f not in props0]
        if missing:
            raise PipelineError("園所主檔欄位不符", f"缺欄位 {missing}（kiang 可能改了 schema）",
                                "比對 scripts/db.py REQUIRED_PRESCHOOL_FIELDS 與快照，更新對應", stage="build")
        preschools = []
        for f in feats:
            p = f.get("properties", f)
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None]
            title = p.get("title") or ""
            m = re.search(r"委託(.+?)辦理", title)
            preschools.append((
                p["id"], title, (p.get("owner") or "").strip(), m.group(1) if m else None, p.get("city"), p.get("town"),
                p.get("type"), p.get("address"), p.get("tel"), p.get("url"), p.get("reg_no"), (p.get("reg_date") or "").replace("/", "-") or None,
                _int(p.get("count_approved")), _int(p.get("monthly")), p.get("pre_public"),
                1 if (p.get("pre_public") or "").strip() not in ("", "無") else 0,
                p.get("is_free5"), p.get("is_after"), _num(str(p.get("size_in") or "").replace("平方公尺", "")),
                _num(str(p.get("size_out") or "").replace("平方公尺", "")), (p.get("shuttle") or "").strip() or None,
                _int(p.get("is_active")), coords[0], coords[1]))
        if not isinstance(X, dict):
            raise PipelineError("裁罰快照格式不符", "punish_all.json 應為 {行為人: [裁罰...]}", "重新下載快照", stage="build")
        penalties = []
        for actor, items in X.items():
            for it in items:
                missing = [f for f in REQUIRED_PENALTY_FIELDS if f not in it]
                if missing:
                    raise PipelineError("裁罰快照欄位不符", f"缺欄位 {missing}", "更新 scripts/db.py 的欄位對應", stage="build")
                law = it.get("law") or ""
                art = re.match(r"^(第\d+條)", law)
                article = art.group(1) if art else None
                role, _, name = actor.partition("：")
                penalties.append((it["id"], it["date"].replace("/", "-"), law, article, it.get("punishment"), actor,
                                  role or None, name or None, 1 if article in CHILD_SAFETY_ARTICLES else 0))
        statements = self._read_csv("statements.csv")
        ratios = self._read_csv("ratios.csv")
        return {"preschools": preschools, "penalties": penalties, "statements": statements, "ratios": ratios}

    def _read_csv(self, name: str) -> list[dict]:
        path = self.data_dir / name
        if not path.exists():
            return []  # OCR products are optional (bootstrap without raw PDFs)
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))

    # ---------------------------------------------------------------- rebuild
    def rebuild_src(self, con: sqlite3.Connection, src: dict) -> dict:
        counts_new = {"src_preschools": len(src["preschools"]), "src_penalties": len(src["penalties"])}
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t, n_new in counts_new.items():
            if t in existing:
                n_old = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                if n_old and n_new < n_old * (1 - ROW_DROP_THRESHOLD):
                    raise PipelineError(f"{t} 筆數驟降 {n_old} → {n_new}（-{(1-n_new/n_old):.0%} > {ROW_DROP_THRESHOLD:.0%}）",
                                        "來源快照可能殘缺或被截斷", "檢查 data/kiang_*.json；確定要覆蓋則加 --force-rebuild", stage="build")
        con.execute("BEGIN IMMEDIATE")
        try:
            for (v,) in con.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
                con.execute(f"DROP VIEW IF EXISTS {v}")  # every v_* is recreated from VIEWS_DDL below
            for t in SRC_TABLES:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            _exec_ddl(con, SRC_DDL)
            con.executemany("INSERT INTO src_preschools VALUES (" + ",".join("?" * 24) + ")", src["preschools"])
            con.executemany("INSERT INTO src_penalties(preschool_id,date,law,law_article,punishment,actor,actor_role,actor_name,is_child_safety) VALUES (?,?,?,?,?,?,?,?,?)", src["penalties"])
            # events: one per school × date (autoplan Eng A2)
            con.execute("""
                INSERT INTO src_penalty_events(preschool_id, date, n_rows, n_articles, articles, is_child_safety, has_stop_enroll, has_person_actor)
                SELECT preschool_id, date, COUNT(*), COUNT(DISTINCT law_article), GROUP_CONCAT(DISTINCT law_article),
                       MAX(is_child_safety), MAX(punishment LIKE '停止招生%'), MAX(actor_role = '行為人')
                FROM src_penalties GROUP BY preschool_id, date""")
            con.execute("UPDATE src_penalties SET event_id = (SELECT event_id FROM src_penalty_events e WHERE e.preschool_id = src_penalties.preschool_id AND e.date = src_penalties.date)")
            resolve = self.finance_resolver(src["preschools"])
            unlinked = {"src_statements": 0, "src_ratios": 0}
            for row in src["statements"]:
                pid = row.get("preschool_id") or resolve(row.get("title"), row.get("name"))
                unlinked["src_statements"] += pid is None
                con.execute("INSERT INTO src_statements VALUES (?,?,?,?,?,?,?,?,?)",
                            (pid, row.get("code"), row.get("name"), row.get("title"), _int(row.get("fiscal_year")),
                             _int(row.get("n_sources")), _int(row.get("bs_ok") in ("True", "1", "true")), _int(row.get("is_ok") in ("True", "1", "true")),
                             json.dumps({k: v for k, v in row.items() if k.startswith(("bs_", "is_"))}, ensure_ascii=False)))
            for row in src["ratios"]:
                pid = row.get("preschool_id") or resolve(row.get("title"), row.get("name"))
                unlinked["src_ratios"] += pid is None
                con.execute("INSERT INTO src_ratios VALUES (?,?,?,?,?,?,?)",
                            (pid, row.get("code"), row.get("name"), row.get("title"), _int(row.get("fiscal_year")),
                             _int(row.get("capacity")), json.dumps({k: v for k, v in row.items() if k not in ("preschool_id", "code", "name", "title", "fiscal_year", "capacity", "penalised", "n_penalty", "pre_penalty")}, ensure_ascii=False)))
            self._insert_finance_flags(con, src["ratios"], resolve)
            _exec_ddl(con, VIEWS_DDL)
            orphans = self.orphan_check(con)
            data_asof = con.execute("SELECT MAX(date) FROM src_penalty_events").fetchone()[0] or ""
            con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('data_asof', ?)", (data_asof,))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        stats = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SRC_TABLES}
        stats["orphans"] = orphans
        stats["finance_unlinked"] = {k: v for k, v in unlinked.items() if v}  # rows whose school could not be resolved
        stats["data_asof"] = data_asof
        return stats

    @staticmethod
    def _insert_finance_flags(con: sqlite3.Connection, ratios: list[dict], resolve) -> None:
        """財務燈號 (scripts/finance.py): one row per school-year, four dimensions + overall; latest year carries the summary."""
        import finance  # local import: keeps db.py importable without pandas-side deps
        by_code: dict[str, list[dict]] = {}
        for row in ratios:
            by_code.setdefault(row["code"], []).append(row)
        peer = finance.peer_baselines(ratios)
        for code, rows in by_code.items():
            pid = rows[0].get("preschool_id") or resolve(rows[0].get("title"), rows[0].get("name"))
            flags = finance.flag_school(rows, peer)
            summary = finance.summarize(flags)
            for f in flags:
                latest = f is flags[-1]
                con.execute(
                    "INSERT INTO src_finance_flags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, code, f.fiscal_year, f.level,
                     f.dims.get("收入", {}).get("level"), f.dims.get("支出", {}).get("level"),
                     f.dims.get("資債", {}).get("level"), f.dims.get("餘絀", {}).get("level"),
                     int(latest), summary["direction"] if latest else None, summary["history"] if latest else None,
                     json.dumps(f.reasons, ensure_ascii=False),
                     json.dumps({d: v.get("level") for d, v in f.dims.items()}, ensure_ascii=False),
                     json.dumps({k: v for k, v in f.metrics.items() if k not in ("bs_ok", "is_ok")}, ensure_ascii=False)))

    @staticmethod
    def orphan_check(con: sqlite3.Connection) -> dict:
        out = {}
        for t in APP_TABLES_WITH_PRESCHOOL:
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE preschool_id NOT IN (SELECT id FROM src_preschools)").fetchone()[0]
            if n:
                out[t] = n
        return out

    @staticmethod
    def finance_resolver(preschools: list[tuple]):
        """Map an OCR report's school name (short, e.g. '安溪') or full kiang title to preschool id.

        Financial reports exist only for 新北市 非營利 schools; the OCR filename carries the short
        name and kiang's title is '新北市<name>非營利幼兒園(委託…)' or '新北市政府<name>…'.
        Returns a function(title, name) -> id | None. Ambiguous short names resolve to None."""
        def norm(t) -> str:
            return (t or "").replace("（", "(").replace("）", ")").replace(" ", "")

        by_title: dict[str, str] = {}
        by_short: dict[str, list[str]] = {}
        for row in preschools:
            pid, title, city, typ = row[0], row[1], row[4], row[6]
            by_title[norm(title)] = pid
            if city != "新北市" or typ != "非營利":
                continue
            m = re.match(r"^新北市(?:政府)?(.+?)非營利幼兒園", title)
            if m:
                by_short.setdefault(m.group(1), []).append(pid)

        def resolve(title: str | None, name: str | None):
            if title and norm(title) in by_title:
                return by_title[norm(title)]
            hits = by_short.get((name or "").strip(), [])
            return hits[0] if len(hits) == 1 else None
        return resolve

    def build(self) -> dict:
        t0 = time.time()
        con = connect(self.db_path)
        try:
            self.ensure_app_schema(con)
            src = self.load_sources()
            stats = self.rebuild_src(con, src)
            stats["seconds"] = round(time.time() - t0, 1)
            return stats
        finally:
            con.close()


def _exec_ddl(con: sqlite3.Connection, ddl: str) -> None:
    """Execute a DDL script statement-by-statement so it stays inside the open transaction
    (executescript() would COMMIT first)."""
    for stmt in ddl.split(";"):
        if stmt.strip():
            con.execute(stmt)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    try:
        stats = DBBuilder().build()
    except PipelineError as e:
        print(e.format(), file=sys.stderr)
        return e.exit_code
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""P6 update: the whole pipeline with one CLI contract.

    python scripts/update.py [--from STAGE] [--no-fetch] [--no-train] [--db PATH] [--max-time S]

Stages in order: ingest → build → linker → train → score → schedule. Every stage writes one row to
app_pipeline_runs (run_id, stage, seconds, n_rows, ok, message). Exit codes: 0 all ok · 1 a stage failed
but the pipeline continued on the previous snapshot (PartialFailure) · 2 aborted, data would be worse
(PipelineError) · 64 usage error.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
import time
import uuid
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, DBBuilder, connect  # noqa: E402
from errors import PartialFailure, PipelineError, UsageError  # noqa: E402

STAGES = ["ingest", "build", "linker", "train", "score", "schedule"]


def log(con: sqlite3.Connection, run_id: str, stage: str, t0: float, ok: bool, n_rows=None, message: str = "") -> None:
    con.execute("INSERT INTO app_pipeline_runs(run_id, stage, started_at, seconds, n_rows, n_failed, ok, message) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, stage, datetime.fromtimestamp(t0).isoformat(timespec="seconds"), round(time.time() - t0, 2), n_rows, 0 if ok else 1, int(ok), message[:500]))


def run(db: pathlib.Path, start: str = "ingest", fetch: bool = True, train: bool = True, max_time: float = 20.0) -> int:
    if start not in STAGES:
        raise UsageError(f"未知階段 {start}", "--from 只接受 " + " / ".join(STAGES), "改用正確階段名", stage="update")
    run_id = uuid.uuid4().hex[:8]
    builder = DBBuilder(db)
    con = connect(db); builder.ensure_app_schema(con)
    worst = 0
    todo = STAGES[STAGES.index(start):]
    for stage in todo:
        if stage == "ingest" and not fetch:
            print(f"[{stage}] 略過（--no-fetch）"); continue
        if stage == "train" and not train:
            print(f"[{stage}] 略過（--no-train）"); continue
        t0 = time.time()
        try:
            if stage == "ingest":
                from ingest import fetch as do_fetch
                r = do_fetch(builder.data_dir); log(con, run_id, stage, t0, True, sum(v for k, v in r.items() if k != "raw_dir"), str(r))
            elif stage == "build":
                con.close(); r = builder.build(); con = connect(db)
                log(con, run_id, stage, t0, True, r["src_preschools"], f"events={r['src_penalty_events']} asof={r['data_asof']}")
            elif stage == "linker":
                from linker import coverage_report, refresh_watchlist, sync_linkers
                s = sync_linkers(con); w = refresh_watchlist(con); c = coverage_report(con)
                log(con, run_id, stage, t0, True, s["linkers"], f"new={s['new_codes']} watch={w['penalized']}+{w['linked']} cov={c['penalized_schools_in_linker']}/{c['penalized_schools']}")
            elif stage == "train":
                from train import train_and_record
                asof = con.execute("SELECT value FROM app_settings WHERE key='data_asof'").fetchone()[0]
                mid = train_and_record(con, "gbdt", asof)
                row = con.execute("SELECT auc, top100_cov, beats_baseline FROM app_models WHERE model_id=?", (mid,)).fetchone()
                log(con, run_id, stage, t0, True, mid, f"model={mid} auc={row[0]:.3f} top100={row[1]:.3f} beats={row[2]}")
            elif stage == "score":
                from score import score_all
                r = score_all(con); log(con, run_id, stage, t0, True, r["scored"], f"batch={r['score_batch_id']} method={r['method']} levels={r['levels']}")
            elif stage == "schedule":
                from schedule import run as do_schedule
                r = do_schedule(con, max_time=max_time); log(con, run_id, stage, t0, True, len(r["visits"]), f"{r['status']} coverage={r['coverage_pct']}% must={r['must']}")
            print(f"[{stage}] ok {time.time() - t0:.1f}s")
        except PartialFailure as e:
            log(con, run_id, stage, t0, False, None, e.format()); print(e.format(), file=sys.stderr); worst = max(worst, 1)
        except PipelineError as e:
            log(con, run_id, stage, t0, False, None, e.format()); print(e.format(), file=sys.stderr)
            if stage in ("train",):
                worst = max(worst, 1); continue          # a model that cannot be trained is not fatal
            return 2
    return worst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", default="ingest", help="start stage: " + " / ".join(STAGES))
    ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--no-train", action="store_true")
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB); ap.add_argument("--max-time", type=float, default=20.0)
    a = ap.parse_args(argv)
    try:
        return run(a.db, a.start, fetch=not a.no_fetch, train=not a.no_train, max_time=a.max_time)
    except UsageError as e:
        print(e.format(), file=sys.stderr); return 64


if __name__ == "__main__":
    sys.exit(main())

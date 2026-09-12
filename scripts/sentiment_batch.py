"""Fetch news (and Maps reviews when GOOGLE_MAPS_API_KEY is set) for the top-N ranked schools, politely."""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, connect  # noqa: E402
from sentiment import get_or_refresh  # noqa: E402


def run(db: pathlib.Path, top: int = 100, delay: float = 1.5, refresh: bool = False) -> dict:
    con = connect(db)
    rows = con.execute("SELECT s.preschool_id, p.title, p.town FROM v_scores s JOIN v_preschools p ON p.id=s.preschool_id WHERE s.rank IS NOT NULL AND s.rank<=? ORDER BY s.rank", (top,)).fetchall()
    t0 = time.time(); n_ok = n_err = n_cached = 0; neg = 0
    for i, (pid, title, town) in enumerate(rows, 1):
        r = get_or_refresh(con, pid, title, town, refresh=refresh)
        if r.get("error"): n_err += 1
        elif r.get("cached"): n_cached += 1
        else:
            n_ok += 1; time.sleep(delay)
        neg += 1 if r.get("n_negative") else 0
        print(f"{i:3d}/{len(rows)} {title[:22]:<22} news={r.get('n_items', 0):3d} neg={r.get('n_negative', 0):2d} rating={r.get('rating') or '-'}{' cached' if r.get('cached') else ''}{' ERR ' + r['error'] if r.get('error') else ''}")
    con.execute("INSERT INTO app_pipeline_runs(run_id, stage, started_at, seconds, n_rows, n_failed, ok, message) VALUES (?,?,?,?,?,?,?,?)",
                ("batch", "sentiment", datetime.fromtimestamp(t0).isoformat(timespec="seconds"), round(time.time() - t0, 1), len(rows), n_err, int(n_err == 0), f"top={top} fetched={n_ok} cached={n_cached} with_negative={neg}"))
    con.commit()
    return {"n": len(rows), "fetched": n_ok, "cached": n_cached, "errors": n_err, "with_negative": neg, "seconds": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB); ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--delay", type=float, default=1.5); ap.add_argument("--refresh", action="store_true"); a = ap.parse_args(argv)
    print(run(a.db, a.top, a.delay, a.refresh)); return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build data/demo/watchdog-demo.sqlite from the working DB with every personal-name column blanked.

Linker codes stay (they are the join key); the names behind them do not travel with the demo.
"""
import pathlib
import shutil
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, ROOT  # noqa: E402

OUT = ROOT / "data" / "demo" / "watchdog-demo.sqlite"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DEFAULT_DB); src.execute("PRAGMA wal_checkpoint(TRUNCATE)"); src.close()
    shutil.copy2(DEFAULT_DB, OUT)
    con = sqlite3.connect(OUT)
    con.executescript("""
      UPDATE src_preschools SET owner = NULL, tel = NULL, address = NULL, url = NULL;
      UPDATE src_penalties SET actor = NULL, actor_name = NULL;
      UPDATE app_linkers SET key_name = code;
      DELETE FROM app_agent_turns; DELETE FROM app_feedback;
      DELETE FROM app_pipeline_runs;
      VACUUM;""")
    n = con.execute("SELECT COUNT(*) FROM src_preschools WHERE owner IS NOT NULL").fetchone()[0]
    con.close()
    assert n == 0
    print(OUT, round(OUT.stat().st_size / 1e6, 1), "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

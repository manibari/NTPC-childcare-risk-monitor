"""Thin wrapper kept for backwards compatibility: `python scripts/build_db.py` == DBBuilder().build()."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

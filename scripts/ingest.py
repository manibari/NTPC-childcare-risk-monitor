"""P6 ingest: fetch the two public snapshots (kiang mirror of 全國教保資訊網) into raw-web/<date>/ and data/.

No redirects are followed (a 30x is reported, not silently swallowed). On any failure the previous
snapshot in data/ is left untouched and a PartialFailure is raised (exit 1).
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
import urllib.request
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import ROOT  # noqa: E402
from errors import PartialFailure  # noqa: E402

BASE = "https://kiang.github.io/ap.ece.moe.edu.tw/"
FILES = {"preschools.json": "kiang_preschools.json", "punish_all.json": "kiang_punish_all.json"}
MIN_BYTES = {"kiang_preschools.json": 2_000_000, "kiang_punish_all.json": 200_000}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PartialFailure("來源回應轉址", f"{req.full_url} → {code} {newurl}", "來源可能搬家；確認 BASE 或改手動下載", stage="ingest")


def fetch(data_dir: pathlib.Path = ROOT / "data", raw_dir: pathlib.Path | None = None, timeout: int = 60) -> dict:
    raw_dir = raw_dir or ROOT / "raw-web" / date.today().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_NoRedirect)
    out = {}
    for remote, local in FILES.items():
        try:
            with opener.open(urllib.request.Request(BASE + remote, headers={"User-Agent": "smart-watchdog/1.0"}), timeout=timeout) as r:
                body = r.read()
        except PartialFailure:
            raise
        except Exception as e:  # network / 5xx
            raise PartialFailure("公開資料抓取失敗，沿用前一快照", f"{remote}: {e}", "稍後重跑 `python scripts/update.py --from ingest`", stage="ingest")
        if len(body) < MIN_BYTES[local]:
            raise PartialFailure("公開資料檔案異常縮小，沿用前一快照", f"{remote} 只有 {len(body)} bytes", "檢查來源是否改版", stage="ingest")
        (raw_dir / local).write_bytes(body)
        out[local] = len(body)
    for local in FILES.values():
        shutil.copy2(raw_dir / local, data_dir / local)
    return {"raw_dir": str(raw_dir), **out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--data-dir", type=pathlib.Path, default=ROOT / "data"); a = ap.parse_args(argv)
    try:
        print(fetch(a.data_dir))
    except PartialFailure as e:
        print(e.format(), file=sys.stderr); return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())

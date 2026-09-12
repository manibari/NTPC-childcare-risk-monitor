"""P2 approve: single active model, Approver-only status changes, drop rule (架構定調 8)."""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, connect  # noqa: E402
from errors import PipelineError, UsageError  # noqa: E402

MAX_AUC_DROP = 0.05
from db import ROOT  # noqa: E402
MODEL_DIR = ROOT / "data" / "models"


def approve(con: sqlite3.Connection, model_id: int, actor: str, force: bool = False) -> None:
    row = con.execute("SELECT status, auc, beats_baseline FROM app_models WHERE model_id=?", (model_id,)).fetchone()
    if row is None:
        raise UsageError(f"model_id {model_id} 不存在", "查無此版本", "先跑 train.py", stage="approve")
    status, auc, beats = row
    if not (MODEL_DIR / f"model_{model_id}.pkl").exists():
        raise PipelineError("模型檔不存在，不核准", f"data/models/model_{model_id}.pkl 遺失", "重新訓練此版本", stage="approve")
    if not beats and not force:
        raise PipelineError("模型未勝過按次數排序，不核准", "回測 AUC 或前 100 覆蓋率未同時優於 count baseline",
                            "維持規則排序；或 --force 明示理由", stage="approve")
    active = con.execute("SELECT model_id, auc FROM app_models WHERE status='active'").fetchone()
    if active and auc is not None and active[1] is not None and auc < active[1] - MAX_AUC_DROP and not force:
        raise PipelineError("新版本 AUC 較現行 active 下降超過 0.05", f"{auc:.3f} vs {active[1]:.3f}",
                            "確認資料是否異常；或 --force", stage="approve")
    con.execute("BEGIN IMMEDIATE")
    try:
        now = date.today().isoformat()
        if active:
            con.execute("UPDATE app_models SET status='retired' WHERE model_id=?", (active[0],))
            con.execute("INSERT INTO app_model_events(model_id, at, from_status, to_status, actor, reason) VALUES (?,?,?,?,?,?)",
                        (active[0], now, "active", "retired", actor, f"replaced by {model_id}"))
        con.execute("UPDATE app_models SET status='active' WHERE model_id=?", (model_id,))
        con.execute("INSERT INTO app_model_events(model_id, at, from_status, to_status, actor, reason) VALUES (?,?,?,?,?,?)",
                    (model_id, now, status, "active", actor, "approved" + (" (forced)" if force else "")))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def retire(con: sqlite3.Connection, model_id: int, actor: str, reason: str = "") -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE app_models SET status='retired' WHERE model_id=?", (model_id,))
        con.execute("INSERT INTO app_model_events(model_id, at, from_status, to_status, actor, reason) VALUES (?,?,?,?,?,?)",
                    (model_id, date.today().isoformat(), "active", "retired", actor, reason))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id", type=int)
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retire", action="store_true")
    a = ap.parse_args(argv)
    con = connect(a.db)
    try:
        if a.retire:
            retire(con, a.model_id, a.actor, "manual")
        else:
            approve(con, a.model_id, a.actor, a.force)
    except PipelineError as e:
        print(e.format(), file=sys.stderr)
        return e.exit_code
    print(f"model {a.model_id}: {'retired' if a.retire else 'active'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""P3/P3b tests: batch switch atomicity, 無紀錄 rows, CP-SAT constraints, capacity 0, determinism."""
import json

import pytest

from conftest import preschool
from db import DBBuilder, connect
from errors import PipelineError
from linker import refresh_watchlist, sync_linkers
from schedule import load_problem, run, solve
from score import score_all


def _con(tmp_path, synthetic_data, n_extra=6):
    feats = [preschool("A", "甲"), preschool("B", "乙（委託社團法人丙協會辦理）", typ="非營利", owner="")]
    feats += [preschool(f"X{i}", f"園{i}", town="板橋區" if i % 2 else "新莊區", owner="王小明" if i < 2 else f"人{i}") for i in range(n_extra)]
    for f in feats[2:]:
        f["geometry"]["coordinates"] = [121.41, 25.005]
    punish = json.loads((synthetic_data / "kiang_punish_all.json").read_text())
    punish["負責人：王小明"] += [{"id": f"X{i}", "date": f"202{3+i%2}/0{1+i}/15", "law": "第16條", "punishment": "罰鍰"} for i in range(n_extra)]
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps({"features": feats}, ensure_ascii=False))
    (synthetic_data / "kiang_punish_all.json").write_text(json.dumps(punish, ensure_ascii=False))
    b = DBBuilder(tmp_path / "wd.sqlite", synthetic_data); b.build()
    con = connect(b.db_path)
    sync_linkers(con); refresh_watchlist(con, asof="2025-12-31")
    return con


def test_score_batch_switch_and_unscored(tmp_path, synthetic_data):
    con = _con(tmp_path, synthetic_data)
    r1 = score_all(con, asof="2025-12-31")
    r2 = score_all(con, asof="2025-12-31")
    assert con.execute("SELECT COUNT(*) FROM app_score_batches WHERE is_current=1").fetchone()[0] == 1
    assert con.execute("SELECT score_batch_id FROM app_score_batches WHERE is_current=1").fetchone()[0] == r2["score_batch_id"]
    rows = dict(con.execute("SELECT preschool_id, level FROM app_scores WHERE score_batch_id=?", (r2["score_batch_id"],)).fetchall())
    assert rows["B"] == "無紀錄" and rows["A"] in ("高", "中", "低")
    assert con.execute("SELECT prob_12m FROM app_scores WHERE score_batch_id=? AND preschool_id='B'", (r2["score_batch_id"],)).fetchone()[0] is None
    assert r1["scored"] == 7


def test_schedule_constraints_and_determinism(tmp_path, synthetic_data):
    con = _con(tmp_path, synthetic_data)
    score_all(con, asof="2025-12-31")
    con.execute("UPDATE app_settings SET value='2' WHERE key='n_inspectors'")
    con.execute("UPDATE app_settings SET value='1' WHERE key='visits_per_inspector_week'")
    con.execute("UPDATE app_settings SET value='2' WHERE key='quarter_weeks'")
    con.execute("INSERT INTO app_season_list(preschool_id, added_at) VALUES ('X5','2026-01-01')")
    prob = load_problem(con)
    r = solve(prob, pinned={"X4": (1, 0)}, max_time=5)
    assert len(r["visits"]) <= 4 and r["status"] in ("OPTIMAL", "FEASIBLE")
    by = {v["preschool_id"]: v for v in r["visits"]}
    assert "X5" in by                                    # must-visit honoured
    assert by["X4"]["week_no"] == 2 and by["X4"]["inspector_no"] == 1 and by["X4"]["pinned"] == 1
    for w in (1, 2):
        for i in (1, 2):
            assert sum(1 for v in r["visits"] if v["week_no"] == w and v["inspector_no"] == i) <= 1
    r2 = solve(prob, pinned={"X4": (1, 0)}, max_time=5)
    assert r["visits"] == r2["visits"]                    # fixed seed → identical
    res = run(con, pinned={"X4": (1, 0)}, max_time=5)
    assert con.execute("SELECT COUNT(*) FROM app_schedules WHERE is_current=1").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM app_schedule_visits WHERE schedule_id=?", (res["schedule_id"],)).fetchone()[0] == len(res["visits"])


def test_schedule_capacity_zero_and_excluded(tmp_path, synthetic_data):
    con = _con(tmp_path, synthetic_data)
    score_all(con, asof="2025-12-31")
    con.execute("UPDATE app_settings SET value='0' WHERE key='n_inspectors'")
    with pytest.raises(PipelineError) as ei:
        solve(load_problem(con))
    assert "產能為 0" in str(ei.value)
    con.execute("UPDATE app_settings SET value='1' WHERE key='n_inspectors'")
    r = solve(load_problem(con), excluded={"A"}, max_time=5)
    assert all(v["preschool_id"] != "A" for v in r["visits"])

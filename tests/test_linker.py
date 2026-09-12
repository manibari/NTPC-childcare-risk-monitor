"""P1 tests: persistent codes, same-name flag, two-tier watchlist, exclusion honoured."""
import json

from conftest import preschool
from db import DBBuilder, connect
from linker import refresh_watchlist, sync_linkers


def _build(tmp_path, data):
    b = DBBuilder(tmp_path / "wd.sqlite", data)
    b.build()
    return b, connect(b.db_path)


def test_codes_persist_and_never_recycle(tmp_path, synthetic_data):
    b, con = _build(tmp_path, synthetic_data)
    s = sync_linkers(con)
    assert s["new_codes"] == 2                           # owner 王小明 (A only; B is 非營利 owner='') + operator 丙協會
    code_a = con.execute("SELECT code FROM app_linkers WHERE kind='owner'").fetchone()[0]
    assert code_a == "O-000001"
    # drop school A's owner, add a new owner → old code keeps its number, new owner gets O-000002
    feats = [preschool("A", "甲", owner="趙六"), preschool("B", "乙（委託社團法人丙協會辦理）", typ="非營利", owner="")]
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps({"features": feats}, ensure_ascii=False))
    b.build()
    s2 = sync_linkers(con)
    assert s2["new_codes"] == 1
    codes = dict(con.execute("SELECT key_name, code FROM app_linkers WHERE kind='owner'").fetchall())
    assert codes == {"王小明": "O-000001", "趙六": "O-000002"}
    assert con.execute("SELECT n_schools FROM app_linkers WHERE key_name='王小明'").fetchone()[0] == 0


def test_same_name_flag_beyond_5km(tmp_path, synthetic_data):
    feats = [preschool("A", "甲", owner="王小明"), preschool("C", "丙", owner="王小明", extra={}),
             preschool("D", "丁", owner="王小明")]
    feats[1]["geometry"]["coordinates"] = [121.41, 25.005]     # ~1.2 km from A
    feats[2]["geometry"]["coordinates"] = [121.90, 25.20]      # ~55 km away → namesake
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps({"features": feats}, ensure_ascii=False))
    _, con = _build(tmp_path, synthetic_data)
    s = sync_linkers(con)
    flags = dict(con.execute("SELECT preschool_id, same_name_flag FROM app_preschool_linkers").fetchall())
    assert flags == {"A": 0, "C": 0, "D": 1} and s["same_name_flags"] == 1


def test_watchlist_two_tiers_and_exclusion(tmp_path, synthetic_data):
    feats = [preschool("A", "甲", owner="王小明"), preschool("C", "丙", owner="王小明"), preschool("E", "戊", owner="孫七")]
    feats[1]["geometry"]["coordinates"] = [121.41, 25.005]
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps({"features": feats}, ensure_ascii=False))
    _, con = _build(tmp_path, synthetic_data)
    sync_linkers(con)
    w = refresh_watchlist(con, asof="2024-12-31")
    assert (w["penalized"], w["linked"]) == (1, 1)
    tiers = dict(con.execute("SELECT preschool_id, tier FROM app_watchlist WHERE is_current=1").fetchall())
    assert tiers == {"A": "penalized", "C": "linked"}
    assert "同負責人" in con.execute("SELECT reason FROM app_watchlist WHERE preschool_id='C'").fetchone()[0]
    # outside the 12-month window → nothing
    w2 = refresh_watchlist(con, asof="2026-06-01")
    assert (w2["penalized"], w2["linked"]) == (0, 0)
    assert con.execute("SELECT COUNT(*) FROM app_watchlist WHERE is_current=0").fetchone()[0] == 2  # history kept
    # user excludes the link → C disappears from tier linked
    con.execute("UPDATE app_preschool_linkers SET excluded_by_user=1 WHERE preschool_id='C'")
    w3 = refresh_watchlist(con, asof="2024-12-31")
    assert w3["linked"] == 0

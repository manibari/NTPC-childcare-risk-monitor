"""P0 regression-level tests: event de-dup, app_* preservation, guards, anonymised views."""
import json
import sqlite3

import pytest

from conftest import HAS_REAL, REAL_DATA, ROOT, preschool
from db import DBBuilder, connect
from errors import PipelineError


def build(tmp_path, data_dir):
    b = DBBuilder(tmp_path / "wd.sqlite", data_dir)
    return b, b.build()


def test_penalty_events_dedup_school_date(tmp_path, synthetic_data):
    b, stats = build(tmp_path, synthetic_data)
    con = connect(b.db_path)
    assert stats["src_penalties"] == 4
    assert stats["src_penalty_events"] == 2                      # 2024-01-10 and 2024-06-01
    ev = con.execute("SELECT date, n_rows, n_articles, is_child_safety, has_stop_enroll, has_person_actor FROM src_penalty_events ORDER BY date").fetchall()
    assert ev[0] == ("2024-01-10", 2, 2, 1, 0, 0)
    assert ev[1] == ("2024-06-01", 2, 2, 1, 1, 1)
    assert con.execute("SELECT COUNT(*) FROM src_penalties WHERE event_id IS NULL").fetchone()[0] == 0


def test_rebuild_preserves_app_tables_and_settings(tmp_path, synthetic_data):
    b, _ = build(tmp_path, synthetic_data)
    con = connect(b.db_path)
    con.execute("INSERT INTO app_season_list(preschool_id, added_at) VALUES ('A', '2026-01-01')")
    con.execute("UPDATE app_settings SET value='5' WHERE key='n_inspectors'")
    con.execute("INSERT INTO app_linkers(kind,key_name,code,created_at) VALUES ('owner','王小明','O-000001','2026-01-01')")
    con.close()
    b.build()  # second rebuild
    con = connect(b.db_path)
    assert con.execute("SELECT COUNT(*) FROM app_season_list").fetchone()[0] == 1
    assert con.execute("SELECT value FROM app_settings WHERE key='n_inspectors'").fetchone()[0] == "5"
    assert con.execute("SELECT code FROM app_linkers").fetchone()[0] == "O-000001"
    assert con.execute("SELECT value FROM app_settings WHERE key='data_asof'").fetchone()[0] == "2024-06-01"


def test_row_drop_guard_aborts_before_drop(tmp_path, synthetic_data):
    b, _ = build(tmp_path, synthetic_data)
    # shrink the preschool snapshot to 1 of 2 (-50%)
    feats = [preschool("A", "新北市私立甲幼兒園")]
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps({"features": feats}, ensure_ascii=False))
    with pytest.raises(PipelineError) as ei:
        b.build()
    assert "筆數驟降" in str(ei.value) and ei.value.exit_code == 2
    con = connect(b.db_path)
    assert con.execute("SELECT COUNT(*) FROM src_preschools").fetchone()[0] == 2  # old data intact


def test_schema_assertion(tmp_path, synthetic_data):
    bad = {"features": [{"properties": {"id": "A", "title": "x"}}]}
    (synthetic_data / "kiang_preschools.json").write_text(json.dumps(bad))
    with pytest.raises(PipelineError) as ei:
        DBBuilder(tmp_path / "wd.sqlite", synthetic_data).build()
    assert "欄位不符" in str(ei.value)


def test_views_never_expose_names(tmp_path, synthetic_data):
    b, _ = build(tmp_path, synthetic_data)
    con = connect(b.db_path, readonly=True)
    for view in ("v_preschools", "v_penalties", "v_penalty_events", "v_ratios", "v_linkers", "v_ntpc_penalty_summary"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({view})")]
        for banned in ("owner", "operator", "actor", "actor_name", "tel", "address", "key_name"):
            assert banned not in cols, f"{view} exposes {banned}"
    dump = " ".join(str(r) for v in ("v_penalties", "v_ntpc_penalty_summary") for r in con.execute(f"SELECT * FROM {v}"))
    assert "王小明" not in dump and "李某" not in dump


def test_orphan_check_reports_missing_school(tmp_path, synthetic_data):
    b, _ = build(tmp_path, synthetic_data)
    con = connect(b.db_path)
    con.execute("INSERT INTO app_season_list(preschool_id, added_at) VALUES ('ZZZ', '2026-01-01')")
    assert DBBuilder.orphan_check(con) == {"app_season_list": 1}


@pytest.mark.skipif(not HAS_REAL, reason="real kiang snapshot not present")
def test_real_data_event_counts(tmp_path):
    b, stats = build(tmp_path, REAL_DATA)
    con = connect(b.db_path)
    ntpc = con.execute("SELECT COUNT(*) FROM src_penalty_events e JOIN src_preschools p ON p.id=e.preschool_id WHERE p.city='新北市'").fetchone()[0]
    assert ntpc == 1004
    rows = con.execute("SELECT COUNT(*) FROM src_penalties x JOIN src_preschools p ON p.id=x.preschool_id WHERE p.city='新北市'").fetchone()[0]
    assert rows == 1474
    assert stats["orphans"] == {}


def test_finance_rows_resolve_to_preschool_id(tmp_path, synthetic_data):
    """statements.csv carries only the OCR short name; ratios.csv the kiang title. Both must land on preschools.id."""
    import csv
    with open(synthetic_data / "statements.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "name", "fiscal_year", "bs_assets", "is_revenue"])
        w.writeheader()
        w.writerow({"code": "N01", "name": "乙", "fiscal_year": "112", "bs_assets": "100", "is_revenue": "50"})
        w.writerow({"code": "N99", "name": "不存在", "fiscal_year": "112", "bs_assets": "1", "is_revenue": "1"})
    with open(synthetic_data / "ratios.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "name", "title", "fiscal_year", "capacity", "人事費率"])
        w.writeheader()
        w.writerow({"code": "N01", "name": "乙", "title": "新北市乙非營利幼兒園（委託社團法人丙協會辦理）", "fiscal_year": "112", "capacity": "60", "人事費率": "0.6"})
    b, stats = build(tmp_path, synthetic_data)
    con = connect(b.db_path)
    assert con.execute("SELECT preschool_id FROM src_statements WHERE code='N01'").fetchone()[0] == "B"
    assert con.execute("SELECT preschool_id FROM src_statements WHERE code='N99'").fetchone()[0] is None
    assert con.execute("SELECT preschool_id FROM src_ratios").fetchone()[0] == "B"
    assert stats["finance_unlinked"] == {"src_statements": 1}
    # the detail-page join (SD §3 finance block) now returns a row
    assert con.execute("SELECT COUNT(*) FROM v_ratios r JOIN src_preschools p ON p.id = r.preschool_id").fetchone()[0] == 1

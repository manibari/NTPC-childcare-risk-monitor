"""財務燈號 rules (scripts/finance.py) + DB integration."""
import json

from conftest import preschool
from db import DBBuilder, connect
import finance


def row(code, fy, *, rev=10_000_000, exp=9_700_000, personnel=5_800_000, assets=10_000_000, liab=8_500_000,
        cur_assets=3_000_000, cur_liab=2_000_000, cash=4_500_000, payable=100_000, capacity=100, bs_ok=True, is_ok=True):
    surplus = rev - exp
    return {"code": code, "name": code, "title": None, "fiscal_year": fy, "capacity": capacity,
            "bs_ok": str(bs_ok), "is_ok": str(is_ok),
            "is_revenue": rev, "is_tuition": rev * 0.9, "is_expense": exp, "is_personnel": personnel,
            "bs_assets": assets, "bs_liab": liab, "bs_equity": assets - liab, "bs_other_payable": payable, "bs_cash": cash,
            "人事費率": personnel / exp, "餘絀率": surplus / rev, "流動比": cur_assets / cur_liab, "負債比": liab / assets,
            "現金月數": cash / (exp / 12), "每核定名額收入(千)": rev / capacity / 1000}


def test_healthy_school_is_green():
    flags = finance.flag_school([row("A", 111), row("A", 112, rev=10_500_000, exp=10_150_000), row("A", 113, rev=11_000_000, exp=10_600_000)])
    assert [f.level for f in flags] == ["綠", "綠", "綠"]
    assert finance.summarize(flags)["direction"] in ("平穩", "改善")


def test_insolvent_balance_sheet_is_red():
    f = finance.flag_school([row("A", 113, assets=10_000_000, liab=10_500_000)])[0]
    assert f.dims["資債"]["level"] == "紅" and f.level == "紅"
    assert any("資不抵債" in r for r in f.reasons)


def test_two_consecutive_deficit_years_escalate_to_red():
    flags = finance.flag_school([row("A", 111), row("A", 112, exp=10_400_000), row("A", 113, exp=10_500_000)])
    assert [f.dims["餘絀"]["level"] for f in flags] == ["綠", "黃", "紅"]


def test_deficit_streak_needs_meaningful_loss():
    # -0.5% is zero-surplus noise under the 委辦 contract, not a deficit
    f = finance.flag_school([row("A", 113, exp=10_050_000)])[0]
    assert f.dims["餘絀"]["level"] == "綠"


def test_cost_jump_requires_revenue_lag():
    base = row("A", 112)
    grow_both = row("A", 113, rev=12_000_000, exp=11_640_000, personnel=7_000_000)          # 擴班：同步成長
    grow_cost = row("A", 113, rev=10_200_000, exp=11_640_000, personnel=7_000_000)          # 成本跳升、收入沒跟上
    assert finance.flag_school([base, grow_both])[-1].dims["支出"]["level"] == "綠"
    assert finance.flag_school([base, grow_cost])[-1].dims["支出"]["level"] == "黃"


def test_peer_relative_seat_cost_growth():
    # Whole cohort +12% per-seat cost: nobody is flagged for it. One school +30%: flagged.
    cohort = []
    for i in range(6):
        c = f"S{i}"
        cohort += [row(c, 112), row(c, 113, rev=11_200_000, exp=10_864_000)]
    cohort += [row("X", 112), row("X", 113, rev=11_200_000, exp=12_610_000)]
    peer = finance.peer_baselines(cohort)
    assert abs(peer[113]["g_seat_cost"] - 0.12) < 0.01
    ok = finance.flag_school([r for r in cohort if r["code"] == "S0"], peer)[-1]
    bad = finance.flag_school([r for r in cohort if r["code"] == "X"], peer)[-1]
    assert not any("同儕" in r for r in ok.reasons)
    assert any("每名額支出成長" in r and "同儕" in r for r in bad.reasons)


def test_other_payable_jump_flags_balance():
    flags = finance.flag_school([row("A", 112, payable=50_000), row("A", 113, payable=1_200_000)])
    assert flags[-1].dims["資債"]["level"] == "黃"
    assert any("掛帳" in r for r in flags[-1].reasons)


def test_three_yellow_dimensions_make_red_overall():
    dims = {"收入": {"level": "黃"}, "支出": {"level": "黃"}, "資債": {"level": "黃"}, "餘絀": {"level": "綠"}}
    assert finance.overall(dims) == "紅"
    dims["資債"]["level"] = "綠"
    assert finance.overall(dims) == "黃"


def test_failed_identities_are_grey_and_not_scored():
    f = finance.flag_school([row("A", 113, bs_ok=False, is_ok=False, liab=99_000_000)])[0]
    assert f.level == "灰"


def test_flags_land_in_db_with_preschool_id(tmp_path, synthetic_data):
    import csv
    rows = [row("N01", 112), row("N01", 113, exp=10_500_000)]
    for r in rows:
        r["name"], r["title"] = "乙", "新北市乙非營利幼兒園（委託社團法人丙協會辦理）"
    with open(synthetic_data / "ratios.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    b = DBBuilder(tmp_path / "wd.sqlite", synthetic_data)
    stats = b.build()
    con = connect(b.db_path)
    assert stats["src_finance_flags"] == 2
    got = con.execute("SELECT preschool_id, fiscal_year, level, level_surplus, is_latest, direction, history FROM v_finance_flags ORDER BY fiscal_year").fetchall()
    assert got[0][:5] == ("B", 112, "綠", "綠", 0)
    assert got[1][:5] == ("B", 113, "黃", "黃", 1) and got[1][6] == "綠黃"
    reasons = json.loads(con.execute("SELECT reasons FROM src_finance_flags WHERE is_latest=1").fetchone()[0])
    assert any("餘絀" in r for r in reasons)

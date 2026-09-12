"""Per-school financial risk flags for nonprofit kindergartens (財務燈號).

Input: ratio rows per (school, fiscal_year) from compare_ratios.py — raw BS/IS
amounts plus ratios, 109–113 學年, 1–5 years per school.

Four dimensions, each with its own level 紅/黃/綠 and reasons, plus an overall:
  收入   — 招生不足、收入下滑、落後同儕
  支出   — 人事費率、成本跑贏收入、成本跳升、每名額成本漲幅高於同儕
  資債   — 負債比、流動比、現金月數、負債長得比資產快、掛帳（其他應付款）暴增、淨值連跌
  餘絀   — 本期虧損、連續虧損
Overall = 紅 if any dimension is 紅 or ≥ 3 dimensions are 黃; 黃 if any 黃; else 綠.
灰 = both statements failed the accounting identity (numbers unusable).

Rules-first, deterministic, no penalty data. This is an accounting-health
label, not a prediction of penalties (task_plan decision 13 / autoplan E4).
Level thresholds are FIXED constants calibrated once on the 2026-09 snapshot
(155 school-years, docs/exploration.md §5); they are not recomputed per rebuild
so the baseline cannot drift. Peer context (cohort median growth per year) IS
recomputed — it is what separates one school's cost rise from sector-wide
inflation/salary steps, which lifted every school's per-seat cost 109→113.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

LEVELS = ("灰", "綠", "黃", "紅")
DIMS = ("收入", "支出", "資債", "餘絀")
RATIO_KEYS = ("人事費率", "餘絀率", "流動比", "負債比", "現金月數", "每核定名額收入(千)")
RAW_KEYS = ("is_revenue", "is_tuition", "is_expense", "is_personnel", "bs_assets", "bs_liab", "bs_equity",
            "bs_other_payable", "bs_cash", "capacity")

# 收入
REV_PER_SEAT_LOW_K = 76       # p10 每核定名額收入(千) → 招生不足
REV_DROP_YEL = -0.10          # 收入單年 ≤ −10%
REV_DROP_RED = -0.20
REV_DECLINE_STEP = -0.03      # 連續 2 年每年 ≤ −3%
PEER_LAG_PP = 0.10            # 落後同儕中位數成長 ≥ 10 pp
# 支出
PERSONNEL_RATE_YEL = 0.70     # p95
COST_OUTRUN_PP = 0.02         # 支出成長 − 收入成長 ≥ 2 pp，連續 2 年
COST_JUMP = 0.15              # 總支出或人事費單年 ≥ +15%
JUMP_REVENUE_GAP = 0.10       # …且領先收入成長 ≥ 10 pp（同步成長 = 擴班，不是風險）
RECLASS_FLOOR = 0.45          # 前一年人事費率 < p5 視為科目搬動（FY112 有 8 園人事費被搬進業務發展費／其他支出），不比人事費跳升
PEER_EXCESS_PP = 0.10         # 每名額支出成長高於同儕中位數 ≥ 10 pp
# 資債
DEBT_RED, DEBT_YEL = 1.00, 0.95          # 負債比
CURRENT_RED, CURRENT_YEL = 1.00, 1.20    # 流動比
CASH_MONTHS_YEL = 4.0
CURRENT_JUMP = 0.35                      # 流動比單年掉 ≥ 35%
DEBT_STEP = 0.02                         # 負債比連續 2 年每年 ≥ +0.02
LIAB_VS_ASSET_PP = 0.10                  # 負債成長 − 資產成長 ≥ 10 pp
PAYABLE_JUMP_X, PAYABLE_MIN_OF_REV = 2.0, 0.05   # 其他應付款 ≥ 2× 前年且 ≥ 收入 5%
# 餘絀
DEFICIT_RATE = -0.02          # 餘絀率低於此才算虧損
DEFICIT_RED_YEARS = 2
TREND_YEARS = 2


@dataclass
class YearFlag:
    fiscal_year: int
    level: str
    dims: dict = field(default_factory=dict)      # {dim: {"level": str, "reasons": [str]}}
    metrics: dict = field(default_factory=dict)

    @property
    def reasons(self) -> list[str]:
        return [f"{d}：{r}" for d in DIMS for r in self.dims.get(d, {}).get("reasons", [])]


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true")


def _growth(prev: dict | None, cur: dict, key: str):
    if prev is None:
        return None
    a, b = prev.get(key), cur.get(key)
    return (b - a) / a if (a and b is not None and a > 0) else None


def metrics(row: dict) -> dict:
    m = {k: _f(row.get(k)) for k in RATIO_KEYS + RAW_KEYS}
    m["fiscal_year"] = int(float(row["fiscal_year"]))
    m["bs_ok"], m["is_ok"] = _truthy(row.get("bs_ok")), _truthy(row.get("is_ok"))
    cap, exp = m["capacity"], m["is_expense"]
    m["每核定名額支出(千)"] = exp / cap / 1000 if (cap and exp is not None) else None
    return m


def peer_baselines(all_rows: list[dict]) -> dict:
    """{fiscal_year: {"g_rev": median revenue growth, "g_seat_cost": median per-seat cost growth}} across the cohort."""
    by_school: dict[str, list[dict]] = {}
    for r in all_rows:
        by_school.setdefault(r["code"], []).append(metrics(r))
    acc: dict[int, dict[str, list[float]]] = {}
    for ms in by_school.values():
        ms.sort(key=lambda m: m["fiscal_year"])
        for a, b in zip(ms, ms[1:]):
            if b["fiscal_year"] != a["fiscal_year"] + 1 or not (a["is_ok"] and b["is_ok"]):
                continue
            g = _growth(a, b, "is_revenue")
            gc = _growth(a, b, "每核定名額支出(千)")
            d = acc.setdefault(b["fiscal_year"], {"g_rev": [], "g_seat_cost": []})
            if g is not None:
                d["g_rev"].append(g)
            if gc is not None:
                d["g_seat_cost"].append(gc)
    return {fy: {k: statistics.median(v) for k, v in d.items() if len(v) >= 5} for fy, d in acc.items()}


def _lvl(red: list, yellow: list) -> str:
    return "紅" if red else "黃" if yellow else "綠"


def _dim_revenue(m, prev, hist, peer):
    red, yel = [], []
    if not m["is_ok"]:
        return {"level": "灰", "reasons": ["收支餘絀表不可用"]}
    s = m["每核定名額收入(千)"]
    if s is not None and s < REV_PER_SEAT_LOW_K:
        yel.append(f"每核定名額收入 {s:.0f} 千 < {REV_PER_SEAT_LOW_K}（招生不足）")
    g = _growth(prev, m, "is_revenue")
    if g is not None and g <= REV_DROP_RED:
        red.append(f"收入單年 {g:+.0%}")
    elif g is not None and g <= REV_DROP_YEL:
        yel.append(f"收入單年 {g:+.0%}")
    if len(hist) >= TREND_YEARS:
        gs = [_growth(a, b, "is_revenue") for a, b in zip(hist[-TREND_YEARS:] + [m], hist[-TREND_YEARS + 1:] + [m])][:TREND_YEARS]
        gs = [_growth(a, b, "is_revenue") for a, b in zip((hist[-TREND_YEARS:] + [m])[:-1], (hist[-TREND_YEARS:] + [m])[1:])]
        if all(x is not None and x <= REV_DECLINE_STEP for x in gs):
            yel.append(f"收入連續 {TREND_YEARS} 年下滑（" + "、".join(f"{x:+.0%}" for x in gs) + "）")
    pg = peer.get(m["fiscal_year"], {}).get("g_rev")
    if g is not None and pg is not None and pg - g >= PEER_LAG_PP:
        yel.append(f"收入成長 {g:+.0%} 落後同儕中位數 {pg:+.0%}")
    return {"level": _lvl(red, yel), "reasons": red + yel, "growth": g}


def _dim_cost(m, prev, hist, peer):
    red, yel = [], []
    if not m["is_ok"]:
        return {"level": "灰", "reasons": ["收支餘絀表不可用"]}
    if m["人事費率"] is not None and m["人事費率"] >= PERSONNEL_RATE_YEL:
        yel.append(f"人事費率 {m['人事費率']:.2f} ≥ {PERSONNEL_RATE_YEL:.2f}")
    g_rev, g_exp, g_per = _growth(prev, m, "is_revenue"), _growth(prev, m, "is_expense"), _growth(prev, m, "is_personnel")
    if g_exp is not None and g_rev is not None and g_exp >= COST_JUMP and g_exp - g_rev >= JUMP_REVENUE_GAP:
        yel.append(f"總支出單年 {g_exp:+.0%}，收入僅 {g_rev:+.0%}")
    elif (g_per is not None and g_rev is not None and g_per >= COST_JUMP and g_per - g_rev >= JUMP_REVENUE_GAP
          and (prev.get("人事費率") or 0) >= RECLASS_FLOOR):
        yel.append(f"人事費單年 {g_per:+.0%}，收入僅 {g_rev:+.0%}")
    if len(hist) >= TREND_YEARS:
        series = hist[-TREND_YEARS:] + [m]
        gaps = []
        for a, b in zip(series, series[1:]):
            ge, gr = _growth(a, b, "is_expense"), _growth(a, b, "is_revenue")
            gaps.append(None if ge is None or gr is None else ge - gr)
        if all(x is not None and x >= COST_OUTRUN_PP for x in gaps):
            yel.append(f"支出成長連續 {TREND_YEARS} 年快過收入（差 " + "、".join(f"{x:+.0%}" for x in gaps) + "）")
    gc = _growth(prev, m, "每核定名額支出(千)")
    pc = peer.get(m["fiscal_year"], {}).get("g_seat_cost")
    if gc is not None and pc is not None and gc - pc >= PEER_EXCESS_PP:
        yel.append(f"每名額支出成長 {gc:+.0%}，同儕中位數 {pc:+.0%}")
    return {"level": _lvl(red, yel), "reasons": red + yel, "growth": g_exp}


def _dim_balance(m, prev, hist):
    red, yel = [], []
    if not m["bs_ok"]:
        return {"level": "灰", "reasons": ["資產負債表不可用"]}
    d, c = m["負債比"], m["流動比"]
    if d is not None and d >= DEBT_RED:
        red.append(f"負債比 {d:.2f} ≥ {DEBT_RED:.2f}（資不抵債）")
    elif d is not None and d >= DEBT_YEL:
        yel.append(f"負債比 {d:.2f} ≥ {DEBT_YEL:.2f}")
    if c is not None and c < CURRENT_RED:
        red.append(f"流動比 {c:.2f} < {CURRENT_RED:.2f}")
    elif c is not None and c < CURRENT_YEL:
        yel.append(f"流動比 {c:.2f} < {CURRENT_YEL:.2f}")
    if m["is_ok"] and m["現金月數"] is not None and m["現金月數"] < CASH_MONTHS_YEL:
        yel.append(f"現金月數 {m['現金月數']:.1f} < {CASH_MONTHS_YEL:.0f}")
    if prev is not None:
        pc = prev.get("流動比")
        if pc and c is not None and (pc - c) / pc >= CURRENT_JUMP:
            yel.append(f"流動比單年 {pc:.2f} → {c:.2f}（{-(pc - c) / pc:+.0%}）")
        gl, ga = _growth(prev, m, "bs_liab"), _growth(prev, m, "bs_assets")
        if gl is not None and ga is not None and gl - ga >= LIAB_VS_ASSET_PP:
            yel.append(f"負債 {gl:+.0%} 長得比資產 {ga:+.0%} 快")
        op, pp_, rev = m["bs_other_payable"], prev.get("bs_other_payable"), m["is_revenue"]
        if op is not None and pp_ is not None and rev and op >= PAYABLE_JUMP_X * max(pp_, 1) and op >= PAYABLE_MIN_OF_REV * rev:
            yel.append(f"其他應付款 {pp_ / 1e3:,.0f} → {op / 1e3:,.0f} 千（占收入 {op / rev:.0%}，掛帳）")
    if len(hist) >= TREND_YEARS:
        series = hist[-TREND_YEARS:] + [m]
        ds = [x.get("負債比") for x in series]
        if all(v is not None for v in ds) and all(b - a >= DEBT_STEP for a, b in zip(ds, ds[1:])):
            yel.append(f"負債比連續 {TREND_YEARS} 年上升 {ds[0]:.2f} → {ds[-1]:.2f}")
        eq = [x.get("bs_equity") for x in series]
        if all(v is not None for v in eq) and all(b < a for a, b in zip(eq, eq[1:])):
            yel.append(f"淨值（餘絀總額）連續 {TREND_YEARS} 年下降 {eq[0] / 1e3:,.0f} → {eq[-1] / 1e3:,.0f} 千")
    return {"level": _lvl(red, yel), "reasons": red + yel}


def _dim_surplus(m, streak):
    if not m["is_ok"]:
        return {"level": "灰", "reasons": ["收支餘絀表不可用"]}
    red, yel = [], []
    if streak >= DEFICIT_RED_YEARS:
        red.append(f"連續 {streak} 年餘絀率 < {DEFICIT_RATE:.0%}（本期 {m['餘絀率']:.1%}）")
    elif streak == 1:
        yel.append(f"本期餘絀率 {m['餘絀率']:.1%} < {DEFICIT_RATE:.0%}")
    return {"level": _lvl(red, yel), "reasons": red + yel}


def overall(dims: dict) -> str:
    lv = [d["level"] for d in dims.values()]
    if all(x == "灰" for x in lv):
        return "灰"
    if "紅" in lv or lv.count("黃") >= 3:
        return "紅"
    return "黃" if "黃" in lv else "綠"


def flag_school(rows: list[dict], peer: dict | None = None) -> list[YearFlag]:
    """All years of one school, oldest first. Trend rules only look backwards (no look-ahead)."""
    peer = peer or {}
    ms = sorted((metrics(r) for r in rows), key=lambda m: m["fiscal_year"])
    out, hist, streak = [], [], 0
    for m in ms:
        if not m["bs_ok"] and not m["is_ok"]:
            out.append(YearFlag(m["fiscal_year"], "灰", {d: {"level": "灰", "reasons": ["兩張報表恆等式皆未通過"]} for d in DIMS}, m))
            continue
        prev = hist[-1] if hist and hist[-1]["fiscal_year"] == m["fiscal_year"] - 1 else None
        s = m["餘絀率"]
        streak = streak + 1 if (m["is_ok"] and s is not None and s < DEFICIT_RATE) else 0
        dims = {
            "收入": _dim_revenue(m, prev, hist, peer),
            "支出": _dim_cost(m, prev, hist, peer),
            "資債": _dim_balance(m, prev, hist),
            "餘絀": _dim_surplus(m, streak),
        }
        out.append(YearFlag(m["fiscal_year"], overall(dims), dims, m))
        hist.append(m)
    return out


def summarize(flags: list[YearFlag]) -> dict:
    """School-level: latest year's levels per dimension, history string, deterioration direction."""
    latest = flags[-1] if flags else None
    usable = [f for f in flags if f.level != "灰"]
    direction = "資料不足"
    if len(usable) >= 2:
        prior, last = [f.metrics for f in usable[-3:-1]], usable[-1].metrics
        worse_is_higher = {"流動比": False, "負債比": True, "人事費率": True, "餘絀率": False,
                           "每核定名額收入(千)": False, "現金月數": False}
        worse = better = 0
        for k, hi in worse_is_higher.items():
            vals = [p.get(k) for p in prior if p.get(k) is not None]
            b = last.get(k)
            if not vals or b is None:
                continue
            a = sum(vals) / len(vals)
            if abs(b - a) <= max(abs(a) * 0.05, 0.01):
                continue
            worse += (b > a) == hi
            better += (b > a) != hi
        direction = "惡化" if worse >= 2 and worse > better else "改善" if better >= 2 and better > worse else "平穩"
    return {
        "level": latest.level if latest else "灰",
        "fiscal_year": latest.fiscal_year if latest else None,
        "dims": {d: latest.dims.get(d, {}).get("level", "灰") for d in DIMS} if latest else {},
        "direction": direction,
        "history": "".join(f.level for f in flags),
        "n_years": len(flags),
        "n_red": sum(f.level == "紅" for f in flags),
        "reasons": latest.reasons if latest else [],
    }

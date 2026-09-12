"""Per-school financial risk flag for nonprofit kindergartens (財務燈號).

Input: ratio rows per (school, fiscal_year) as produced by compare_ratios.py
(109–113 學年, 3–5 years per school). Output per year: level 紅/黃/綠/灰 + the
rules that fired; per school: latest level, trend direction, history string.

Rules-first and deterministic; no penalty data involved. This is an
*accounting health* label, not a prediction of penalties (task_plan decision
13 / autoplan E4). Two rule families:

  snapshot  — this year's numbers against fixed thresholds
  trend     — this year against the school's own previous years
              (consecutive deterioration, one-year jump)

Thresholds are FIXED constants calibrated once on the 2026-09 snapshot
(155 school-years; docs/exploration.md §5). Not recomputed per rebuild on
purpose: a moving baseline can never raise an alarm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

LEVELS = ("灰", "綠", "黃", "紅")
KEYS = ("人事費率", "餘絀率", "流動比", "負債比", "現金月數", "每核定名額收入(千)")
RAW = ("is_revenue", "is_expense", "is_personnel", "is_material", "capacity")   # for cost trend

# --- snapshot, red: accounting facts, not peer-relative ------------------------
RED_DEBT_RATIO = 1.00        # 負債比 ≥ 1 → 資不抵債
RED_CURRENT_RATIO = 1.00     # 流動比 < 1 → 流動負債蓋不住
RED_DEFICIT_YEARS = 2        # 連續 N 年本期餘絀 < 0
# --- snapshot, yellow: tail of the peer distribution (p5 / p10 / p95) ----------
YEL_DEBT_RATIO = 0.95
YEL_CURRENT_RATIO = 1.20
YEL_CASH_MONTHS = 4.0
YEL_PERSONNEL_RATE = 0.70    # 委辦合約下人事費率偏高 = 招生不足或超編
YEL_REVENUE_PER_SEAT_K = 76  # 每核定名額收入(千) → 招生不足
YEL_DEFICIT_RATE = -0.02     # 單年餘絀率低於此才算「虧損」（-0.5% 的零餘絀不算）
# --- trend --------------------------------------------------------------------
TREND_YEARS = 2              # 連續 N 年往壞的方向走
TREND_MIN_STEP = {"流動比": -0.05, "負債比": +0.02, "人事費率": +0.02, "每核定名額收入(千)": -5.0,
                  "每核定名額支出(千)": +5.0}  # 每年至少變這麼多才算「走壞」
JUMP_CURRENT_RATIO = 0.35    # 流動比單年掉 ≥ 35%
JUMP_DEBT_RATIO = 0.10       # 負債比單年升 ≥ 0.10
JUMP_REVENUE_PER_SEAT = 0.20 # 每核定名額收入單年掉 ≥ 20%
# --- cost trend ---------------------------------------------------------------
COST_OUTRUN_PP = 0.02        # 支出成長率 − 收入成長率 ≥ 2 pp，連續 TREND_YEARS 年 → 成本跑贏收入
COST_JUMP = 0.15             # 總支出單年成長 ≥ 15%
PERSONNEL_JUMP = 0.15        # 人事費單年成長 ≥ 15%
JUMP_REVENUE_GAP = 0.10      # …且收入成長落後 ≥ 10 pp 才算成本問題（同步成長 = 擴班，不是風險）
WORSE_IS_HIGHER = {"人事費率": True, "餘絀率": False, "流動比": False, "負債比": True, "現金月數": False,
                   "每核定名額收入(千)": False, "每核定名額支出(千)": True}


@dataclass
class YearFlag:
    fiscal_year: int
    level: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    snapshot_level: str = "灰"


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true")


def _metrics(row: dict) -> dict:
    m = {k: _f(row.get(k)) for k in KEYS}
    m.update({k: _f(row.get(k)) for k in RAW})
    cap, exp = m["capacity"], m["is_expense"]
    m["每核定名額支出(千)"] = exp / cap / 1000 if (cap and exp is not None) else None
    return m


def _growth(prev: dict, cur: dict, key: str) -> float | None:
    a, b = prev.get(key), cur.get(key)
    return (b - a) / a if (a and b is not None and a > 0) else None


def _snapshot(m: dict, bs_ok: bool, is_ok: bool, deficit_streak: int) -> tuple[list[str], list[str]]:
    red, yellow = [], []
    if bs_ok:
        d, c = m["負債比"], m["流動比"]
        if d is not None and d >= RED_DEBT_RATIO:
            red.append(f"負債比 {d:.2f} ≥ {RED_DEBT_RATIO:.2f}（資不抵債）")
        elif d is not None and d >= YEL_DEBT_RATIO:
            yellow.append(f"負債比 {d:.2f} ≥ {YEL_DEBT_RATIO:.2f}")
        if c is not None and c < RED_CURRENT_RATIO:
            red.append(f"流動比 {c:.2f} < {RED_CURRENT_RATIO:.2f}")
        elif c is not None and c < YEL_CURRENT_RATIO:
            yellow.append(f"流動比 {c:.2f} < {YEL_CURRENT_RATIO:.2f}")
    if is_ok:
        if deficit_streak >= RED_DEFICIT_YEARS:
            red.append(f"連續 {deficit_streak} 年餘絀率 < {YEL_DEFICIT_RATE:.0%}")
        elif deficit_streak == 1:
            yellow.append(f"本期餘絀率 {m['餘絀率']:.1%} < {YEL_DEFICIT_RATE:.0%}")
        p, s = m["人事費率"], m["每核定名額收入(千)"]
        if p is not None and p >= YEL_PERSONNEL_RATE:
            yellow.append(f"人事費率 {p:.2f} ≥ {YEL_PERSONNEL_RATE:.2f}")
        if s is not None and s < YEL_REVENUE_PER_SEAT_K:
            yellow.append(f"每核定名額收入 {s:.0f} 千 < {YEL_REVENUE_PER_SEAT_K}（招生不足）")
    if bs_ok and is_ok and m["現金月數"] is not None and m["現金月數"] < YEL_CASH_MONTHS:
        yellow.append(f"現金月數 {m['現金月數']:.1f} < {YEL_CASH_MONTHS:.0f}")
    return red, yellow


def _trend(hist: list[dict], m: dict) -> tuple[list[str], list[str]]:
    """hist = metrics of previous years (oldest first, only years whose statements passed). Returns (worsening, jumps)."""
    worsening, jumps = [], []
    if not hist:
        return worsening, jumps
    prev = hist[-1]
    # one-year jumps
    if prev["流動比"] and m["流動比"] is not None and prev["流動比"] > 0 and (prev["流動比"] - m["流動比"]) / prev["流動比"] >= JUMP_CURRENT_RATIO:
        jumps.append(f"流動比單年 {prev['流動比']:.2f} → {m['流動比']:.2f}（-{(prev['流動比'] - m['流動比']) / prev['流動比']:.0%}）")
    if prev["負債比"] is not None and m["負債比"] is not None and m["負債比"] - prev["負債比"] >= JUMP_DEBT_RATIO:
        jumps.append(f"負債比單年 {prev['負債比']:.2f} → {m['負債比']:.2f}")
    if prev["每核定名額收入(千)"] and m["每核定名額收入(千)"] is not None and (prev["每核定名額收入(千)"] - m["每核定名額收入(千)"]) / prev["每核定名額收入(千)"] >= JUMP_REVENUE_PER_SEAT:
        jumps.append(f"每核定名額收入單年 {prev['每核定名額收入(千)']:.0f} → {m['每核定名額收入(千)']:.0f} 千")
    # cost jumps: expense (or personnel) up ≥ 15% in one year while revenue lags ≥ 10 pp
    g_rev, g_exp, g_per = _growth(prev, m, "is_revenue"), _growth(prev, m, "is_expense"), _growth(prev, m, "is_personnel")
    if g_exp is not None and g_rev is not None and g_exp >= COST_JUMP and g_exp - g_rev >= JUMP_REVENUE_GAP:
        jumps.append(f"總支出單年 +{g_exp:.0%}，收入僅 {g_rev:+.0%}")
    elif g_per is not None and g_rev is not None and g_per >= PERSONNEL_JUMP and g_per - g_rev >= JUMP_REVENUE_GAP:
        jumps.append(f"人事費單年 +{g_per:.0%}，收入僅 {g_rev:+.0%}")
    # cost outrunning revenue for TREND_YEARS consecutive years
    if len(hist) >= TREND_YEARS:
        series = hist[-TREND_YEARS:] + [m]
        gaps = []
        for a, b in zip(series, series[1:]):
            ge, gr = _growth(a, b, "is_expense"), _growth(a, b, "is_revenue")
            gaps.append(None if ge is None or gr is None else ge - gr)
        if all(g is not None and g >= COST_OUTRUN_PP for g in gaps):
            worsening.append(f"支出成長連續 {TREND_YEARS} 年快過收入（差 " + "、".join(f"{g:+.0%}" for g in gaps) + "）")
    # consecutive deterioration over TREND_YEARS steps
    if len(hist) >= TREND_YEARS:
        series = [h for h in hist[-TREND_YEARS:]] + [m]
        for k, step in TREND_MIN_STEP.items():
            vals = [x[k] for x in series]
            if any(v is None for v in vals):
                continue
            diffs = [b - a for a, b in zip(vals, vals[1:])]
            if all((d >= step) if step > 0 else (d <= step) for d in diffs):
                worsening.append(f"{k} 連續 {TREND_YEARS} 年走壞 {vals[0]:.2f} → {vals[-1]:.2f}")
    return worsening, jumps


def flag_school(rows: list[dict]) -> list[YearFlag]:
    """All years of one school, oldest first. Trend rules see only earlier years (no look-ahead)."""
    rows = sorted(rows, key=lambda r: int(float(r["fiscal_year"])))
    out, hist, streak = [], [], 0
    for r in rows:
        fy = int(float(r["fiscal_year"]))
        m = _metrics(r)
        bs_ok, is_ok = _truthy(r.get("bs_ok")), _truthy(r.get("is_ok"))
        if not bs_ok and not is_ok:
            out.append(YearFlag(fy, "灰", ["兩張報表恆等式皆未通過，數字不可用"], m, "灰"))
            continue
        s = m["餘絀率"]
        streak = streak + 1 if (is_ok and s is not None and s < YEL_DEFICIT_RATE) else 0
        red, yellow = _snapshot(m, bs_ok, is_ok, streak)
        snap = "紅" if red else "黃" if yellow else "綠"
        worsening, jumps = _trend(hist, m) if (bs_ok and is_ok) else ([], [])
        level = snap
        if level == "黃" and worsening:
            level = "紅"
            worsening = [w + "（黃 + 連續惡化 → 紅）" for w in worsening]
        elif level == "綠" and (worsening or jumps):
            level = "黃"
        reasons = red + yellow + worsening + jumps
        if not (bs_ok and is_ok):
            reasons.append(f"僅{'資產負債表' if bs_ok else '收支餘絀表'}可用")
        out.append(YearFlag(fy, level, reasons, m, snap))
        if bs_ok and is_ok:
            hist.append(m)
    return out


def worst(levels: list[str]) -> str:
    return max(levels, key=LEVELS.index) if levels else "灰"


def summarize(flags: list[YearFlag]) -> dict:
    """School-level summary: latest year's level, direction over the available years, history string."""
    usable = [f for f in flags if f.level != "灰"]
    latest = flags[-1] if flags else None
    direction = "資料不足"
    if len(usable) >= 2:
        prior, last = [f.metrics for f in usable[-3:-1]], usable[-1].metrics
        worse = better = 0
        for k in ("流動比", "負債比", "人事費率", "餘絀率", "每核定名額收入(千)", "每核定名額支出(千)"):
            vals = [p.get(k) for p in prior if p.get(k) is not None]
            b = last.get(k)
            if not vals or b is None:
                continue
            a = sum(vals) / len(vals)
            tol = max(abs(a) * 0.05, 0.01)
            if abs(b - a) <= tol:
                continue
            worse += (b > a) == WORSE_IS_HIGHER[k]
            better += (b > a) != WORSE_IS_HIGHER[k]
        direction = "惡化" if worse >= 2 and worse > better else "改善" if better >= 2 and better > worse else "平穩"
    return {
        "level": latest.level if latest else "灰",
        "fiscal_year": latest.fiscal_year if latest else None,
        "direction": direction,
        "history": "".join(f.level for f in flags),
        "n_years": len(flags),
        "n_red": sum(f.level == "紅" for f in flags),
        "reasons": latest.reasons if latest else [],
    }

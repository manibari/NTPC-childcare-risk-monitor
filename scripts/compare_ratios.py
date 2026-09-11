"""Compare financial ratios: penalised vs non-penalised nonprofit kindergartens.

Reads data/statements.csv (parser output), reconciles each fiscal year across
the two reports that carry it, computes ratios, and writes
data/ratio_comparison.md + data/ratios.csv.
"""
import json
import pathlib
import re

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

ROOT = pathlib.Path(__file__).resolve().parent.parent
d = pd.read_csv(ROOT / "data/statements.csv")
fields = [c for c in d.columns if c.startswith(("bs_", "is_"))]

# --- reconcile duplicates: same (code, fiscal_year) from two reports ---
def reconcile(g: pd.DataFrame) -> pd.Series:
    out = {"code": g.code.iloc[0], "name": g["name"].iloc[0], "fiscal_year": g.fiscal_year.iloc[0], "n_sources": len(g)}
    for f in fields:
        vals = g[f].dropna().unique()
        if len(vals) == 1:
            out[f] = vals[0]
        elif len(vals) == 0:
            out[f] = np.nan
        else:  # disagreement -> pick the one that satisfies identities, else NaN
            out[f] = np.nan
            for _, r in g.iterrows():
                ok_bs = pd.notna(r.bs_assets) and abs(r.bs_assets - (r.bs_liab + r.bs_equity)) <= 1
                ok_is = pd.notna(r.is_revenue) and pd.notna(r.is_expense) and abs(r.is_revenue - r.is_expense - r.is_surplus) <= 1
                if (f.startswith("bs_") and ok_bs) or (f.startswith("is_") and ok_is):
                    out[f] = r[f]
                    break
    return pd.Series(out)

r = d.groupby(["code", "fiscal_year"]).apply(reconcile).reset_index(drop=True)
r["bs_ok"] = (r.bs_assets - (r.bs_liab + r.bs_equity)).abs() <= 1
r["is_ok"] = (r.is_revenue - r.is_expense - r.is_surplus).abs() <= 1
# drop IS fields where identity fails (digit misread somewhere on that page)
for f in [c for c in fields if c.startswith("is_")]:
    r.loc[~r.is_ok, f] = np.nan
for f in [c for c in fields if c.startswith("bs_")]:
    r.loc[~r.bs_ok, f] = np.nan

# --- join penalties + capacity ---
pen = json.load(open(ROOT / "data/ntpc_penalties_by_school.json"))
master = {f["properties"]["title"]: f["properties"] for f in json.load(open(ROOT / "data/kiang_preschools.json"))["features"]}
def lookup(short):
    for t, p in master.items():
        if p["city"] == "新北市" and p["type"] == "非營利" and (t.startswith("新北市" + short) or t.startswith("新北市政府" + short)):
            return t
    return None
r["title"] = r["name"].map(lookup)
pen_by_title = {v["title"]: v["penalties"] for v in pen.values()}
r["n_penalty"] = r.title.map(lambda t: len(pen_by_title.get(t, [])))
r["penalised"] = r.n_penalty > 0
r["capacity"] = r.title.map(lambda t: pd.to_numeric(str(master[t]["count_approved"]).replace(",", ""), errors="coerce") if t in master else np.nan)
r["first_penalty_year"] = r.title.map(lambda t: min((int(p["date"][:4]) for p in pen_by_title.get(t, [])), default=np.nan))
# fiscal_year 112 = 2023/8–2024/7 ; "before" = fiscal year ends before first penalty year
r["pre_penalty"] = r.penalised & ((r.fiscal_year + 1911 + 1) <= r.first_penalty_year)

# --- ratios ---
r["人事費率"] = r.is_personnel / r.is_expense
r["餘絀率"] = r.is_surplus / r.is_revenue
r["業務發展費率"] = r.is_dev_expense.fillna(0) / r.is_expense
r["材料費率"] = r.is_material / r.is_expense
r["修繕維護率"] = (r.is_maint.fillna(0) + r.is_repair.fillna(0)) / r.is_expense
r["其他收入占比"] = r.is_other_income.fillna(0) / r.is_revenue
r["流動比"] = r.bs_cur_assets / r.bs_cur_liab
r["負債比"] = r.bs_liab / r.bs_assets
r["現金月數"] = r.bs_cash / (r.is_expense / 12)
r["預收款占收入"] = r.bs_unearned / r.is_revenue
r["發展準備金占資產"] = r.bs_dev_reserve.fillna(0) / r.bs_assets
r["每核定名額收入(千)"] = r.is_revenue / r.capacity / 1000
r["每核定名額人事費(千)"] = r.is_personnel / r.capacity / 1000
ratios = ["人事費率", "餘絀率", "業務發展費率", "材料費率", "修繕維護率", "其他收入占比", "流動比", "負債比", "現金月數", "預收款占收入", "發展準備金占資產", "每核定名額收入(千)", "每核定名額人事費(千)"]
r.to_csv(ROOT / "data/ratios.csv", index=False)

# --- per-school mean over years, then group comparison ---
per = r.groupby(["code", "name", "penalised", "n_penalty"])[ratios].mean().reset_index()
lines = ["# 財務比率：被裁罰 vs 未裁罰非營利園", "",
         f"園所 {per.code.nunique()}，園-年 {len(r)}（BS 恆等式通過 {r.bs_ok.sum()}，IS 通過 {r.is_ok.sum()}）；",
         f"被裁罰 {per.penalised.sum()} 園、未裁罰 {(~per.penalised).sum()} 園。每園先對年度取平均，再比兩組中位數；p 為 Mann-Whitney。", "",
         "| 比率 | 被裁罰 中位數 | 未裁罰 中位數 | 差 | p |", "|---|---|---|---|---|"]
for k in ratios:
    a = per.loc[per.penalised, k].dropna(); b = per.loc[~per.penalised, k].dropna()
    if len(a) < 3 or len(b) < 3:
        continue
    p = mannwhitneyu(a, b).pvalue
    lines.append(f"| {k} | {a.median():.2f} | {b.median():.2f} | {a.median()-b.median():+.2f} | {p:.2f} |")
# pre-penalty years only
pre = r[r.pre_penalty].groupby("code")[ratios].mean(); non = r[~r.penalised].groupby("code")[ratios].mean()
lines += ["", "## 只看裁罰「之前」的年度（事前訊號）", "", f"被裁罰園有裁罰前年度資料者 {len(pre)} 園。", "",
          "| 比率 | 裁罰前 中位數 | 未裁罰 中位數 | 差 | p |", "|---|---|---|---|---|"]
for k in ratios:
    a = pre[k].dropna(); b = non[k].dropna()
    if len(a) < 3 or len(b) < 3:
        continue
    p = mannwhitneyu(a, b).pvalue
    lines.append(f"| {k} | {a.median():.2f} | {b.median():.2f} | {a.median()-b.median():+.2f} | {p:.2f} |")
lines += ["", "## 各被裁罰園（年度平均）", "", "| 園 | 裁罰筆數 | " + " | ".join(ratios[:9]) + " |", "|---" * 11 + "|"]
for _, x in per[per.penalised].sort_values("n_penalty", ascending=False).iterrows():
    lines.append(f"| {x['name']} | {x.n_penalty} | " + " | ".join(f"{x[k]:.2f}" if pd.notna(x[k]) else "–" for k in ratios[:9]) + " |")
(ROOT / "data/ratio_comparison.md").write_text("\n".join(lines))
print("\n".join(lines))

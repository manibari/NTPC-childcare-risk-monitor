"""Parse balance sheet + income statement figures out of data/ocr/*.jsonl.

Output: data/statements.csv, one row per (school, fiscal_year, source_report).
Column x-positions come from the header tokens on each page, so OCR garbling
in titles does not matter; only the account labels and numbers must be read.
"""
import csv
import glob
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OCR = ROOT / "data/ocr"

BS_ITEMS = {
    "cash": "現金及銀行存款", "cur_assets": "流動資產合計", "assets": "資產總額",
    "cur_liab": "流動負債合計", "liab": "負債總額", "unearned": "預收款項",
    "dev_reserve": "業務發展準備金", "acc_surplus": "累積餘絀", "period_surplus": "本期餘絀",
    "equity": "餘絀總額", "other_payable": "其他應付款",
}
IS_ITEMS = {
    "tuition": "教保費收入", "revenue": "收入合計", "personnel": "人事費", "operating": "業務費",
    "material": "材料費", "maint": "維護費", "repair": "修繕購置費", "admin": "行政管理費",
    "dev_expense": "業務發展費", "expense": "支出合計", "surplus": "本期稅後餘絀",
    "other_income": "其他收入", "extended_income": "延長照顧服務收入淨額",
    "misc": "雜支", "extended_expense": "延長照顧服務支出", "other_expense": "其他支出", "interest_income": "利息收入",
}
NUM = re.compile(r"^[\$＄]?\s*[\(（]?\s*[\$＄]?\s*-?[\d][\d,\.，]*\s*[\)）]?$")


def to_num(tok: str):
    """Amount token -> int. Thousands separators are noise (OCR mixes , . ，);
    parentheses (half or full width) or leading minus mean negative."""
    t = tok.strip()
    if not NUM.match(t):
        return None
    neg = any(c in t for c in "(（") or t.lstrip("$＄ ").startswith("-")
    digits = re.sub(r"\D", "", t)
    if not digits:
        return None
    v = int(digits)
    return -v if neg else v


def similar(a: str, b: str) -> bool:
    """Label match tolerant to 1 garbled char (OCR under red stamps)."""
    a = a.replace(" ", "")
    if b in a:
        return True
    if len(a) < len(b) - 1:
        return False
    for i in range(len(a) - len(b) + 2):
        w = a[i:i + len(b)]
        if len(w) == len(b) and sum(x != y for x, y in zip(w, b)) <= 1:
            return True
    return False


def rows_of(lines: list[dict], tol=0.006):
    """Group OCR lines into visual rows by y."""
    rows, cur = [], []
    for ln in sorted(lines, key=lambda r: r["y"]):
        if cur and abs(ln["y"] - cur[-1]["y"]) > tol:
            rows.append(sorted(cur, key=lambda r: r["x"]))
            cur = []
        cur.append(ln)
    if cur:
        rows.append(sorted(cur, key=lambda r: r["x"]))
    return rows


def page_kind(lines):
    txt = "".join(l["text"] for l in lines)
    if similar(txt, "現金及銀行存款") and similar(txt, "負債總額"):
        return "BS"
    if similar(txt, "教保費收入") and similar(txt, "支出合計"):
        return "IS"
    return None


def col_center(lines, label):
    for l in lines:
        if label in l["text"].replace(" ", ""):
            return l["x"] + l["w"] / 2
    return None


def extract(lines, items, col_x):
    """For each item label, take the numeric token whose center is nearest col_x."""
    out = {}
    for row in rows_of(lines):
        label = "".join(t["text"] for t in row if not NUM.match(t["text"].strip()))
        nums = [(t["x"] + t["w"] / 2, to_num(t["text"].strip())) for t in row if NUM.match(t["text"].strip())]
        nums = [(x, v) for x, v in nums if v is not None]
        if not nums:
            continue
        for key, name in items.items():
            if key in out or not similar(label, name):
                continue
            # 累積餘絀 vs 本期餘絀 vs 餘絀總額 share chars; require exact-ish start
            x, v = min(nums, key=lambda p: abs(p[0] - col_x))
            if abs(x - col_x) < 0.08:
                out[key] = v
    return out


def parse_file(path: pathlib.Path):
    m = re.match(r"(N\d+)(.+?)_(\d+)學年度", path.stem)
    code, name, year = m.group(1), m.group(2), int(m.group(3))
    pages = [json.loads(l) for l in open(path)]
    bs, is_pages = None, []
    for p in pages:
        k = page_kind(p["lines"])
        if k == "BS" and bs is None:
            bs = p["lines"]
        elif k == "IS":
            is_pages.append(p["lines"])
    recs = {}
    if bs:
        # two amount columns: current year (left) and prior year (right)
        # anchor the two amount columns on a totals row (headers are often under the stamp)
        amt_cols = []
        for anchor in ("資產總額", "負債及餘絀總計", "負債總額"):
            for row in rows_of(bs):
                label = "".join(t["text"] for t in row if not NUM.match(t["text"].strip()))
                big = sorted(t["x"] + t["w"] / 2 for t in row
                             if NUM.match(t["text"].strip()) and (to_num(t["text"].strip()) or 0) >= 1000)
                if similar(label, anchor) and len(big) == 2:
                    amt_cols = big
                    break
            if amt_cols:
                break
        if len(amt_cols) >= 2:
            for fy, cx in ((year, amt_cols[0]), (year - 1, amt_cols[1])):
                d = extract(bs, BS_ITEMS, cx)
                recs.setdefault(fy, {}).update({f"bs_{k}": v for k, v in d.items()})
    for i, lines in enumerate(is_pages[:2]):
        fy = year - i
        cx = None
        for row in rows_of(lines):
            label = "".join(t["text"] for t in row if not NUM.match(t["text"].strip()))
            nums = [t["x"] + t["w"] / 2 for t in row if NUM.match(t["text"].strip())]
            if (similar(label, "收入合計") or similar(label, "支出合計")) and len(nums) == 3:
                cx = sorted(nums)[1]
                break
        if cx is None:
            cx = col_center(lines, "決算數")
        if cx is None:
            continue
        d = extract(lines, IS_ITEMS, cx)
        recs.setdefault(fy, {}).update({f"is_{k}": v for k, v in d.items()})
    rows = []
    for fy, d in recs.items():
        rows.append({"code": code, "name": name, "report_year": year, "fiscal_year": fy, **d})
    return rows, (bs is not None, len(is_pages))


def main():
    files = sorted(OCR.glob("*.jsonl"))
    if len(sys.argv) > 1:
        files = [f for f in files if sys.argv[1] in f.name]
    all_rows, problems = [], []
    for f in files:
        rows, (has_bs, n_is) = parse_file(f)
        if not has_bs or n_is < 2:
            problems.append((f.stem, has_bs, n_is))
        all_rows.extend(rows)
    keys = ["code", "name", "report_year", "fiscal_year"] + [f"bs_{k}" for k in BS_ITEMS] + [f"is_{k}" for k in IS_ITEMS]
    with open(ROOT / "data/statements.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(sorted(all_rows, key=lambda r: (r["code"], r["fiscal_year"], -r["report_year"])))
    print(f"files {len(files)} rows {len(all_rows)} problems {len(problems)}")
    for p in problems:
        print("  problem:", p)


if __name__ == "__main__":
    main()

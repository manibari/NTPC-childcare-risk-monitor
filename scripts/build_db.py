"""Load parsed artefacts into data/watchdog.sqlite (rebuilt from files each run).

Tables: preschools (national master), penalties (one row per penalty),
statements (school × fiscal year, reconciled), ratios (derived).
"""
import json
import pathlib
import sqlite3

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data/watchdog.sqlite"

P = json.load(open(ROOT / "data/kiang_preschools.json"))
pre = pd.DataFrame([f["properties"] | {"lng": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1]}
                    if f.get("geometry") else f["properties"] for f in P["features"]])
pre["count_approved"] = pd.to_numeric(pre["count_approved"].astype(str).str.replace(",", ""), errors="coerce")
pre["operator"] = pre["title"].str.extract(r"委託(.+?)辦理")

X = json.load(open(ROOT / "data/kiang_punish_all.json"))
pen = pd.DataFrame([{"preschool_id": p["id"], "date": p["date"].replace("/", "-"), "law": p["law"],
                     "punishment": p["punishment"], "actor": k} for k, v in X.items() for p in v])
pen["law_article"] = pen["law"].str.extract(r"^(第\d+條)")
pen["actor_role"] = pen["actor"].str.split("：").str[0]
pen["actor_name"] = pen["actor"].str.split("：").str[-1]

ratios = pd.read_csv(ROOT / "data/ratios.csv")
ratio_cols = [c for c in ratios.columns if not c.startswith(("bs_", "is_")) and c not in
              ("code", "name", "fiscal_year", "n_sources", "bs_ok", "is_ok", "title", "n_penalty", "penalised", "capacity", "first_penalty_year", "pre_penalty")]
stmt = ratios[["code", "name", "title", "fiscal_year", "n_sources", "bs_ok", "is_ok"] + [c for c in ratios.columns if c.startswith(("bs_", "is_")) and c not in ("bs_ok", "is_ok")]]
rat = ratios[["code", "name", "title", "fiscal_year", "capacity", "penalised", "n_penalty", "pre_penalty"] + ratio_cols]
stmt = stmt.merge(pre[["title", "id"]].rename(columns={"id": "preschool_id"}), on="title", how="left")
rat = rat.merge(pre[["title", "id"]].rename(columns={"id": "preschool_id"}), on="title", how="left")

if DB.exists():
    DB.unlink()
con = sqlite3.connect(DB)
pre.to_sql("preschools", con, index=False)
pen.to_sql("penalties", con, index=False)
stmt.to_sql("statements", con, index=False)
rat.to_sql("ratios", con, index=False)
con.executescript("""
CREATE INDEX idx_pre_city ON preschools(city, type);
CREATE INDEX idx_pen_school ON penalties(preschool_id, date);
CREATE INDEX idx_stmt ON statements(preschool_id, fiscal_year);
CREATE VIEW ntpc_penalty_summary AS
  SELECT p.id, p.title, p.type, p.town, p.owner, p.operator, p.count_approved,
         COUNT(x.date) AS n_penalty, MIN(x.date) AS first_penalty, MAX(x.date) AS last_penalty
  FROM preschools p LEFT JOIN penalties x ON x.preschool_id = p.id
  WHERE p.city = '新北市' GROUP BY p.id;
""")
for t in ("preschools", "penalties", "statements", "ratios"):
    print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
con.close()
print("db:", DB)

"""P1 Linker: owner / operator (委辦法人) → persistent linker codes, membership, two-tier watchlist.

Rules (task_plan 架構定調 5, autoplan E5):
  * 私立 + 非營利 only for kind='owner' (公立 owner = principal, not a business owner → excluded).
  * kind='operator' for any school whose title carries 「委託…辦理」.
  * Codes are persistent: first assignment is never recycled (app_linkers UNIQUE(kind,key_name)).
  * same_name_flag = 1 when a school sits > SAME_NAME_KM from every other school of the same owner
    (likely a namesake, needs human confirmation; excluded from tier 'linked' by default).
  * Watchlist tiers: 'penalized' (own event within watch window) and 'linked' (shares a linker with a
    penalized school). Linked never feeds the score — it is a lead for human confirmation.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import DEFAULT_DB, connect  # noqa: E402

SAME_NAME_KM = 5.0
GENERIC_OWNERS = {"新北市政府", "教育局", "無", "-", "－"}
CODE_PREFIX = {"owner": "O", "operator": "L"}


def _now() -> str:
    return date.today().isoformat()


def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


# ----------------------------------------------------------------------------- linkers
def sync_linkers(con: sqlite3.Connection, city: str = "新北市") -> dict:
    """Assign persistent codes and rebuild membership for the current src_preschools snapshot."""
    rows = con.execute(
        "SELECT id, type, owner, operator, lng, lat FROM src_preschools WHERE city=?", (city,)
    ).fetchall()
    desired: dict[tuple[str, str], list[tuple[str, float, float]]] = {}
    for pid, typ, owner, operator, lng, lat in rows:
        owner = (owner or "").strip()
        operator = (operator or "").strip()
        if owner and typ != "公立" and owner not in GENERIC_OWNERS:
            desired.setdefault(("owner", owner), []).append((pid, lng, lat))
        if operator:
            desired.setdefault(("operator", operator), []).append((pid, lng, lat))

    con.execute("BEGIN IMMEDIATE")
    try:
        existing = {(k, n): lid for lid, k, n in con.execute("SELECT linker_id, kind, key_name FROM app_linkers")}
        next_no = {kind: _next_code_no(con, kind) for kind in CODE_PREFIX}
        n_new = 0
        for (kind, name) in desired:
            if (kind, name) in existing:
                continue
            code = f"{CODE_PREFIX[kind]}-{next_no[kind]:06d}"
            next_no[kind] += 1
            cur = con.execute(
                "INSERT INTO app_linkers(kind, key_name, code, created_at) VALUES (?,?,?,?)",
                (kind, name, code, _now()),
            )
            existing[(kind, name)] = cur.lastrowid
            n_new += 1

        # membership: keep excluded_by_user, drop links no longer in snapshot, add new
        keep_pairs: set[tuple[str, int]] = set()
        n_flag = 0
        for key, members in desired.items():
            lid = existing[key]
            for pid, lng, lat in members:
                flag = 0
                if key[0] == "owner" and len(members) > 1 and lng and lat:
                    nearest = min(
                        (haversine_km(lng, lat, l2, t2) for p2, l2, t2 in members if p2 != pid and l2 and t2),
                        default=0.0,
                    )
                    flag = int(nearest > SAME_NAME_KM)
                n_flag += flag
                con.execute(
                    "INSERT INTO app_preschool_linkers(preschool_id, linker_id, same_name_flag) VALUES (?,?,?) "
                    "ON CONFLICT(preschool_id, linker_id) DO UPDATE SET same_name_flag=excluded.same_name_flag",
                    (pid, lid, flag),
                )
                keep_pairs.add((pid, lid))
        stale = [
            (p, l) for p, l in con.execute("SELECT preschool_id, linker_id FROM app_preschool_linkers")
            if (p, l) not in keep_pairs
        ]
        con.executemany("DELETE FROM app_preschool_linkers WHERE preschool_id=? AND linker_id=?", stale)
        con.execute(
            "UPDATE app_linkers SET n_schools = (SELECT COUNT(*) FROM app_preschool_linkers pl WHERE pl.linker_id = app_linkers.linker_id)"
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    multi = con.execute("SELECT COUNT(*) FROM app_linkers WHERE n_schools > 1").fetchone()[0]
    return {"linkers": len(desired), "new_codes": n_new, "multi_school_linkers": multi,
            "same_name_flags": n_flag, "stale_removed": len(stale)}


def _next_code_no(con: sqlite3.Connection, kind: str) -> int:
    row = con.execute("SELECT MAX(CAST(substr(code, 3) AS INTEGER)) FROM app_linkers WHERE kind=?", (kind,)).fetchone()
    return (row[0] or 0) + 1


# ----------------------------------------------------------------------------- watchlist
def refresh_watchlist(con: sqlite3.Connection, asof: str | None = None, city: str = "新北市") -> dict:
    """Two tiers: 'penalized' (own event inside the watch window) and 'linked' (shares a linker)."""
    asof = asof or get_setting(con, "data_asof") or _now()
    months = int(get_setting(con, "watch_window_months", "12"))
    window_start = (date.fromisoformat(asof) - timedelta(days=int(months * 30.44))).isoformat()

    penalized = con.execute(
        """SELECT e.preschool_id, COUNT(*) AS n, MAX(e.date) AS last_date,
                  (SELECT event_id FROM src_penalty_events x WHERE x.preschool_id=e.preschool_id AND x.date<=? ORDER BY x.date DESC LIMIT 1) AS last_event_id,
                  SUM(e.is_child_safety) AS n_cs
           FROM src_penalty_events e JOIN src_preschools p ON p.id=e.preschool_id
           WHERE p.city=? AND e.date > ? AND e.date <= ? GROUP BY e.preschool_id""",
        (asof, city, window_start, asof),
    ).fetchall()
    pen_ids = {r[0] for r in penalized}

    linked = con.execute(
        """SELECT DISTINCT b.preschool_id, a.preschool_id AS src, a.linker_id, l.kind, l.code
           FROM app_preschool_linkers a
           JOIN app_preschool_linkers b ON b.linker_id=a.linker_id AND b.preschool_id<>a.preschool_id
           JOIN app_linkers l ON l.linker_id=a.linker_id
           JOIN src_preschools p ON p.id=b.preschool_id AND p.is_active=1
           WHERE a.excluded_by_user=0 AND b.excluded_by_user=0 AND a.same_name_flag=0 AND b.same_name_flag=0""",
    ).fetchall()

    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE app_watchlist SET is_current=0 WHERE is_current=1")
        for pid, n, last_date, last_event_id, n_cs in penalized:
            reason = f"近{months}個月裁罰 {n} 次（最近 {last_date}）" + ("，含兒童安全條款" if n_cs else "")
            con.execute(
                "INSERT INTO app_watchlist(preschool_id, asof_date, reason, source_preschool_id, source_event_id, linker_id, tier, is_current) "
                "VALUES (?,?,?,?,?,?, 'penalized', 1)",
                (pid, asof, reason, pid, last_event_id, None),
            )
        seen: set[str] = set()
        n_linked = 0
        for pid, src, lid, kind, code in linked:
            if src not in pen_ids or pid in pen_ids or pid in seen:
                continue
            seen.add(pid)
            src_row = next(r for r in penalized if r[0] == src)
            label = "同負責人" if kind == "owner" else "同委辦法人"
            reason = f"{label}（{code}）園所 {src_row[2]} 裁罰，待人工確認"
            con.execute(
                "INSERT INTO app_watchlist(preschool_id, asof_date, reason, source_preschool_id, source_event_id, linker_id, tier, is_current) "
                "VALUES (?,?,?,?,?,?, 'linked', 1)",
                (pid, asof, reason, src, src_row[3], lid),
            )
            n_linked += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"asof": asof, "window_start": window_start, "penalized": len(penalized), "linked": n_linked}


# ----------------------------------------------------------------------------- coverage (T5)
def coverage_report(con: sqlite3.Connection, city: str = "新北市") -> dict:
    """How much of the city can the linker actually reach? (kiang field fill rates)."""
    q = lambda sql: con.execute(sql, (city,)).fetchone()[0]  # noqa: E731
    return {
        "schools": q("SELECT COUNT(*) FROM src_preschools WHERE city=?"),
        "with_owner_private_nonprofit": q("SELECT COUNT(*) FROM src_preschools WHERE city=? AND type<>'公立' AND owner<>''"),
        "private_nonprofit": q("SELECT COUNT(*) FROM src_preschools WHERE city=? AND type<>'公立'"),
        "with_operator": q("SELECT COUNT(*) FROM src_preschools WHERE city=? AND operator<>''"),
        "with_coords": q("SELECT COUNT(*) FROM src_preschools WHERE city=? AND lng>0 AND lat>0"),
        "penalized_schools_in_linker": q(
            "SELECT COUNT(DISTINCT e.preschool_id) FROM src_penalty_events e JOIN src_preschools p ON p.id=e.preschool_id "
            "JOIN app_preschool_linkers pl ON pl.preschool_id=e.preschool_id WHERE p.city=?"),
        "penalized_schools": q(
            "SELECT COUNT(DISTINCT e.preschool_id) FROM src_penalty_events e JOIN src_preschools p ON p.id=e.preschool_id WHERE p.city=?"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync linkers and refresh the two-tier watchlist")
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--asof", default=None)
    args = ap.parse_args(argv)
    con = connect(args.db)
    s = sync_linkers(con)
    w = refresh_watchlist(con, args.asof)
    c = coverage_report(con)
    for name, d in (("linkers", s), ("watchlist", w), ("coverage", c)):
        print(name + ": " + ", ".join(f"{k}={v}" for k, v in d.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

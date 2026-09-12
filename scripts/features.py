"""P2 features: observation points, leakage-safe event-history features, 31–365 day labels.

Observation point = (preschool_id, asof). asof ∈ {event date + 1 day} ∪ {quarter ends}, asof ≥ reg_date,
and only for schools with ≥ 1 event on/before asof (schools without history are 「無紀錄」, never scored).
Label = 1 if a new event falls in (asof + 30d, asof + 365d]; NULL when asof + 365d > data_asof (unresolved).
Only event-history features (snapshot=0) enter the model; snapshot attributes are excluded by design.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

FEATURE_VERSION = "fv1"
EVENT_FEATURES = [
    "n_events_total", "n_events_12m", "n_events_36m", "days_since_last", "years_since_first",
    "events_per_year", "n_child_safety_total", "n_child_safety_36m", "has_stop_enroll", "has_person_actor",
    "n_rows_total", "n_articles_last", "n_events_prev_12m",
]
ATTR_FEATURES = ["is_private", "is_nonprofit", "count_approved", "years_since_reg", "is_pre_public", "town_event_rate", "town_n_schools"]
LABEL_MIN_DAYS, LABEL_MAX_DAYS = 31, 365
FEATURE_SETS = {"events": EVENT_FEATURES, "events+attrs": EVENT_FEATURES + ATTR_FEATURES}


def feature_hash(feature_set: str = "events") -> str:
    return hashlib.sha256((FEATURE_VERSION + "|" + feature_set + "|" + ",".join(FEATURE_SETS[feature_set])).encode()).hexdigest()[:12]


def is_quarter_end(d: date) -> bool:
    return (d.month, d.day) in {(3, 31), (6, 30), (9, 30), (12, 31)}


def _quarter_ends(start: date, end: date) -> list[date]:
    out, y = [], start.year
    while y <= end.year:
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if start <= q <= end:
                out.append(q)
        y += 1
    return out


def load_events(con: sqlite3.Connection, city: str = "新北市") -> pd.DataFrame:
    ev = pd.read_sql_query(
        """SELECT e.event_id, e.preschool_id, e.date, e.n_rows, e.n_articles, e.is_child_safety,
                  e.has_stop_enroll, e.has_person_actor, p.reg_date, p.town
           FROM src_penalty_events e JOIN src_preschools p ON p.id = e.preschool_id
           WHERE p.city = ? ORDER BY e.preschool_id, e.date""", con, params=(city,))
    ev["date"] = pd.to_datetime(ev["date"]).dt.date
    ev["reg_date"] = pd.to_datetime(ev["reg_date"], errors="coerce").dt.date
    return ev


def observation_points(ev: pd.DataFrame, data_asof: date, include_latest: bool = True) -> pd.DataFrame:
    """One row per (preschool_id, asof) where the school has history on/before asof."""
    if ev.empty:
        return pd.DataFrame(columns=["preschool_id", "asof"])
    rows = []
    q_ends = _quarter_ends(ev["date"].min(), data_asof)
    for pid, g in ev.groupby("preschool_id", sort=False):
        first = g["date"].min()
        reg = g["reg_date"].iloc[0]
        asofs = {d + timedelta(days=1) for d in g["date"]}
        asofs.update(q for q in q_ends if q >= first)
        if include_latest:
            asofs.add(data_asof)
        for a in asofs:
            if a > data_asof or (isinstance(reg, date) and a < reg):
                continue
            rows.append((pid, a))
    return pd.DataFrame(rows, columns=["preschool_id", "asof"])


def build_features(ev: pd.DataFrame, obs: pd.DataFrame, data_asof: date) -> pd.DataFrame:
    """Vectorised per school; asserts that no event after asof leaks into a feature."""
    out = []
    by_school = {pid: g.sort_values("date") for pid, g in ev.groupby("preschool_id", sort=False)}
    for pid, g_obs in obs.groupby("preschool_id", sort=False):
        g = by_school[pid]
        dates = np.array(g["date"].tolist())
        for a in g_obs["asof"]:
            past = g[dates <= a]
            if not len(past) or past["date"].max() > a:  # REGRESSION guard (not an assert: survives python -O)
                raise RuntimeError(f"leakage: event after asof used for {pid} @ {a}")
            last, first = past["date"].max(), past["date"].min()
            d12, d36, d24 = a - timedelta(days=365), a - timedelta(days=3 * 365), a - timedelta(days=2 * 365)
            in12 = past[past["date"] > d12]
            in36 = past[past["date"] > d36]
            prev12 = past[(past["date"] > d24) & (past["date"] <= d12)]
            years = max((a - first).days / 365.25, 0.25)
            future = g[(dates > a + timedelta(days=LABEL_MIN_DAYS - 1)) & (dates <= a + timedelta(days=LABEL_MAX_DAYS))]
            resolved = a + timedelta(days=LABEL_MAX_DAYS) <= data_asof
            out.append({
                "preschool_id": pid, "asof": a,
                "n_events_total": len(past), "n_events_12m": len(in12), "n_events_36m": len(in36),
                "days_since_last": (a - last).days, "years_since_first": years,
                "events_per_year": len(past) / years,
                "n_child_safety_total": int(past["is_child_safety"].sum()),
                "n_child_safety_36m": int(in36["is_child_safety"].sum()),
                "has_stop_enroll": int(past["has_stop_enroll"].max()),
                "has_person_actor": int(past["has_person_actor"].max()),
                "n_rows_total": int(past["n_rows"].sum()),
                "n_articles_last": int(past.iloc[-1]["n_articles"]),
                "n_events_prev_12m": len(prev12),
                "label": (int(len(future) > 0) if resolved else None),
                "label_resolved": int(resolved),
                "next_event_days": (int((future["date"].min() - a).days) if len(future) else None),
            })
    df = pd.DataFrame(out)
    df["snapshot"] = 0
    return df


def attach_attributes(con: sqlite3.Connection, df: pd.DataFrame, ev: pd.DataFrame, city: str = "新北市") -> pd.DataFrame:
    """Snapshot attributes (fv2): school type/size/age + the town's event rate computed only from events before asof
    (time-safe). Excluded from the default feature set by design; enabled with feature_set='events+attrs'."""
    ps = pd.read_sql_query("SELECT id, type, town, count_approved, reg_date, is_pre_public FROM src_preschools WHERE city=?", con, params=(city,))
    ps["reg"] = pd.to_datetime(ps["reg_date"], errors="coerce").dt.date
    n_town = ps.groupby("town").size()
    m = df.merge(ps[["id", "type", "town", "count_approved", "reg", "is_pre_public"]], left_on="preschool_id", right_on="id", how="left")
    m["is_private"] = (m["type"] == "私立").astype(int)
    m["is_nonprofit"] = (m["type"] == "非營利").astype(int)
    m["count_approved"] = pd.to_numeric(m["count_approved"], errors="coerce").fillna(0)
    m["years_since_reg"] = [((a - r).days / 365.25 if isinstance(r, date) else 0.0) for a, r in zip(m["asof"], m["reg"])]
    m["is_pre_public"] = pd.to_numeric(m["is_pre_public"], errors="coerce").fillna(0).astype(int)
    m["town_n_schools"] = m["town"].map(n_town).fillna(0)
    ev_sorted = ev.sort_values("date")
    rates = []
    for a, tn in zip(m["asof"], m["town"]):
        past = ev_sorted[(ev_sorted["town"] == tn) & (ev_sorted["date"] <= a) & (ev_sorted["date"] > a - timedelta(days=3 * 365))]
        rates.append(len(past) / max(n_town.get(tn, 1), 1))
    m["town_event_rate"] = rates
    return m.drop(columns=["id", "type", "town", "reg"])


def feature_frame(con: sqlite3.Connection, data_asof: str | date, city: str = "新北市", feature_set: str = "events") -> pd.DataFrame:
    data_asof = date.fromisoformat(data_asof) if isinstance(data_asof, str) else data_asof
    ev = load_events(con, city)
    obs = observation_points(ev, data_asof)
    df = build_features(ev, obs, data_asof)
    if feature_set == "events+attrs":
        df = attach_attributes(con, df, ev, city)
    return df


# ----------------------------------------------------------------------------- 回頭客燈號 (rule score)
def rule_score(df: pd.DataFrame) -> pd.Series:
    """Transparent repeat-offender score: events in 36 m × recency × child-safety bonus (架構定調 3)."""
    recency = np.exp(-df["days_since_last"] / 365.0)
    base = df["n_events_36m"].clip(upper=5)
    return base * (0.5 + recency) + 0.5 * df["n_child_safety_36m"].clip(upper=2) + 0.5 * df["has_stop_enroll"]


def rule_reason(row: pd.Series) -> str:
    parts = [f"近 3 年裁罰 {int(row['n_events_36m'])} 次", f"距上次 {int(row['days_since_last'])} 天"]
    if row["n_child_safety_36m"]:
        parts.append(f"不當對待或安全相關 {int(row['n_child_safety_36m'])} 次")
    if row["has_stop_enroll"]:
        parts.append("曾停止招生")
    return "、".join(parts)

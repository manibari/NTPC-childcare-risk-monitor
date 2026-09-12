"""輿情情蒐 for one school: Google News RSS (no key) → keyword tone → cached in app_sentiment.

Sentiment never enters the score (架構定調 3 / NOT-in-scope list): it is pre-visit context for the inspector.
Items are public news headlines only; nothing is scraped from social accounts.
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import reviews as _reviews

RSS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
NEGATIVE = ["虐", "不當對待", "體罰", "毆", "打", "傷", "超收", "停業", "停止招生", "檢舉", "投訴", "家長控", "裁罰", "罰鍰", "違規", "違法", "餵藥", "性侵", "猥褻", "霸凌", "疏失", "意外", "死亡", "食安", "中毒", "關門", "倒閉", "積欠", "欠薪", "無照", "未立案"]
POSITIVE = ["優等", "特優", "績優", "得獎", "表揚", "認證", "評鑑優", "模範", "感謝", "公益"]
MAX_AGE_DAYS = 7


def short_title(title: str) -> str:
    t = re.sub(r"[（(].*?[)）]", "", title or "").replace("新北市", "", 1)
    t = re.sub(r"^(私立|公立|市立|非營利)", "", t)
    return t.strip()


def classify(text: str) -> str:
    if any(k in text for k in NEGATIVE):
        return "負面"
    if any(k in text for k in POSITIVE):
        return "正面"
    return "中性"


def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except Exception:
            pub = None
        link = (it.findtext("link") or "").strip()
        if not re.match(r"^https?://", link):
            link = None
        out.append({"title": title, "link": link, "source": src.text if src is not None else None, "date": pub, "tone": classify(title)})
    return out


def fetch(title: str, timeout: int = 8) -> dict:
    q = f'"{short_title(title)}" 幼兒園'
    url = RSS.format(q=urllib.parse.quote(q))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 smart-watchdog/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        items = parse_rss(r.read(2_000_000).decode("utf-8", "replace"))
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"query": q, "items": items[:30], "n_items": len(items), "n_negative": sum(i["tone"] == "負面" for i in items),
            "n_12m": sum(1 for i in items if i["date"] and i["date"] >= cutoff)}


def _row(con, preschool_id):
    row = con.execute("SELECT * FROM app_sentiment WHERE preschool_id=?", (preschool_id,)).fetchone()
    if not row:
        return None
    d = dict(zip([c[0] for c in con.execute("SELECT * FROM app_sentiment LIMIT 0").description], row))
    d["items"] = json.loads(d["items"]); d["reviews"] = json.loads(d["reviews"]) if d.get("reviews") else []
    return d


def get_or_refresh(con: sqlite3.Connection, preschool_id: str, title: str, town: str = "", refresh: bool = False) -> dict:
    d = _row(con, preschool_id)
    if d and not refresh:
        age = (datetime.now() - datetime.fromisoformat(d["fetched_at"])).days
        if age <= MAX_AGE_DAYS:
            d["cached"] = True; d["age_days"] = age; d["reviews_enabled"] = _reviews.enabled()
            return d
    try:
        r = fetch(title)
    except Exception as e:  # offline → tell the UI, keep any cached copy
        if d:
            d["cached"] = True; d["error"] = str(e)[:120]; d["reviews_enabled"] = _reviews.enabled()
            return d
        return {"error": f"無法連線新聞來源：{str(e)[:120]}", "items": [], "n_items": 0, "n_negative": 0, "n_12m": 0, "reviews": [], "reviews_enabled": _reviews.enabled()}
    rv = None
    try:
        rv = _reviews.fetch(short_title(title), town)
    except Exception as e:
        r["reviews_error"] = str(e)[:120]
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO app_sentiment(preschool_id, fetched_at, query, n_items, n_negative, n_12m, items, rating, n_ratings, reviews, place_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (preschool_id, now, r["query"], r["n_items"], r["n_negative"], r["n_12m"], json.dumps(r["items"], ensure_ascii=False),
                 rv["rating"] if rv else None, rv["n_ratings"] if rv else None, json.dumps(rv["reviews"], ensure_ascii=False) if rv else None, rv["place_id"] if rv else None))
    return {"preschool_id": preschool_id, "fetched_at": now, "cached": False, "age_days": 0, **r,
            "rating": rv["rating"] if rv else None, "n_ratings": rv["n_ratings"] if rv else None, "reviews": rv["reviews"] if rv else [], "reviews_enabled": _reviews.enabled()}

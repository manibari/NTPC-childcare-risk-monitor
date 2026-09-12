"""Google Maps ratings / reviews for a school — only when GOOGLE_MAPS_API_KEY is set (Places API).
Without a key the function returns None and the UI says so; nothing is scraped from Maps pages."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json?query={q}&language=zh-TW&region=tw&key={k}"
DETAILS = "https://maps.googleapis.com/maps/api/place/details/json?place_id={pid}&fields=rating,user_ratings_total,reviews&language=zh-TW&key={k}"


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_API_KEY"))


def fetch(title: str, town: str = "", timeout: int = 8) -> dict | None:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    q = urllib.parse.quote(f"新北市{town} {title}")
    with urllib.request.urlopen(SEARCH.format(q=q, k=key), timeout=timeout) as r:
        res = json.load(r).get("results") or []
    if not res:
        return {"place_id": None, "rating": None, "n_ratings": 0, "reviews": []}
    key_name = title.replace("幼兒園", "")
    top = next((x for x in res if key_name and key_name in (x.get("name") or "") and (not town or town in (x.get("formatted_address") or ""))), None)
    if top is None:  # do not attach another business's stars to this school
        return {"place_id": None, "rating": None, "n_ratings": 0, "reviews": []}
    with urllib.request.urlopen(DETAILS.format(pid=top["place_id"], k=key), timeout=timeout) as r:
        d = json.load(r).get("result") or {}
    reviews = [{"rating": x.get("rating"), "time": x.get("relative_time_description"), "text": (x.get("text") or "")[:300]} for x in d.get("reviews") or []]
    return {"place_id": top["place_id"], "rating": d.get("rating", top.get("rating")), "n_ratings": d.get("user_ratings_total", top.get("user_ratings_total", 0)), "reviews": reviews}

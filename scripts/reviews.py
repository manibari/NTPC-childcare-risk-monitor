"""Google Maps ratings / reviews for a school via Places API (New) — only when GOOGLE_MAPS_API_KEY is set.
One Text Search call returns rating, user rating count and up to 5 reviews. The result is attached only when
the place name contains the school name and the address contains the district; otherwise nothing is attached."""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    import pathlib as _pl
    load_dotenv(_pl.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import json
import os
import urllib.request

SEARCH = "https://places.googleapis.com/v1/places:searchText"
FIELDS = "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.reviews"


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_API_KEY"))


def fetch(title: str, town: str = "", timeout: int = 8) -> dict | None:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    body = json.dumps({"textQuery": f"新北市{town} {title}", "languageCode": "zh-TW", "regionCode": "TW", "pageSize": 5}).encode()
    req = urllib.request.Request(SEARCH, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r).get("places") or []
    key_name = title.replace("幼兒園", "")
    top = next((x for x in res if key_name and key_name in ((x.get("displayName") or {}).get("text") or "")
                and (not town or town in (x.get("formattedAddress") or ""))), None)
    if top is None:  # do not attach another business's stars to this school
        return {"place_id": None, "rating": None, "n_ratings": 0, "reviews": []}
    reviews = [{"rating": x.get("rating"), "time": x.get("relativePublishTimeDescription"),
                "text": ((x.get("text") or {}).get("text") or "")[:300]} for x in top.get("reviews") or []]
    return {"place_id": top.get("id"), "rating": top.get("rating"), "n_ratings": top.get("userRatingCount", 0), "reviews": reviews}

"""Plain-language violation labels per 幼兒教育及照顧法 article, derived from the source rows' own text
(most frequent description per article across the national penalty list, 2026-09 snapshot).
A row keeps its own text when it has one; the label is the fallback for rows that only cite the article."""
from __future__ import annotations

import re

LABELS = {
    "第33條": "教保人員對幼兒不當對待",
    "第8條": "超收幼兒或設施設備不符",
    "第16條": "師生比或編班違規",
    "第43條": "收費超收或未報備查",
    "第32條": "進用未具資格人員",
    "第12條": "違反教保照顧禁止規定",
    "第15條": "教職員異動未報備查",
    "第30條": "人員不當對待幼兒／安全管理未落實",
    "第31條": "幼童專用車違規",
    "第17條": "未依規定配置教師／廚工／護理人員",
    "第48條": "未經核准辦理課後照顧",
    "第46條": "評鑑追蹤未改善或拒絕檢查",
    "第25條": "不當管教或體罰",
    "第34條": "未辦理幼兒團體保險",
    "第42條": "未訂書面契約或超收未退費",
    "第37條": "未依規定公開資訊",
    "第26條": "進用未具資格人員",
    "第41條": "追蹤評鑑未改善",
    "第27條": "不適任人員認定／通報違規",
    "第20條": "延長照顧進用未符資格人員",
    "第38條": "收費未報備查或超收",
    "第29條": "負責人／董監事資格不符",
    "第13條": "聘用不得聘用之人員",
}
CHILD_SAFETY_LABEL = "不當對待或安全相關"
SHORT = {"第33條": "不當對待", "第8條": "超收/設施", "第16條": "師生比", "第43條": "收費", "第32條": "人員資格", "第12條": "教保禁規",
         "第15條": "人員備查", "第30條": "不當對待/安全", "第31條": "幼童車", "第17條": "人員配置", "第48條": "課後照顧", "第46條": "評鑑/拒檢",
         "第25條": "體罰管教", "第34條": "團保", "第42條": "契約退費", "第37條": "資訊公開", "第26條": "人員資格"}


def short_label(article: str | None) -> str:
    return SHORT.get(article or "", (article or "未載明"))
_PREFIX = re.compile(r"^第\d+條(之\d+)?(第\d+項)?(第\d+款)?[\s\-–—:：]*")


def label(article: str | None) -> str:
    if not article:
        return "來源未載明條文"
    return LABELS.get(article, f"違反{article}（來源未載明細節）")


def describe(article: str | None, law: str | None) -> str:
    """Own description when the row carries one, otherwise the article's typical label."""
    desc = _PREFIX.sub("", law or "").strip("。 ")
    return desc if desc else label(article)


def short(text: str, n: int = 12) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"

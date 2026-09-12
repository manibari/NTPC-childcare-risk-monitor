"""輿情: RSS parsing + tone keywords, offline (canned XML)."""
from sentiment import classify, parse_rss, short_title

RSS = """<rss><channel><item><title>板橋某幼兒園爆不當對待 家長檢舉</title><link>http://x/1</link><pubDate>Mon, 01 Sep 2026 08:00:00 GMT</pubDate><source url="http://a">A報</source></item>
<item><title>幼兒園評鑑特優名單出爐</title><link>http://x/2</link><pubDate>Sun, 10 Aug 2025 08:00:00 GMT</pubDate></item></channel></rss>"""


def test_parse_and_classify():
    items = parse_rss(RSS)
    assert [i["tone"] for i in items] == ["負面", "正面"] and items[0]["source"] == "A報" and items[0]["date"] == "2026-09-01"
    assert classify("園方發表聲明") == "中性"
    assert short_title("新北市私立小親親幼兒園（委託某協會辦理）") == "小親親幼兒園"

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

REAL_DATA = ROOT / "data"
HAS_REAL = (REAL_DATA / "kiang_preschools.json").exists() and (REAL_DATA / "kiang_punish_all.json").exists()


def preschool(pid, title, town="新莊區", typ="私立", owner="王小明", extra=None):
    props = {"id": pid, "title": title, "city": "新北市", "town": town, "type": typ, "owner": owner, "count_approved": "60",
             "monthly": "9000", "pre_public": "無", "reg_date": "2015/03/01", "is_active": 1, "address": "x", "tel": "y"}
    props.update(extra or {})
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [121.4, 25.0]}}


@pytest.fixture
def synthetic_data(tmp_path):
    """Two schools, one with three penalty rows on two dates (one date has two articles)."""
    feats = [preschool("A", "新北市私立甲幼兒園"), preschool("B", "新北市乙非營利幼兒園（委託社團法人丙協會辦理）", typ="非營利", owner="")]
    punish = {
        "負責人：王小明": [
            {"id": "A", "date": "2024/01/10", "law": "第16條第1項 班級師生比", "punishment": "罰鍰：60,000元"},
            {"id": "A", "date": "2024/01/10", "law": "第33條第1項 教保服務人員不當對待", "punishment": "罰鍰：60,000元"},
            {"id": "A", "date": "2024/06/01", "law": "第8條 設立變更", "punishment": "停止招生：2024/06/01~2025/05/31"},
        ],
        "行為人：李某": [
            {"id": "A", "date": "2024/06/01", "law": "第33條第1項", "punishment": "罰鍰：6,000元"},
        ],
    }
    (tmp_path / "kiang_preschools.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    (tmp_path / "kiang_punish_all.json").write_text(json.dumps(punish, ensure_ascii=False))
    return tmp_path

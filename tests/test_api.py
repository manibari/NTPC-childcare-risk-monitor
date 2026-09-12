"""P4 contract tests on the real DB (skipped without it): every endpoint answers, envelope shape, and the
REGRESSION-level de-identification property: no owner / actor name from src tables appears in any response."""
import json
import os
import pathlib
import sqlite3

import pytest

from conftest import HAS_REAL, ROOT

DB = ROOT / "data" / "watchdog.sqlite"
pytestmark = pytest.mark.skipif(not HAS_REAL or not DB.exists(), reason="real DB not built")


@pytest.fixture(scope="module")
def client():
    os.environ["WATCHDOG_DB"] = str(DB)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    import sys
    sys.path.insert(0, str(ROOT / "app"))
    from fastapi.testclient import TestClient
    import importlib
    main = importlib.import_module("main")
    return TestClient(main.app)


@pytest.fixture(scope="module")
def names():
    con = sqlite3.connect(DB)
    out = set()
    # 負責人 is public registry data and is shown by decision (2026-09-12); other individuals (行為人／教保人員) never
    owners = {r[0] for r in con.execute("SELECT DISTINCT owner FROM src_preschools WHERE owner IS NOT NULL")}
    out |= {r[0] for r in con.execute("SELECT DISTINCT actor_name FROM src_penalties WHERE actor_name IS NOT NULL AND length(actor_name)>=2 AND actor_role<>'負責人'")} - owners
    import re
    org = re.compile(r"幼兒園|公司|法人|協會|基金會|學校|國小|國中|大學|政府|負責人|教會|寺|宮")
    return {n for n in out if n and not org.search(n)}


ENDPOINTS = ["/api/v1/overview", "/api/v1/rankings?size=500", "/api/v1/schedule", "/api/v1/season-list", "/api/v1/finance",
             "/api/v1/backtest", "/api/v1/data-quality", "/api/v1/settings", "/api/v1/schedule/capacity-curve"]


def test_endpoints_and_no_names(client, names):
    dump = ""
    for ep in ENDPOINTS:
        r = client.get(ep); assert r.status_code == 200, ep
        dump += r.text
    top = client.get("/api/v1/rankings?size=5&level=高").json()["items"][0]
    r = client.get(f"/api/v1/preschools/{top['preschool_id']}"); assert r.status_code == 200; dump += r.text
    assert r.json()["score"]["level"] in ("高", "中", "低")
    code = client.get("/api/v1/data-quality").json()
    lk = sqlite3.connect(DB).execute("SELECT code FROM app_linkers WHERE n_schools>1 LIMIT 1").fetchone()[0]
    r = client.get(f"/api/v1/linkers/{lk}/graph"); assert r.status_code == 200 and r.json()["nodes"]; dump += r.text
    r = client.get("/api/v1/export?scope=season&format=csv"); assert r.status_code == 200; dump += r.content.decode()
    leaked = [n for n in names if n in dump]
    assert not leaked, f"names leaked: {leaked[:5]}"


def test_error_envelope_and_state(client):
    r = client.get("/api/v1/rankings?type=外星")
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_FILTER" and "request_id" in r.json()["error"]
    r = client.post("/api/v1/ask", json={"question": "hi"})
    assert r.status_code == 409 and r.json()["state"] == "agent_disabled"
    r = client.get("/api/v1/preschools/nope"); assert r.status_code == 404
    r = client.put("/api/v1/settings", json={"high_threshold": 0.1, "mid_threshold": 0.2}); assert r.status_code == 400


def test_season_list_roundtrip_and_feedback(client):
    pid = client.get("/api/v1/rankings?size=1&page=3&level=低").json()["items"][0]["preschool_id"]
    n0 = client.get("/api/v1/season-list").json()
    r = client.post("/api/v1/season-list", json={"preschool_id": pid, "note": "test"}); assert r.status_code == 200
    assert any(m["preschool_id"] == pid for m in client.get("/api/v1/season-list").json()["manual"])
    assert client.delete(f"/api/v1/season-list/{pid}").status_code == 200
    assert not any(m["preschool_id"] == pid for m in client.get("/api/v1/season-list").json()["manual"])
    r = client.post("/api/v1/feedback", json={"page": "rank", "text": "誤判"}); assert r.status_code == 200 and r.json()["feedback_id"]
    sqlite3.connect(DB).execute("DELETE FROM app_feedback WHERE text='誤判'").connection.commit()


def test_agent_sql_guard():
    import sys
    sys.path.insert(0, str(ROOT / "app"))
    from agent import AgentService
    a = AgentService(DB)
    assert "error" in a.sql_readonly("DELETE FROM app_settings")
    assert "error" in a.sql_readonly("SELECT owner FROM src_preschools LIMIT 1")      # authorizer denies src_
    r = a.sql_readonly("SELECT title FROM v_preschools WHERE city='新北市'")
    assert r["n"] == 200                                                                # LIMIT enforced
    assert "error" in a.sql_readonly("SELECT 1; SELECT 2")

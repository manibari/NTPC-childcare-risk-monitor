"""P2 tests: leakage guard, label window, deterministic training, single-active approval."""
from datetime import date

import pytest

from conftest import HAS_REAL, REAL_DATA
from db import DBBuilder, connect
from features import EVENT_FEATURES, build_features, feature_frame, load_events, observation_points
from approve import approve
from errors import PipelineError


def _con(tmp_path, data):
    b = DBBuilder(tmp_path / "wd.sqlite", data)
    b.build()
    return connect(b.db_path)


def test_observation_points_and_labels(tmp_path, synthetic_data):
    con = _con(tmp_path, synthetic_data)
    df = feature_frame(con, "2025-12-31")
    a = df[df.preschool_id == "A"].set_index("asof")
    # event+1 day points exist; 2024-01-11 sees only the first event and its label window catches 2024-06-01
    assert date(2024, 1, 11) in a.index and a.loc[date(2024, 1, 11), "n_events_total"] == 1
    assert a.loc[date(2024, 1, 11), "label"] == 1 and a.loc[date(2024, 1, 11), "next_event_days"] == 142
    # 2024-06-02 sees both events, nothing after → label 0 (resolved because 2025-06-02 <= data_asof)
    assert a.loc[date(2024, 6, 2), "n_events_total"] == 2 and a.loc[date(2024, 6, 2), "label"] == 0
    # latest asof is unresolved
    assert a.loc[date(2025, 12, 31), "label_resolved"] == 0
    # school B has no events → never an observation point (「無紀錄」)
    assert "B" not in set(df.preschool_id)
    assert (df["snapshot"] == 0).all() and set(EVENT_FEATURES) <= set(df.columns)


def test_no_event_after_asof_leaks(tmp_path, synthetic_data):
    con = _con(tmp_path, synthetic_data)
    ev = load_events(con)
    obs = observation_points(ev, date(2025, 12, 31))
    df = build_features(ev, obs, date(2025, 12, 31))
    for _, r in df.iterrows():
        past = ev[(ev["preschool_id"] == r["preschool_id"]) & (ev["date"] <= r["asof"])]
        assert r["n_events_total"] == len(past)              # feature equals what was knowable at asof


def test_approve_single_active_and_gate(tmp_path, synthetic_data, monkeypatch):
    import approve as approve_mod
    monkeypatch.setattr(approve_mod, "MODEL_DIR", tmp_path)
    for i in (1, 2, 3):
        (tmp_path / f"model_{i}.pkl").write_bytes(b"x")
    con = _con(tmp_path, synthetic_data)
    con.executemany(
        "INSERT INTO app_models(trained_at, algo, params, seed, feature_hash, data_asof, auc, beats_baseline, status) VALUES (?,?,?,?,?,?,?,?,?)",
        [("2026-01-01", "gbdt", "{1}", 42, "h", "2026-01-01", 0.70, 1, "trained"),
         ("2026-01-02", "gbdt", "{2}", 42, "h", "2026-01-01", 0.60, 1, "trained"),
         ("2026-01-03", "gbdt", "{3}", 42, "h", "2026-01-01", 0.61, 0, "trained")])
    approve(con, 1, "peter")
    with pytest.raises(PipelineError):        # AUC drop > 0.05
        approve(con, 2, "peter")
    with pytest.raises(PipelineError):        # did not beat baseline
        approve(con, 3, "peter")
    approve(con, 2, "peter", force=True)
    assert con.execute("SELECT model_id FROM app_models WHERE status='active'").fetchall() == [(2,)]
    assert con.execute("SELECT status FROM app_models WHERE model_id=1").fetchone()[0] == "retired"
    with pytest.raises(Exception):            # partial unique index: two actives impossible
        con.execute("UPDATE app_models SET status='active' WHERE model_id=1")


@pytest.mark.skipif(not HAS_REAL, reason="real kiang snapshot not present")
def test_training_is_deterministic(tmp_path):
    from train import walk_forward
    con = _con(tmp_path, REAL_DATA)
    df = feature_frame(con, "2026-09-11")
    a = walk_forward(df, "logreg", [2023])
    b = walk_forward(df, "logreg", [2023])
    assert a == b and a[0]["baseline"] is None and a[0]["n_pos"] > 50

"""Export metadata is reproducible and does not mistake build time for data time."""

import json

from gpa import store
from gpa.export import _overview
from gpa.zones import get_zone
from tests.test_store import price_rows


def test_overview_depends_on_observations_not_export_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    store.write(price_rows([10.0, -20.0]), "price")
    first = _overview()
    second = _overview()
    assert json.dumps(first, default=str) == json.dumps(second, default=str)
    assert first["data_as_of"] == "2026-06-15T01:00:00+00:00"
    german = next(z for z in first["zones"] if z["code"] == "DE-LU")
    assert "age_hours" not in german["datasets"]["price"]
    assert german["datasets"]["load"]["status"] == "pending"


def test_empty_store_has_no_data_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert _overview()["data_as_of"] is None


def test_market_dst_is_not_inferred_from_civil_timezone_presence():
    assert not get_zone("BR-SIN").observes_market_dst
    assert get_zone("DE-LU").observes_market_dst

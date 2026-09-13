"""Export metadata is reproducible and does not mistake build time for data time."""

import json

from gpa import store
from gpa.export import _json_values_close, _overview
from gpa.zones import get_zone
from tests.test_store import price_rows


def test_export_check_tolerates_cross_platform_float_noise_not_real_change():
    # A fitted model's last bits can differ between a Windows workstation and
    # a Linux CI runner even with a fixed seed; that must not read as stale.
    reference = {"best_mae": 21.03558956221899, "alpha_search": [{"mae": 16.579941693983606}]}
    noisy = {"best_mae": 21.035589562219, "alpha_search": [{"mae": 16.579941693983607}]}
    assert _json_values_close(reference, noisy)
    # A real change, even a small one, must still be caught.
    changed = {"best_mae": 21.05, "alpha_search": [{"mae": 16.579941693983606}]}
    assert not _json_values_close(reference, changed)
    missing_key = {"alpha_search": [{"mae": 16.579941693983606}]}
    assert not _json_values_close(reference, missing_key)


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

"""Prospective evidence must survive retries, late completion and reconciliation."""

import datetime as dt
from dataclasses import replace

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from gpa.forecast import ledger
from gpa.forecast.models import Ridge
from gpa.forecast.panel import build_panel
from tests.test_forecast import ZONE, price_frame, small_panel

DAY = dt.date(2025, 2, 10)
STAMP = dt.datetime(2025, 2, 9, 9, tzinfo=dt.UTC)


def full_panel():
    return build_panel(price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24 * 45), ZONE)


def changed_target(source, value):
    feature = source.features[0]
    return replace(source, frame=source.frame.with_columns(
        pl.when(pl.col("local_date") == DAY).then(value).otherwise(pl.col(feature)).alias(feature)
    ))


def record(root, *, panel=None, stamp=STAMP, **kwargs):
    return ledger.record_issue(panel or full_panel(), Ridge(0.1), DAY,
                               issued_at=stamp, root=root, **kwargs)


def test_target_day_features_change_input_identity():
    source = small_panel()
    one = ledger.issue(source, Ridge(0.1), DAY, issued_at=STAMP)
    two = ledger.issue(changed_target(source, 500.0), Ridge(0.1), DAY, issued_at=STAMP)
    assert one["input_sha256"][0] != two["input_sha256"][0]


def test_parameter_changes_cannot_hide_behind_the_same_model_label():
    one = ledger.issue(small_panel(), Ridge(0.1), DAY, issued_at=STAMP, model_version="custom")
    two = ledger.issue(small_panel(), Ridge(1.0), DAY, issued_at=STAMP, model_version="custom")
    assert one["model_version"][0] != two["model_version"][0]


def test_future_outcomes_are_removed_before_prediction_and_fingerprinting():
    source = small_panel()
    changed = replace(source, frame=source.frame.with_columns(
        pl.when(pl.col("local_date") >= DAY).then(999999.0).otherwise(pl.col("price")).alias("price")
    ))
    one = ledger.issue(source, Ridge(0.1), DAY, issued_at=STAMP)
    two = ledger.issue(changed, Ridge(0.1), DAY, issued_at=STAMP)
    assert_frame_equal(one, two)


def test_all_expected_hours_and_abstentions_are_retained(tmp_path):
    result = record(tmp_path, panel=changed_target(full_panel(), None))
    assert result.height == 24
    assert result["forecast"].null_count() == 24
    assert set(result["status"]) == {"abstain_missing_inputs"}
    assert ledger.read(root=tmp_path).height == 24
    assert ledger.canonical(result, root=tmp_path).is_empty()


def test_late_diagnostics_never_become_scored_prospective_rows():
    late = ledger.issue(full_panel(), Ridge(0.1), DAY,
                        issued_at=STAMP.replace(hour=12), allow_late=True)
    prices = price_frame(dt.datetime(2025, 2, 9, 23, tzinfo=dt.UTC), 24)
    result = ledger.reconcile(late, prices, ZONE)
    assert not result["eligible"].any()
    assert set(result["status"]) == {"diagnostic_scored"}


def test_exact_gate_time_is_not_before_the_gate():
    with pytest.raises(ValueError, match="gate"):
        ledger.issue(full_panel(), Ridge(0.1), DAY, issued_at=ledger.market_gate(DAY, ZONE))


def test_clock_is_sampled_after_prediction(monkeypatch):
    times = iter([STAMP, STAMP.replace(hour=12)])
    monkeypatch.setattr(ledger, "now_utc", lambda: next(times))
    with pytest.raises(ValueError, match="gate"):
        ledger.issue(full_panel(), Ridge(0.1), DAY)


def test_late_persistence_is_ineligible_even_if_computation_finished_before_gate(tmp_path):
    result = record(tmp_path, clock=lambda: STAMP.replace(hour=12))
    assert not result["eligible"].any()
    assert set(result["eligibility_reason"]) == {"late_recording"}
    assert ledger.canonical(result, root=tmp_path).is_empty()


def test_nonfinite_predictions_fail_instead_of_becoming_eligible(monkeypatch):
    def bad(self, panel, day, *, min_train_rows):
        return pl.DataFrame({"local_date": [DAY], "local_hour": [0], "forecast": [float("nan")]})
    monkeypatch.setattr(Ridge, "predict_day", bad)
    with pytest.raises(ValueError, match="finite"):
        ledger.issue(full_panel(), Ridge(0.1), DAY, issued_at=STAMP)


def test_snapshot_round_trip_and_replay(tmp_path):
    from gpa.forecast import provenance
    frame = record(tmp_path)
    meta, prepared, model, original = provenance.read_snapshot(tmp_path, frame["issue_id"][0])
    assert meta["model"]["parameters"]["alpha"] == 0.1
    assert meta["source_availability"] == "not_supplied"
    assert prepared.frame.filter(pl.col("local_date") == DAY)["price"].null_count() == 24
    assert prepared.frame.filter(pl.col("local_date") > DAY).is_empty()
    assert_frame_equal(original, frame)
    replay = ledger.issue(prepared, model, DAY, issued_at=STAMP)
    assert_frame_equal(replay.select("local_hour", "forecast"), frame.select("local_hour", "forecast"))
    assert ledger.canonical(frame, root=tmp_path).height == 24


def test_snapshot_checksums_reject_modified_input(tmp_path):
    from gpa.forecast import provenance
    frame = record(tmp_path)
    path = tmp_path / "issues" / frame["issue_id"][0] / "input_panel.parquet"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        provenance.read_snapshot(tmp_path, frame["issue_id"][0])
    with pytest.raises(ValueError, match="checksum"):
        ledger.canonical(frame, root=tmp_path)


def test_canonical_selects_one_whole_earliest_complete_issue(tmp_path):
    first = record(tmp_path)
    later = record(tmp_path, panel=changed_target(full_panel(), 400.0), stamp=STAMP.replace(hour=10))
    combined = pl.concat([later, first])
    chosen = ledger.canonical(combined, root=tmp_path)
    assert chosen["issue_id"].unique().to_list() == first["issue_id"].unique().to_list()
    assert_frame_equal(chosen, first)


def test_canonical_does_not_stitch_partial_retries(tmp_path):
    source = full_panel()
    feature = source.features[0]
    first_panel = replace(source, frame=source.frame.with_columns(
        pl.when((pl.col("local_date") == DAY) & (pl.col("local_hour") < 12))
        .then(None).otherwise(pl.col(feature)).alias(feature)))
    second_panel = replace(source, frame=source.frame.with_columns(
        pl.when((pl.col("local_date") == DAY) & (pl.col("local_hour") >= 12))
        .then(None).otherwise(pl.col(feature)).alias(feature)))
    first = record(tmp_path, panel=first_panel)
    later = record(tmp_path, panel=second_panel, stamp=STAMP.replace(hour=10))
    assert ledger.canonical(pl.concat([first, later]), root=tmp_path).is_empty()


def test_reconciliation_can_update_actuals_but_not_original_forecasts(tmp_path):
    original = record(tmp_path)
    prices = price_frame(dt.datetime(2025, 2, 9, 23, tzinfo=dt.UTC), 24)
    scored = ledger.reconcile(original, prices, ZONE)
    ledger.append(scored, root=tmp_path)
    assert set(ledger.read(root=tmp_path)["status"]) == {"scored"}
    with pytest.raises(ValueError, match="immutable"):
        ledger.append(scored.with_columns(pl.col("forecast") + 10), root=tmp_path)
    ledger.append(original, root=tmp_path)  # Retrying cannot erase observed settlement.
    assert set(ledger.read(root=tmp_path)["status"]) == {"scored"}


def test_duplicate_issue_rows_raise(tmp_path):
    frame = ledger.issue(full_panel(), Ridge(0.1), DAY, issued_at=STAMP)
    with pytest.raises(ValueError, match="duplicate"):
        ledger.append(pl.concat([frame, frame.head(1)]), root=tmp_path)


def test_legacy_ledger_is_readable_but_not_prospectively_certified(tmp_path):
    frame = ledger.issue(full_panel(), Ridge(0.1), DAY, issued_at=STAMP)
    legacy = frame.select("zone", "model", "model_version", "issued_at", "delivery_date",
                         "local_hour", "delivery_start_utc", "forecast", "actual", "status", "input_sha256")
    path = tmp_path / "zone=DE-LU" / "2025-02.parquet"
    path.parent.mkdir()
    legacy.write_parquet(path)
    loaded = ledger.read(root=tmp_path)
    assert loaded.height == 24
    assert not loaded["eligible"].any()
    assert ledger.canonical(loaded, root=tmp_path).is_empty()

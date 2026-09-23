"""Prospective evidence must survive retries, late completion and reconciliation."""

import datetime as dt
import json
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
    return replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") == DAY)
            .then(value)
            .otherwise(pl.col(feature))
            .alias(feature)
        ),
    )


def record(root, *, panel=None, stamp=STAMP, **kwargs):
    return ledger.record_issue(
        panel or full_panel(), Ridge(0.1), DAY, issued_at=stamp, root=root, **kwargs
    )


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
    changed = replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") >= DAY)
            .then(999999.0)
            .otherwise(pl.col("price"))
            .alias("price")
        ),
    )
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
    late = ledger.issue(
        full_panel(), Ridge(0.1), DAY, issued_at=STAMP.replace(hour=12), allow_late=True
    )
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
    assert_frame_equal(
        replay.select("local_hour", "forecast"), frame.select("local_hour", "forecast")
    )
    assert ledger.canonical(frame, root=tmp_path).height == 24


def test_snapshot_checksums_reject_modified_input(tmp_path):
    from gpa.forecast import provenance

    frame = record(tmp_path)
    manifest = json.loads(
        (tmp_path / "issues" / frame["issue_id"][0] / "manifest.json").read_text()
    )
    digest = next(iter(manifest["artifacts"]["input_panel"].values()))
    (tmp_path / "blobs" / digest).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        provenance.read_snapshot(tmp_path, frame["issue_id"][0])
    with pytest.raises(ValueError, match="checksum"):
        ledger.canonical(frame, root=tmp_path)


def test_snapshot_shares_one_blob_across_two_issues_of_the_same_history(tmp_path):
    record(tmp_path, stamp=STAMP)
    before = sorted(p.name for p in (tmp_path / "blobs").glob("*"))
    record(tmp_path, panel=changed_target(full_panel(), 400.0), stamp=STAMP.replace(hour=10))
    after = sorted(p.name for p in (tmp_path / "blobs").glob("*"))

    # Both issues share the same history except the delivery day's own target
    # feature, which changed_target rewrites; only that day's blob is new.
    assert len(after) == len(before) + 1
    assert set(before) < set(after)


def test_snapshot_stores_only_the_fuels_the_panel_reads(tmp_path):
    from gpa.forecast import provenance

    generation = pl.DataFrame(
        {
            "zone": [ZONE.code] * 3,
            "ts_utc": [STAMP] * 3,
            "resolution_min": [60] * 3,
            "fuel": ["wind", "solar", "coal"],
            "gen_mw": [10.0, 20.0, 30.0],
            "source": ["test"] * 3,
        }
    )
    load = pl.DataFrame(
        {
            "zone": [ZONE.code],
            "ts_utc": [STAMP],
            "resolution_min": [60],
            "load_mw": [100.0],
            "source": ["test"],
        }
    )
    frame = record(
        tmp_path,
        source_frames={"generation": generation, "load": load},
        observed_at={"generation": STAMP, "load": STAMP},
    )
    meta = json.loads((tmp_path / "issues" / frame["issue_id"][0] / "manifest.json").read_text())
    assert meta["generation_fuels_stored"] == ["wind", "solar"]
    stored = provenance._read_partitioned(tmp_path, meta["artifacts"]["source_generation"])
    assert set(stored["fuel"]) == {"wind", "solar"}


def test_snapshot_refuses_to_silently_narrow_generation_if_fuels_drift(tmp_path, monkeypatch):
    """If RESIDUAL_LOAD_FUELS ever falls out of sync with what the panel
    actually reads, a minimal snapshot would misrepresent the archived
    evidence. This must fail loudly instead."""
    from gpa.forecast import provenance

    monkeypatch.setattr(provenance, "RESIDUAL_LOAD_FUELS", ("wind",))
    generation = pl.DataFrame(
        {
            "zone": [ZONE.code] * 2,
            "ts_utc": [STAMP] * 2,
            "resolution_min": [60] * 2,
            "fuel": ["wind", "solar"],
            "gen_mw": [10.0, 20.0],
            "source": ["test"] * 2,
        }
    )
    load = pl.DataFrame(
        {
            "zone": [ZONE.code],
            "ts_utc": [STAMP],
            "resolution_min": [60],
            "load_mw": [100.0],
            "source": ["test"],
        }
    )
    with pytest.raises(ValueError, match="fuels changed"):
        record(
            tmp_path,
            source_frames={"generation": generation, "load": load},
            observed_at={"generation": STAMP, "load": STAMP},
        )


def test_canonical_selects_one_whole_earliest_complete_issue(tmp_path):
    first = record(tmp_path)
    later = record(
        tmp_path, panel=changed_target(full_panel(), 400.0), stamp=STAMP.replace(hour=10)
    )
    combined = pl.concat([later, first])
    chosen = ledger.canonical(combined, root=tmp_path)
    assert chosen["issue_id"].unique().to_list() == first["issue_id"].unique().to_list()
    assert_frame_equal(chosen, first)


def test_canonical_does_not_stitch_partial_retries(tmp_path):
    source = full_panel()
    feature = source.features[0]
    first_panel = replace(
        source,
        frame=source.frame.with_columns(
            pl.when((pl.col("local_date") == DAY) & (pl.col("local_hour") < 12))
            .then(None)
            .otherwise(pl.col(feature))
            .alias(feature)
        ),
    )
    second_panel = replace(
        source,
        frame=source.frame.with_columns(
            pl.when((pl.col("local_date") == DAY) & (pl.col("local_hour") >= 12))
            .then(None)
            .otherwise(pl.col(feature))
            .alias(feature)
        ),
    )
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

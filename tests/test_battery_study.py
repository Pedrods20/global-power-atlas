"""Economic interpretation, paired uncertainty and local research artifacts."""

import datetime as dt
import json

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from gpa.battery_study import evaluate, paired_comparisons, read_study, risk_metrics, save_study
from gpa.cli import app
from tests.test_battery import DAY, predictions


def daily_sample(values, *, baseline=None, days=None):
    dates = days or [DAY + dt.timedelta(days=i) for i in range(len(values))]
    baseline = [0.0] * len(values) if baseline is None else baseline
    rows = []
    for model, profits in (("ridge", values), ("naive_previous_day", baseline)):
        rows.extend(
            {
                "local_date": day,
                "strategy": model,
                "power_mw": 1.0,
                "energy_mwh": 4.0,
                "profit_eur": value,
            }
            for day, value in zip(dates, profits, strict=True)
        )
    return pl.DataFrame(rows)


def test_risk_metrics_include_initial_loss_and_concentration_denominator():
    risk = risk_metrics(daily_sample([-10.0, 20.0, -30.0, 40.0]))
    row = risk.filter(pl.col("strategy") == "ridge").row(0, named=True)
    assert row["profit_eur"] == 20.0
    assert row["worst_day_eur"] == -30.0
    assert row["max_drawdown_eur"] == 30.0
    assert row["loss_days"] == 2
    assert row["top_5_days_share_positive_margin"] == 1.0
    assert row["worst_observed_month_eur"] == 20.0
    assert row["worst_month_observed_days"] == 4
    assert row["incremental_vs_best_naive_eur_mw"] == 20.0
    assert row["best_naive_selection"] == "retrospective_in_sample"
    losses = risk_metrics(daily_sample([-10.0, -20.0]))
    assert losses.filter(pl.col("strategy") == "ridge")["max_drawdown_eur"].item() == 30.0
    assert (
        losses.filter(pl.col("strategy") == "ridge")["top_5_days_share_positive_margin"].item()
        is None
    )


def test_pairing_uses_differences_and_is_reproducible():
    baseline = [float(i % 11) for i in range(70)]
    daily = daily_sample([value + 2.0 for value in baseline], baseline=baseline)
    one = paired_comparisons(daily, resamples=200, seed=7)
    two = paired_comparisons(daily.reverse(), resamples=200, seed=7)
    assert one.to_dicts() == two.to_dicts()
    row = one.filter(pl.col("strategy") == "ridge").row(0, named=True)
    assert row["incremental_eur_mw"] == 140.0
    assert row["ci_low_eur_mw"] == pytest.approx(140.0)
    assert row["ci_high_eur_mw"] == pytest.approx(140.0)
    assert row["status"] == "exploratory"


def test_short_sample_does_not_manufacture_a_confidence_interval():
    result = paired_comparisons(daily_sample([5.0] * 14), resamples=200)
    row = result.filter(pl.col("strategy") == "ridge").row(0, named=True)
    assert row["status"] == "insufficient_blocks"
    assert row["ci_low_eur_mw"] is None


def test_calendar_gaps_are_not_collapsed_into_adjacent_observations():
    dates = [DAY + dt.timedelta(days=7 * i) for i in range(10)]
    result = paired_comparisons(daily_sample([2.0] * 10, days=dates), resamples=200)
    row = result.filter(pl.col("strategy") == "ridge").row(0, named=True)
    assert row["calendar_blocks"] == 10
    assert row["paired_days"] == 10
    assert row["status"] == "insufficient_days"
    assert row["ci_low_eur_mw"] is None


def test_unmatched_days_raise_instead_of_cherry_picking():
    frame = daily_sample([1.0] * 10).filter(
        ~((pl.col("strategy") == "ridge") & (pl.col("local_date") == DAY))
    )
    with pytest.raises(ValueError, match=r"same.*days"):
        paired_comparisons(frame)


def test_evaluation_retains_costs_coverage_and_input_fingerprint():
    frame = predictions()
    result = evaluate(
        frame,
        durations_mwh=(1.0,),
        spec_kwargs={"variable_cost_eur_mwh": 2.0, "degradation_cost_eur_mwh": 3.0},
        resamples=200,
    )
    assert result.coverage.height == 5
    assert result.daily.height == 7
    assert result.comparisons.height == 12  # four non-naive strategies x three fixed naives
    assert result.assumptions["cost_basis"] == "absolute_grid_mwh_charge_plus_discharge"
    assert result.assumptions["cost_status"] == "user_assumptions_not_market_estimates"
    assert len(result.assumptions["prediction_sha256"]) == 64
    rows = result.daily.filter(pl.col("strategy") == "ridge")
    assert rows["operating_cost_eur"].item() > 0
    assert rows["profit_eur"].item() == pytest.approx(
        rows["gross_revenue_eur"].item()
        - rows["operating_cost_eur"].item()
        - rows["degradation_cost_eur"].item()
    )


def test_saved_study_is_immutable_and_detects_modified_artifacts(tmp_path):
    frame = predictions()
    result = evaluate(frame, durations_mwh=(1.0,), resamples=200)
    path = save_study(result, frame, tmp_path)
    metadata, tables = read_study(path)
    assert metadata["assumptions"] == json.loads(json.dumps(result.assumptions))
    assert tables["predictions"].equals(frame)
    assert tables["summary"].equals(result.summary)
    with pytest.raises(FileExistsError):
        save_study(result, frame, tmp_path)
    (path / "summary.parquet").write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        read_study(path)


def test_cannot_save_results_with_different_prediction_inputs(tmp_path):
    frame = predictions()
    result = evaluate(frame, durations_mwh=(1.0,), resamples=200)
    with pytest.raises(ValueError, match="input"):
        save_study(result, frame.with_columns(pl.col("forecast") + 1), tmp_path)


def test_study_replays_from_its_saved_inputs_and_explicit_assumptions(tmp_path):
    frame = predictions()
    result = evaluate(
        frame,
        durations_mwh=(1.0,),
        spec_kwargs={"variable_cost_eur_mwh": 2.0, "degradation_cost_eur_mwh": 3.0},
        resamples=200,
    )
    path = save_study(result, frame, tmp_path)
    metadata, tables = read_study(path)
    args = metadata["assumptions"]
    replay = evaluate(
        tables["predictions"],
        **{
            key: args[key]
            for key in (
                "model_names",
                "durations_mwh",
                "spec_kwargs",
                "block_days",
                "resamples",
                "seed",
            )
        },
    )
    for table in ("dispatch", "summary", "coverage", "daily", "risk", "comparisons"):
        assert_frame_equal(tables[table], getattr(replay, table))


def test_local_study_command_does_not_read_or_mutate_the_live_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path / "no-live-data"))
    input_path = tmp_path / "predictions.parquet"
    predictions().write_parquet(input_path)
    output = tmp_path / "studies"
    result = CliRunner().invoke(
        app,
        [
            "battery-study",
            "--predictions",
            str(input_path),
            "--output",
            str(output),
            "--resamples",
            "200",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Research study" in result.output
    assert len(list(output.glob("*/manifest.json"))) == 1
    assert not (tmp_path / "no-live-data").exists()

import datetime as dt

from gpa.schema import validate
from gpa.sources.eia import EiaSource
from gpa.zones import get_zone


def test_eia_hour_only_strings_and_fuel_aggregation(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "test-key")
    source = EiaSource()
    monkeypatch.setattr(
        source,
        "_paginate",
        lambda *args: [
            {"period": "2026-06-01T00", "value": "25", "fueltype": "SUN"},
            {"period": "2026-06-01T01", "value": "30", "fueltype": "NG"},
        ],
    )
    start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    for dataset in ("load", "generation"):
        result = source.fetch(get_zone("ERCOT"), dataset, start, start + dt.timedelta(days=1))
        validate(result, dataset)
        assert result.height == 2
        assert result["ts_utc"][0] == start

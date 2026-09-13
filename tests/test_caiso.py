"""CAISO OASIS adapter tests.

Two things here are specific to OASIS and both fail silently if handled
carelessly, which is why each has a named test:

- the file interleaves five LMP components for every interval, so taking every
  row would store congestion and loss terms as though each were a price;
- the throttle reply arrives as 200 OK carrying HTML, not as 429, so the shared
  retry layer cannot see it.

Everything runs against a recorded fixture or a mock transport. Nothing here
reaches the network.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import httpx
import pytest

from gpa.sources import caiso
from gpa.sources.base import UpstreamError
from gpa.sources.caiso import LMP_COMPONENTS, CaisoSource, parse_oasis_lmp
from gpa.zones import get_zone

HEADER = (
    "INTERVALSTARTTIME_GMT,INTERVALENDTIME_GMT,OPR_DT,OPR_HR,OPR_INTERVAL,"
    "NODE_ID_XML,NODE_ID,NODE,MARKET_RUN_ID,LMP_TYPE,XML_DATA_ITEM,"
    "PNODE_RESMRID,GRP_TYPE,POS,MW,GROUP"
)


def row(hour: int, component: str, value: float, node: str = "TH_SP15_GEN-APND") -> str:
    start = f"2026-09-01T{hour:02d}:00:00-00:00"
    end = f"2026-09-01T{hour + 1:02d}:00:00-00:00"
    item = {
        "LMP": "LMP_PRC",
        "MCC": "LMP_CONG_PRC",
        "MCE": "LMP_ENE_PRC",
        "MCL": "LMP_LOSS_PRC",
        "MGHG": "LMP_GHG_PRC",
    }[component]
    return (
        f"{start},{end},2026-09-01,{hour + 1},0,{node},{node},{node},DAM,"
        f"{component},{item},{node},ALL_APNODES,0,{value},1"
    )


def fixture(hours: int = 2) -> str:
    """A CSV shaped exactly like the live archive, all five components per hour."""
    lines = [HEADER]
    for hour in range(hours):
        lines.append(row(hour, "LMP", 50.0 + hour))
        lines.append(row(hour, "MCE", 45.0 + hour))
        lines.append(row(hour, "MCC", 3.0))
        lines.append(row(hour, "MCL", 1.0))
        lines.append(row(hour, "MGHG", 1.0))
    return "\n".join(lines) + "\n"


# --- Component filtering ----------------------------------------------------


def test_only_the_total_lmp_component_is_stored() -> None:
    """Five components share every interval; four of them are not prices.

    Keeping all of them would write congestion and loss into the price column
    and quintuple the row count.
    """
    parsed = parse_oasis_lmp(fixture(hours=3), "CAISO", "TH_SP15_GEN-APND")

    assert parsed.height == 3
    assert parsed["price"].to_list() == [50.0, 51.0, 52.0]


def test_the_component_map_documents_the_californian_carbon_term() -> None:
    """MGHG exists because California prices carbon into dispatch.

    It is not a component any European or Australian LMP carries, and a reader
    decomposing a Californian price needs to know it is there.
    """
    assert LMP_COMPONENTS["MGHG"] == "greenhouse_gas"
    assert set(LMP_COMPONENTS) == {"LMP", "MCE", "MCC", "MCL", "MGHG"}


# --- Canonical shape --------------------------------------------------------


def test_timestamps_are_utc_and_interval_starting() -> None:
    """OASIS already publishes UTC interval-start, so no shift is applied.

    This is the opposite of AEMO, which stamps interval-ending and needs the
    resolution subtracted. Getting the two confused moves a series by an hour.
    """
    parsed = parse_oasis_lmp(fixture(hours=2), "CAISO", "TH_SP15_GEN-APND")

    assert parsed["ts_utc"][0] == dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.UTC)
    assert parsed["ts_utc"][1] == dt.datetime(2026, 9, 1, 1, 0, tzinfo=dt.UTC)


def test_the_frame_matches_the_price_contract() -> None:
    from gpa.schema import validate

    parsed = parse_oasis_lmp(fixture(hours=4), "CAISO", "TH_SP15_GEN-APND")
    validated = validate(parsed, "price")

    assert validated["currency"].unique().to_list() == ["USD"]
    assert validated["resolution_min"].unique().to_list() == [60]
    assert validated["zone"].unique().to_list() == ["CAISO"]


def test_rows_are_sorted_and_deduplicated() -> None:
    """OASIS returns rows unordered and repeats one on a window boundary."""
    doubled = fixture(hours=2) + row(0, "LMP", 99.0) + "\n"
    parsed = parse_oasis_lmp(doubled, "CAISO", "TH_SP15_GEN-APND")

    assert parsed.height == 2
    assert parsed["ts_utc"].is_sorted()


def test_negative_prices_survive_parsing() -> None:
    """California reaches negative day-ahead prices in spring solar surplus."""
    csv = "\n".join([HEADER, row(0, "LMP", -12.5), row(0, "MCE", -14.0)]) + "\n"
    parsed = parse_oasis_lmp(csv, "CAISO", "TH_SP15_GEN-APND")

    assert parsed["price"].to_list() == [-12.5]


# --- Failure handling -------------------------------------------------------


def test_a_changed_schema_is_rejected() -> None:
    with pytest.raises(UpstreamError, match="missing expected columns"):
        parse_oasis_lmp("A,B,C\n1,2,3\n", "CAISO", "TH_SP15_GEN-APND")


def test_a_file_with_no_total_component_yields_an_empty_frame() -> None:
    csv = "\n".join([HEADER, row(0, "MCC", 3.0), row(0, "MCL", 1.0)]) + "\n"
    assert parse_oasis_lmp(csv, "CAISO", "TH_SP15_GEN-APND").is_empty()


# --- Empty results ----------------------------------------------------------

NO_DATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<m:OASISReport xmlns:m="http://www.caiso.com/soa/OASISReport_v1.xsd">
<m:MessagePayload><m:RTO><m:name>CAISO</m:name>
<m:ERROR>
<m:ERR_CODE>1000</m:ERR_CODE>
<m:ERR_DESC>No data returned for the specified selection</m:ERR_DESC>
</m:ERROR>
</m:RTO></m:MessagePayload></m:OASISReport>
"""

OTHER_ERROR_XML = NO_DATA_XML.replace("1000", "1001").replace(
    "No data returned for the specified selection", "Invalid node identifier"
)


def test_a_no_data_window_yields_an_empty_frame_not_an_error() -> None:
    """OASIS reports emptiness as XML inside the zip, still under a 200.

    A day-ahead window running past the last published session has no rows by
    definition, so this must be an ordinary empty result. Handing the XML to
    the CSV parser instead produces a frame whose only column is the XML
    declaration, which is exactly how this surfaced during the first backfill.
    """
    assert parse_oasis_lmp(NO_DATA_XML, "CAISO", "TH_SP15_GEN-APND").is_empty()


def test_any_other_oasis_error_is_raised_with_its_description() -> None:
    """Only code 1000 is benign; everything else must be loud."""
    with pytest.raises(UpstreamError, match="Invalid node identifier"):
        parse_oasis_lmp(OTHER_ERROR_XML, "CAISO", "TH_SP15_GEN-APND")


# --- Throttling -------------------------------------------------------------


def zipped(csv: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("PRC_LMP_DAM_v1.csv", csv)
    return buffer.getvalue()


THROTTLE_HTML = (
    "<html><body><p>CAISO Acceptable Use Policy Violation. "
    "Please retry your request after 5 seconds.</p></body></html>"
)


def test_an_html_throttle_reply_is_retried_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The throttle arrives as 200 OK, so the shared retry layer never sees it.

    Parsing that body as data would silently store an empty month, which is the
    worst outcome available: no error and no data.
    """
    monkeypatch.setattr(caiso.time, "sleep", lambda _seconds: None)
    responses = [
        httpx.Response(200, text=THROTTLE_HTML),
        httpx.Response(200, content=zipped(fixture(hours=2))),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0) if len(responses) > 1 else responses[0]

    monkeypatch.setattr(
        caiso,
        "http_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    source = CaisoSource()
    frame = source.fetch(
        get_zone("CAISO"),
        "price",
        dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
    )

    assert frame.height == 2


def test_persistent_throttling_raises_rather_than_returning_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(caiso.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        caiso,
        "http_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=THROTTLE_HTML))
        ),
    )

    with pytest.raises(UpstreamError, match="throttl"):
        CaisoSource().fetch(
            get_zone("CAISO"),
            "price",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        )


def test_an_unexpected_non_zip_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(caiso.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        caiso,
        "http_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>down</html>"))
        ),
    )

    with pytest.raises(UpstreamError, match="non-zip"):
        CaisoSource().fetch(
            get_zone("CAISO"),
            "price",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        )


# --- Configuration ----------------------------------------------------------


def test_the_window_cap_leaves_room_for_calendar_day_counting() -> None:
    """OASIS counts calendar days touched, not elapsed duration.

    A 31-day window starting mid-afternoon spans 32 distinct calendar days and
    is rejected with ERR_CODE 1004. Thirty fits from any starting time, which
    matters because a backfill starts at whatever time it is run.
    """
    assert CaisoSource().max_window_days == 30


def test_the_zone_declares_a_node_and_a_market() -> None:
    zone = get_zone("CAISO")

    assert zone.source_keys["caiso_node"] == "TH_SP15_GEN-APND"
    assert zone.source_keys["caiso_market"] == "DAM"
    assert zone.sources["price"] == "caiso"


def test_a_zone_without_a_node_is_rejected() -> None:
    from dataclasses import replace

    zone = replace(get_zone("CAISO"), source_keys={"eia_respondent": "CISO"})

    with pytest.raises(UpstreamError, match="caiso_node"):
        CaisoSource().fetch(
            zone,
            "price",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        )


def test_the_adapter_refuses_datasets_it_cannot_produce() -> None:
    with pytest.raises(ValueError, match="price only"):
        CaisoSource().fetch(
            get_zone("CAISO"),
            "generation",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        )

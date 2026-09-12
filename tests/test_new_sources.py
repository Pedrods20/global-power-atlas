"""Small contract examples with explicit units and interval boundaries."""

import datetime as dt

from gpa.schema import validate
from gpa.sources.ccee import parse_ccee
from gpa.sources.jepx import parse_jepx


def test_jepx_period_48_and_kwh_to_mwh():
    raw = "年月日,時刻コード,エリアプライス東京(円/kWh)\n2026/06/01,1,0.01\n2026/06/01,48,10.5\n"
    frame = parse_jepx(raw)
    validate(frame, "price")
    assert frame["price"].to_list() == [10, 10500]
    assert frame["ts_utc"][0] == dt.datetime(2026, 5, 31, 15, tzinfo=dt.UTC)
    assert frame["ts_utc"][1] == dt.datetime(2026, 6, 1, 14, 30, tzinfo=dt.UTC)


def test_ccee_keeps_submarkets_separate_and_preserves_decimal_comma():
    raw = (
        "MES_REFERENCIA;SUBMERCADO;PERIODO_COMERCIALIZACAO;DIA;HORA;PLD_HORA\n"
        "202606;SUDESTE;152;1;0;123,45\n"
        "202606;SUL;152;1;0;200,00\n"
    )
    frame = parse_ccee(raw, "BR-SECO", "SUDESTE")
    validate(frame, "price")
    assert frame.height == 1
    assert frame["price"][0] == 123.45
    assert frame["ts_utc"][0] == dt.datetime(2026, 6, 1, 3, tzinfo=dt.UTC)

"""JEPX's public fiscal-year spot archive; Tokyo area price, JPY/kWh to JPY/MWh.

Source: https://www.jepx.jp/electricpower/market-data/spot/
The archive's period code 1 means 00:00-00:30 JST. Bid and traded quantities
are not system demand and are deliberately not ingested as load.
"""

import io

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, _request, http_client


class JepxSource:
    name = "jepx"
    datasets = ("price",)
    max_window_days = None

    def fetch(self, zone, dataset, start, end):
        if dataset != "price":
            raise ValueError("JEPX archive supplies prices, not system load or generation")
        frames = []
        # The files cover April-March fiscal years, not calendar years.
        with http_client() as client:
            for year in range(start.year - (start.month < 4), end.year + 1):
                url = f"https://www.jepx.jp/market/excel/spot_{year}.csv"
                response = _request("GET", url, client=client)
                if response.status_code == 404:
                    continue
                if not response.is_success or not response.content:
                    raise UpstreamError(f"JEPX archive unavailable for fiscal year {year}")
                text = response.content.decode("cp932")
                frames.append(parse_jepx(text, zone.code, zone.source_keys["jepx_area"]))
        if not frames:
            return empty_frame("price")
        return (
            pl.concat(frames)
            .filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
            .unique(["zone", "ts_utc"])
            .sort("ts_utc")
        )


def parse_jepx(text, zone_code="JP-TOKYO", area="東京"):
    frame = pl.read_csv(io.StringIO(text), infer_schema_length=0)
    date_column = "年月日" if "年月日" in frame.columns else "受渡日"
    price_column = f"エリアプライス{area}(円/kWh)"
    if not {date_column, "時刻コード", price_column}.issubset(frame.columns):
        raise UpstreamError("JEPX CSV columns changed")
    period = pl.col("時刻コード").cast(pl.Int32, strict=True)
    if frame.filter(~period.is_between(1, 48)).height:
        raise UpstreamError("JEPX period must be between 1 and 48")
    return (
        frame.select(
            pl.lit(zone_code).alias("zone"),
            (
                pl.col(date_column).str.to_datetime("%Y/%m/%d").dt.replace_time_zone("Asia/Tokyo")
                + pl.duration(minutes=(period - 1) * 30)
            )
            .dt.convert_time_zone("UTC")
            .cast(UTC_DATETIME)
            .alias("ts_utc"),
            pl.lit(30, dtype=pl.Int16).alias("resolution_min"),
            (pl.col(price_column).cast(pl.Float64, strict=False) * 1000).alias("price"),
            pl.lit("JPY").alias("currency"),
            pl.lit("jepx").alias("source"),
        )
        .drop_nulls("price")
        .sort("ts_utc")
    )

import zipfile
from datetime import UTC, datetime

import pytest

from v9.data import binance as bn

HEADER = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
          "taker_buy_volume,taker_buy_quote_volume,ignore\n")
ROW_MS = ("1785542400000,62859.90,62945.40,62859.80,62927.20,703.907,1785543299999,"
          "44287307.3,16339,411.591,25895836.3,0\n")
ROW_US = ("1785542400000000,62887.88,62970.00,62887.87,62942.00,182.20936,1785543299999999,"
          "11469641.7,12674,45.99193,2894770.87,0\n")


def _zip(tmp_path, name: str, text: str):
    p = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(f"{name}.csv", text)
    return p


@pytest.mark.parametrize("text", [HEADER + ROW_MS, ROW_MS, ROW_US])
def test_klines_header_and_timestamp_units(tmp_path, text):
    df = bn.parse_klines(_zip(tmp_path, "k", text))
    assert df.height == 1
    assert df["open_time"][0] == datetime(2026, 8, 1, tzinfo=UTC)
    assert df["taker_buy_base"][0] > 0 and df["trades"].dtype.is_integer()


def test_funding_time_snapped_to_minute(tmp_path):
    text = "calc_time,funding_interval_hours,last_funding_rate\n1785542400001,8,0.00004123\n"
    df = bn.parse_funding(_zip(tmp_path, "f", text))
    assert df["time"][0] == datetime(2026, 8, 1, tzinfo=UTC)
    assert df["rate"][0] == pytest.approx(0.00004123)


def test_metrics_columns(tmp_path):
    text = (",".join(bn.METRIC_COLUMNS) + "\n"
            "2020-09-01 00:00:00,BTCUSDT,39080.231,456144339.2,1.17547937,1.23012681,1.35731217,0.78373373\n")
    df = bn.parse_metrics(_zip(tmp_path, "m", text))
    assert df.columns[:3] == ["time", "oi", "oi_value"]
    assert df["time"][0] == datetime(2020, 9, 1, tzinfo=UTC)


def test_months_and_urls():
    from datetime import date
    assert bn.months(date(2020, 11, 1), date(2021, 2, 1)) == ["2020-11", "2020-12", "2021-01", "2021-02"]
    assert bn.kline_url("um", "BTCUSDT", "15m", "2020-01").endswith(
        "futures/um/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2020-01.zip")


def test_metrics_empty_fields_become_null(tmp_path):
    text = (",".join(bn.METRIC_COLUMNS) + "\n"
            "2021-01-01 00:05:00,BTCUSDT,39080.231,456144339.2,,,,\n")
    df = bn.parse_metrics(_zip(tmp_path, "m", text))
    assert df["oi"][0] > 0 and df["top_account_ratio"][0] is None

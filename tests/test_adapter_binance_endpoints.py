"""The base URLs, read out of the real ``config/venues.yaml``.

``03`` §6.1 puts them there ("三个 base URL 分开配，标 `官方`") and AGENTS §2.3
bans a hard-coded host anywhere else. The reason both say so is ``04`` §2's
change notice: on 2026-04-23 the legacy ``wss://…/stream`` endpoint stopped
pushing **permanently** and the streams moved into groups. A host spelled into
code is a host nobody re-checks the next time that happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import VENUES_PATH
from hlens_core.adapters.binance import (
    WS_FORCE_ORDER_ALL,
    WS_MARK_PRICE_ALL,
    BinanceEndpoints,
    EndpointError,
    ws_mark_price_stream,
)
from hlens_core.adapters.binance.endpoints import (
    BINANCE_FUTURES_DATA_PATHS,
    REST_EXCHANGE_INFO,
    REST_PREMIUM_INDEX,
)


def _endpoints() -> BinanceEndpoints:
    return BinanceEndpoints.load(VENUES_PATH)


def test_the_four_base_urls_load_from_the_repositorys_own_config() -> None:
    endpoints = _endpoints()
    assert endpoints.rest.startswith("https://")
    assert endpoints.rest_mirror.startswith("https://")
    assert endpoints.ws_market.startswith("wss://")
    assert endpoints.ws_public.startswith("wss://")


def test_the_market_and_public_groups_are_different_endpoints() -> None:
    """``04`` §2: "M4 的 ``@depth`` 属 ``/public``、``@aggTrade`` 属 ``/market``,
    分属两组 → 至少两条 WS 连接"，and the two can never be merged into one."""
    endpoints = _endpoints()
    assert endpoints.ws_market != endpoints.ws_public
    assert endpoints.ws_market.endswith("/market")
    assert endpoints.ws_public.endswith("/public")


def test_the_legacy_stream_endpoint_is_nowhere_in_the_config() -> None:
    """The legacy ``…/stream`` base stopped pushing on 2026-04-23. Connecting to
    it is the failure mode that looks like a silent venue rather than an error,
    so the address must not be reachable from configuration at all."""
    body = "\n".join(
        line
        for line in VENUES_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "/stream" not in body
    endpoints = _endpoints()
    for url in (endpoints.ws_market, endpoints.ws_public):
        assert not url.endswith("/stream")


def test_both_m1_streams_are_built_on_the_market_group() -> None:
    endpoints = _endpoints()
    url = endpoints.ws_url((WS_MARK_PRICE_ALL, WS_FORCE_ORDER_ALL))
    assert url.startswith(endpoints.ws_market)
    assert WS_MARK_PRICE_ALL in url and WS_FORCE_ORDER_ALL in url


def test_a_stream_url_needs_at_least_one_stream() -> None:
    with pytest.raises(EndpointError, match="at least one stream"):
        _endpoints().ws_url(())


def test_rest_urls_join_the_configured_host_with_the_adapters_path() -> None:
    endpoints = _endpoints()
    assert endpoints.rest_url(REST_PREMIUM_INDEX) == f"{endpoints.rest}{REST_PREMIUM_INDEX}"
    assert (
        endpoints.rest_url(REST_EXCHANGE_INFO, mirror=True)
        == f"{endpoints.rest_mirror}{REST_EXCHANGE_INFO}"
    )


def test_the_mirror_is_the_same_paths_on_another_host() -> None:
    """``04`` §6: ``fapi`` answers 451 from a US egress and the ``www`` mirror
    answers 200 on the **same paths**. Whether it is needed is an on-machine
    measurement (``04`` §11 第 13 项), never something a failed call decides."""
    endpoints = _endpoints()
    assert endpoints.rest_base() == endpoints.rest
    assert endpoints.rest_base(mirror=True) == endpoints.rest_mirror


def test_per_symbol_stream_names_are_lower_case() -> None:
    assert ws_mark_price_stream("1000PEPEUSDT") == "1000pepeusdt@markPrice@1s"


def test_the_three_futures_data_paths_are_the_three_kinds() -> None:
    assert len(BINANCE_FUTURES_DATA_PATHS) == 3
    assert all(p.startswith("/futures/data/") for p in BINANCE_FUTURES_DATA_PATHS)
    assert not any("topLongShortAccountRatio" in p for p in BINANCE_FUTURES_DATA_PATHS)


# --------------------------------------------------------------------------- #
# Refusals — all of them before a socket is opened
# --------------------------------------------------------------------------- #
def _config(tmp_path: Path, endpoints_block: str) -> Path:
    path = tmp_path / "venues.yaml"
    path.write_text(
        "schema: 1\nvenues:\n  binance:\n    endpoints:\n" + endpoints_block,
        encoding="utf-8",
    )
    return path


_GOOD = (
    '      rest: {value: "https://example.invalid", source: official, doc: d}\n'
    '      rest_mirror: {value: "https://mirror.invalid", source: official, doc: d}\n'
    '      ws_market: {value: "wss://ws.invalid/market", source: official, doc: d}\n'
    '      ws_public: {value: "wss://ws.invalid/public", source: official, doc: d}\n'
)


def test_a_complete_block_loads(tmp_path: Path) -> None:
    assert BinanceEndpoints.load(_config(tmp_path, _GOOD)).ws_market.endswith("/market")


def test_a_missing_key_refuses_to_load(tmp_path: Path) -> None:
    block = "\n".join(line for line in _GOOD.splitlines() if "ws_public" not in line) + "\n"
    with pytest.raises(EndpointError, match="ws_public: missing"):
        BinanceEndpoints.load(_config(tmp_path, block))


def test_a_guessed_host_refuses_to_load(tmp_path: Path) -> None:
    """``03`` §6.1 tags these ``官方``. ``measured`` would mean somebody worked
    out where the exchange is by connecting to it, which is not how this
    repository finds anything out."""
    with pytest.raises(EndpointError, match="somebody guessed"):
        BinanceEndpoints.load(_config(tmp_path, _GOOD.replace("official", "measured", 1)))


def test_a_plain_http_host_refuses_to_load(tmp_path: Path) -> None:
    with pytest.raises(EndpointError, match="expected a https"):
        BinanceEndpoints.load(_config(tmp_path, _GOOD.replace("https://example", "http://example")))


def test_a_trailing_slash_refuses_to_load(tmp_path: Path) -> None:
    """Paths are joined verbatim, so a trailing slash silently makes every URL
    a double slash — which some hosts answer and some redirect."""
    with pytest.raises(EndpointError, match="trailing slash"):
        BinanceEndpoints.load(
            _config(tmp_path, _GOOD.replace("https://example.invalid", "https://example.invalid/"))
        )


def test_a_missing_endpoints_block_refuses_to_load(tmp_path: Path) -> None:
    path = tmp_path / "venues.yaml"
    path.write_text("schema: 1\nvenues:\n  binance:\n    buckets: {}\n", encoding="utf-8")
    with pytest.raises(EndpointError, match=r"03 §6\.1 requires"):
        BinanceEndpoints.load(path)

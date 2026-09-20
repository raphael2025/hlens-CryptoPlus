"""Hyperliquid's hosts come from ``config/venues.yaml``; its paths do not.

AGENTS §2.3 bans hard-coded hosts outside that file, and ``03`` §6.1 requires
the base URLs to be in it. What must *not* be in it is how to call a venue —
the ledger reads that file and "does accounting and admission only, and never
sends a request".
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import VENUES_PATH
from hlens_core.adapters.hyperliquid import (
    INFO_PATH,
    MAX_CANDLE_HISTORY_ROWS,
    MAX_ROWS_PER_RANGED_RESPONSE,
    WS_CHANNEL_ALL_MIDS,
    WS_GROUP_INFO,
    EndpointError,
    HyperliquidEndpoints,
    subscribe_frame,
)


def test_the_hosts_are_read_from_the_repositorys_own_config() -> None:
    endpoints = HyperliquidEndpoints.load(VENUES_PATH)
    assert endpoints.rest.startswith("https://")
    assert endpoints.ws.startswith("wss://")
    # One host and one socket: 04 §6 records this venue answering 200 from both
    # a US and a non-US egress, so there is no mirror and no switch to get wrong.
    assert endpoints.info_url() == f"{endpoints.rest}{INFO_PATH}"
    assert endpoints.ws_url() == endpoints.ws


def test_no_host_is_spelled_anywhere_in_the_adapter_package() -> None:
    """The point of reading them from the config file is that nothing else
    knows them. A second spelling in code is a host nobody re-checks."""
    endpoints = HyperliquidEndpoints.load(VENUES_PATH)
    package = Path(__file__).resolve().parents[1] / (
        "packages/hlens-core/src/hlens_core/adapters/hyperliquid"
    )
    hosts = {endpoints.rest, endpoints.ws}
    for source in package.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for host in hosts:
            assert host not in text, f"{source.name} spells a host out: {host}"


def test_the_path_is_not_in_the_config_file() -> None:
    """``config/venues.yaml``'s header: endpoint paths belong to the adapter."""
    document = yaml.safe_load(VENUES_PATH.read_text(encoding="utf-8"))
    node = document["venues"]["hyperliquid"]["endpoints"]
    assert set(node) == {"rest", "ws"}
    for entry in node.values():
        assert entry["source"] == "official"
        assert INFO_PATH not in entry["value"]


def test_a_guessed_host_is_refused(tmp_path: Path) -> None:
    """A ``measured`` or ``unverified`` base URL would mean somebody guessed
    where the exchange is — which is not a thing to find out by connecting."""
    bad = tmp_path / "venues.yaml"
    bad.write_text(
        "venues:\n"
        "  hyperliquid:\n"
        "    endpoints:\n"
        "      rest:\n"
        "        value: https://example.invalid\n"
        "        source: unverified\n"
        "      ws:\n"
        "        value: wss://example.invalid/ws\n"
        "        source: official\n",
        encoding="utf-8",
    )
    with pytest.raises(EndpointError, match="guessed"):
        HyperliquidEndpoints.load(bad)


def test_a_missing_endpoints_block_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "venues.yaml"
    bad.write_text("venues:\n  hyperliquid:\n    buckets: {}\n", encoding="utf-8")
    with pytest.raises(EndpointError, match="endpoints is missing"):
        HyperliquidEndpoints.load(bad)


def test_a_wrong_scheme_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "venues.yaml"
    bad.write_text(
        "venues:\n"
        "  hyperliquid:\n"
        "    endpoints:\n"
        "      rest:\n"
        "        value: https://example.invalid\n"
        "        source: official\n"
        "      ws:\n"
        "        value: https://example.invalid/ws\n"
        "        source: official\n",
        encoding="utf-8",
    )
    with pytest.raises(EndpointError, match="wss://"):
        HyperliquidEndpoints.load(bad)


def test_the_subscription_frame_is_what_the_venue_expects() -> None:
    """Binance names its streams in the URL; Hyperliquid connects first and
    then sends this (``04`` §3). That difference is why this adapter's
    WebSocket protocol has a ``send`` and Binance's does not."""
    assert subscribe_frame(WS_CHANNEL_ALL_MIDS) == {
        "method": "subscribe",
        "subscription": {"type": "allMids"},
    }
    assert WS_GROUP_INFO == "info"


def test_the_two_response_limits_are_two_different_numbers() -> None:
    """``04`` §13 第 3 行 exists because they were once read as one: 500 is a
    *pagination* cap on any ranged response, 5000 is ``candleSnapshot``'s
    *history depth* — about 3.5 days at 1m, which is why HL klines are
    ``partial_history``."""
    assert MAX_ROWS_PER_RANGED_RESPONSE == 500
    assert MAX_CANDLE_HISTORY_ROWS == 5000
    assert MAX_CANDLE_HISTORY_ROWS != MAX_ROWS_PER_RANGED_RESPONSE

"""Preflight with a mocked transport: nothing here touches the network."""

from __future__ import annotations

import httpx
import pytest
import respx

from hlens_core.contracts import now_ms
from hlens_core.preflight import (
    CheckKind,
    Verdict,
    normalize_asn,
    probe_coverage,
    resolve_egress,
    run_preflight,
)
from hlens_core.preflight.probes import _dig, _to_ms
from hlens_core.ratelimit import load_venues

IPINFO = "https://ipinfo.io/json"
IFCONFIG = "https://ifconfig.me/all.json"


def mock_egress(ip="203.0.113.9", org="AS24757 Ethio Telecom", country="ET", second_ip=None):
    respx.get(IPINFO).mock(
        return_value=httpx.Response(200, json={"ip": ip, "org": org, "country": country})
    )
    respx.get(IFCONFIG).mock(
        return_value=httpx.Response(200, json={"ip_addr": second_ip or ip})
    )


def mock_venue(name: str, status=200, json_body=None, headers=None):
    spec = load_venues()[name]
    for url in {spec.raw.get("probe_url"), spec.raw.get("time_url")}:
        if not url:
            continue
        respx.route(url=url).mock(
            return_value=httpx.Response(
                status, json=json_body if json_body is not None else {},
                headers=headers or {"date": "Sat, 12 Sep 2026 07:08:48 GMT"},
            )
        )


# -- egress ------------------------------------------------------------------


def test_normalize_asn_forms():
    assert normalize_asn("AS24757 Ethio Telecom") == "AS24757"
    assert normalize_asn("as132203") == "AS132203"
    assert normalize_asn("132203") == "AS132203"
    assert normalize_asn(None) is None


@respx.mock
async def test_egress_merges_two_observers():
    mock_egress()
    e = await resolve_egress()
    assert (e.ip, e.asn, e.country) == ("203.0.113.9", "AS24757", "ET")
    assert e.agreed and len(e.sources) == 2


@respx.mock
async def test_egress_reports_a_disagreement_rather_than_picking_one():
    mock_egress(second_ip="198.51.100.1")
    e = await resolve_egress()
    assert not e.agreed
    assert "ip:" in e.conflicts[0]


@respx.mock
async def test_egress_tolerates_one_observer_failing():
    mock_egress()
    respx.get(IFCONFIG).mock(return_value=httpx.Response(503))
    e = await resolve_egress()
    assert e.ip == "203.0.113.9" and e.sources == ["ipinfo.io"]
    assert e.errors and "http 503" in e.errors[0]


@respx.mock
async def test_egress_tolerates_total_failure():
    respx.get(IPINFO).mock(side_effect=httpx.ConnectError("no route"))
    respx.get(IFCONFIG).mock(side_effect=httpx.ConnectError("no route"))
    e = await resolve_egress()
    assert e.ip is None and len(e.errors) == 2


# -- probes ------------------------------------------------------------------


def test_dig_walks_dicts_and_list_indices():
    assert _dig({"data": [{"ts": "5"}]}, "data.0.ts") == "5"
    assert _dig({"result": {"timeNano": 1}}, "result.timeNano") == 1
    assert _dig({"a": 1}, "b.c") is None


def test_to_ms_units():
    assert _to_ms("1789196927034", "ms") == 1789196927034
    assert _to_ms("1789196927034000000", "ns") == 1789196927034
    assert _to_ms(None, "ms") is None


@respx.mock
async def test_reachable_venue_is_ok():
    mock_egress()
    mock_venue("binance", json_body={"serverTime": now_ms()})
    report = await run_preflight(["binance"])
    reach = next(c for c in report.checks if c.kind is CheckKind.reachability)
    assert reach.ok and reach.http_status == 200
    assert report.venue_ok("binance")


@respx.mock
async def test_451_is_classified_as_a_geo_block():
    mock_egress()
    mock_venue("binance", status=451)
    report = await run_preflight(["binance"])
    reach = next(c for c in report.checks if c.kind is CheckKind.reachability)
    assert not reach.ok and reach.geo_blocked
    assert "geo/policy block" in reach.detail
    assert not report.ok


@respx.mock
async def test_bybit_403_geo_block_vs_rate_limit():
    mock_egress()
    respx.route(url=load_venues()["bybit"].raw["probe_url"]).mock(
        return_value=httpx.Response(403, text="Access denied by CDN")
    )
    report = await run_preflight(["bybit"])
    assert next(c for c in report.checks if c.kind is CheckKind.reachability).geo_blocked

    respx.route(url=load_venues()["bybit"].raw["probe_url"]).mock(
        return_value=httpx.Response(403, text="access too frequent")
    )
    report = await run_preflight(["bybit"])
    reach = next(c for c in report.checks if c.kind is CheckKind.reachability)
    assert not reach.geo_blocked, "rate limiting is not a geo block"


@respx.mock
async def test_clock_skew_is_a_warning_not_a_failure():
    mock_egress()
    mock_venue("binance", json_body={"serverTime": 1_000_000_000_000})
    report = await run_preflight(["binance"])
    clock = next(c for c in report.checks if c.kind is CheckKind.clock)
    assert clock.verdict is Verdict.warn and not clock.ok
    assert "skew" in clock.detail


@respx.mock
async def test_rate_limit_headers_are_captured():
    mock_egress()
    mock_venue("binance", json_body={"serverTime": now_ms()},
               headers={"X-MBX-USED-WEIGHT-1M": "3", "date": "Sat, 12 Sep 2026 07:08:48 GMT"})
    report = await run_preflight(["binance"])
    reach = next(c for c in report.checks if c.kind is CheckKind.reachability)
    assert reach.data["headers"]["x-mbx-used-weight-1m"] == "3"


@respx.mock
async def test_unreachable_venue_does_not_hide_the_others():
    mock_egress()
    mock_venue("binance", json_body={"serverTime": now_ms()})
    respx.route(url=load_venues()["gate"].raw["probe_url"]).mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    report = await run_preflight(["binance", "gate"])
    assert report.venue_ok("binance")
    assert not report.venue_ok("gate")
    assert "ConnectTimeout" in "".join(c.detail for c in report.checks if c.venue == "gate")


@respx.mock
async def test_report_renders_a_table():
    mock_egress()
    mock_venue("binance", json_body={"serverTime": now_ms()})
    table = (await run_preflight(["binance"])).to_table()
    assert "venue" in table and "reachability" in table
    assert "egress: ip=203.0.113.9 asn=AS24757 country=ET" in table


# -- coverage ----------------------------------------------------------------


async def test_coverage_matrix_flags_empty_facets():
    async def has_ratio(_venue: str, symbol: str) -> bool:
        return symbol != "ILLIQUID"

    async def boom(_venue: str, _symbol: str) -> bool:
        raise RuntimeError("endpoint gone")

    rows = await probe_coverage(
        "binance", ["BTC", "ILLIQUID"], {"long_short_ratio": has_ratio, "taker_flow": boom}
    )
    ratio = next(r for r in rows if r.data["endpoint"] == "long_short_ratio")
    assert ratio.data["matrix"] == {"BTC": True, "ILLIQUID": False}
    assert ratio.verdict is Verdict.warn
    assert ratio.ok, "an empty facet greys out a panel; it does not fail the venue"
    taker = next(r for r in rows if r.data["endpoint"] == "taker_flow")
    assert taker.data["matrix"] == {"BTC": False, "ILLIQUID": False}
    assert "RuntimeError" in str(taker.data["errors"])


@pytest.mark.parametrize("name", sorted(load_venues()))
def test_every_venue_declares_a_probe_url(name):
    assert load_venues()[name].raw.get("probe_url", "").startswith("https://")

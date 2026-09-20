"""The venue probe, exercised entirely offline through ``respx``.

``03`` §3 测试规范: every network-facing function has an offline test against a
trimmed fixture. Here that rule carries more than usual weight, because the
function under test is the only thing in ``M1-A3`` that *can* send a request
and it was never allowed to: this machine's egress is the production egress
(``03`` §8, M1-B), and ``04`` §12 says a probe on it spends the budget the
older collector is using. So the fixtures are hand-built from the documents,
tagged ``source: documented``, and the assertion that nothing real was called
is structural — ``respx`` fails the test on any request it was not told about.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from conftest import REPO_ROOT, VENUES_PATH, preflight_fixture
from hlens_core.preflight import HttpReachabilityProbe, probe_plan
from hlens_core.preflight.reachability import GEO_REFUSAL_STATUS, plan_cost


def test_the_plan_comes_from_venues_yaml_and_nowhere_else() -> None:
    """AGENTS §2.3: no host is hard-coded outside ``config/venues.yaml``."""
    families = probe_plan(VENUES_PATH)
    names = [family.name for family in families]
    assert names == ["binance:fapi", "binance:fapi_mirror", "hyperliquid:info"]

    # No module of this package contains a URL. The venue hostnames appear in
    # prose — quoting `04` §6 is how a reader checks the code against the
    # document — but nothing here can *reach* a host that is not in the config.
    package = REPO_ROOT / "packages/hlens-core/src/hlens_core/preflight"
    for module in sorted(package.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "https://" not in source, module.name
        assert "wss://" not in source, module.name

    for family in families:
        assert family.url.startswith("https://")
        assert family.bucket in {"binance:fapi_weight", "hyperliquid:info_weight"}


def test_the_plan_prints_what_one_pass_costs() -> None:
    """``04`` §12: 不能随手打 — so the price is on the screen before the flag."""
    cost = plan_cost(probe_plan(VENUES_PATH))
    # Two W=1 pings plus one W=20 `meta`: 22 weight for a whole pass, and
    # nothing at all charged to the `futures_data` request bucket.
    assert cost == {"binance:fapi_weight": 2, "hyperliquid:info_weight": 20}
    assert "binance:futures_data" not in cost


@pytest.fixture
def probe() -> HttpReachabilityProbe:
    return HttpReachabilityProbe(client=httpx.Client(timeout=httpx.Timeout(1.0)))


@respx.mock
def test_a_reachable_family_records_its_code_and_latency(
    probe: HttpReachabilityProbe,
) -> None:
    families = {family.name: family for family in probe_plan(VENUES_PATH)}
    status_code, payload = preflight_fixture("binance_ping_ok")
    route = respx.get(families["binance:fapi"].url).mock(
        return_value=httpx.Response(status_code, json=payload)
    )

    outcome = probe.probe(families["binance:fapi"])
    assert route.called
    assert outcome.ok
    assert outcome.status_code == 200
    assert outcome.latency_ms is not None
    assert outcome.error_class is None
    assert not outcome.geo_refused


@respx.mock
def test_a_451_is_a_geographic_refusal_not_an_outage(
    probe: HttpReachabilityProbe,
) -> None:
    """``04`` §6: 应用层按地理位置拒绝，不是网络层不可达.

    The two need different remedies — switch to the mirror host versus fix the
    link — so they are different answers all the way through.
    """
    families = {family.name: family for family in probe_plan(VENUES_PATH)}
    status_code, payload = preflight_fixture("binance_ping_geo_blocked")
    assert status_code == GEO_REFUSAL_STATUS
    respx.get(families["binance:fapi"].url).mock(
        return_value=httpx.Response(status_code, json=payload)
    )
    respx.get(families["binance:fapi_mirror"].url).mock(
        return_value=httpx.Response(*preflight_fixture("binance_ping_ok")[:1], json={})
    )

    blocked = probe.probe(families["binance:fapi"])
    assert blocked.geo_refused
    assert not blocked.ok
    assert blocked.error_class == "http_451"

    mirror = probe.probe(families["binance:fapi_mirror"])
    assert mirror.ok


@respx.mock
def test_hyperliquid_is_probed_with_a_meta_post(probe: HttpReachabilityProbe) -> None:
    families = {family.name: family for family in probe_plan(VENUES_PATH)}
    family = families["hyperliquid:info"]
    status_code, payload = preflight_fixture("hyperliquid_meta_ok")
    route = respx.post(family.url).mock(
        return_value=httpx.Response(status_code, json=payload)
    )

    outcome = probe.probe(family)
    assert outcome.ok
    request = route.calls[0].request
    assert request.method == "POST"
    # `/info` selects the endpoint with a `type` field (04 §3); `meta` is the
    # cheapest one, and the probe reads the status code, never the body.
    assert b'"type"' in request.content
    assert b'"meta"' in request.content


@respx.mock
def test_a_transport_failure_is_recorded_not_raised(
    probe: HttpReachabilityProbe,
) -> None:
    """A preflight that crashes on an unreachable venue reports nothing at all
    about the other seven checks — which is the state it exists to prevent."""
    families = {family.name: family for family in probe_plan(VENUES_PATH)}
    respx.get(families["binance:fapi"].url).mock(
        side_effect=httpx.ConnectTimeout("no route")
    )
    outcome = probe.probe(families["binance:fapi"])
    assert outcome.status_code is None
    assert outcome.error_class == "ConnectTimeout"
    assert not outcome.ok

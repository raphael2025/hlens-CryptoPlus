"""The two configuration files, and the ways they are not allowed to lie.

``config/venues.yaml`` and ``config/egress-consumers.yaml`` are the only place
a rate-limit constant exists (AGENTS §2). The tests here are about the loader's
refusals: §6.1 says preflight "`reserved` 段缺失、或与运行中的旧采集器对不上，
即拒绝启动", and the failure mode it exists to prevent is stated in the document
itself — 「我们以为有 960 权重其实没有」.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conftest import HL_RESERVATION_MARKER, reclaimed_consumers_yaml, set_hl_reservation
from hlens_core.ratelimit import ConfigError, LedgerConfig, SourceTag, VenuesConfig


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _consumers_text(consumers_path: Path) -> str:
    return consumers_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Source tags
# --------------------------------------------------------------------------- #
def test_every_constant_in_venues_yaml_carries_a_source_tag(venues_path: Path) -> None:
    """AGENTS §2: each constant carries `source: official|measured|unverified`
    (plus `derived`; see the PR's "Doc corrections")."""
    document = yaml.safe_load(venues_path.read_text(encoding="utf-8"))
    untagged: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            if "value" in node:
                if node.get("source") not in set(SourceTag):
                    untagged.append(path)
                return
            for name, child in node.items():
                walk(child, f"{path}.{name}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(document["venues"], "venues")
    assert not untagged, f"constants without a usable source tag: {untagged}"


def test_every_reservation_carries_a_source_tag(consumers_path: Path) -> None:
    document = yaml.safe_load(consumers_path.read_text(encoding="utf-8"))
    for consumer in document["consumers"]:
        for venue, entry in consumer["venues"].items():
            for name, body in entry.items():
                if name == "ws_seats":
                    for seat, seat_body in body.items():
                        assert seat_body.get("source") in set(SourceTag), (venue, seat)
                    continue
                assert body.get("source") in set(SourceTag), (consumer["name"], venue, name)


def _without_block(text: str, key: str) -> str:
    """``text`` with every ``<key>:`` block removed — the key line and
    everything indented under it."""
    kept: list[str] = []
    depth = 0
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if skipping:
            if stripped and indent <= depth:
                skipping = False
            else:
                continue
        if stripped == f"{key}:":
            skipping = True
            depth = indent
            continue
        kept.append(line)
    return "\n".join(kept)


def test_venues_yaml_holds_hosts_only_under_endpoints_and_no_coin_list(
    venues_path: Path,
) -> None:
    """Scope, stated as a test — **narrowed by M1-A5, not dropped.**

    M1-A2 wrote this assertion as "no host anywhere in this file", with a
    docstring that already anticipated "the venue configuration this file does
    not yet contain". ``03`` §6.1 is where that configuration was always going
    to land — "`config/venues.yaml` 里三个 base URL 分开配，标 `官方`" — and
    AGENTS §2.3 forbids a hard-coded host anywhere else, so M1-A5 put the base
    URLs under each venue's ``endpoints:`` key.

    What the assertion protects is unchanged, and is now three things:

    1. a host may appear **only** inside an ``endpoints:`` block;
    2. endpoint **paths** still do not live here at all — a file that knew both
       where a venue is and what to ask it would be a file that knows how to
       call one, which is exactly what the ledger must not be;
    3. no IP address, and no coin list or symbol map, anywhere.
    """
    text = venues_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    outside_endpoints = _without_block(body, "endpoints")

    for forbidden in ("http://", "https://", "wss://", "ws://", "@", ".com", ".xyz"):
        assert forbidden not in outside_endpoints, (
            f"{forbidden!r} belongs under a venue's `endpoints:` key, nowhere else"
        )
    for path in ("/fapi/", "/futures/data/", "/api/v3/"):
        assert path not in body, (
            f"{path!r} is an endpoint path; paths stay with the adapter "
            "(packages/hlens-core/src/hlens_core/adapters/<venue>/endpoints.py). "
            "Hyperliquid's `/info` is named in a `doc:` citation, which is a "
            "quotation of 04 §3 rather than something the loader could call"
        )
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", body), "no IP addresses in the repo"
    assert "BTC" not in body and "symbol" not in body.lower()


def test_every_endpoint_constant_is_a_tagged_official_url(venues_path: Path) -> None:
    """``03`` §6.1: the base URLs are configured "标 `官方`".

    A ``measured`` or ``unverified`` host would mean somebody worked out where
    the exchange is by connecting to it, which is not how this repository finds
    anything out (AGENTS §2.2, ``04`` §11's on-machine rule).
    """
    document = yaml.safe_load(venues_path.read_text(encoding="utf-8"))
    seen = 0
    for venue, body in document["venues"].items():
        for name, constant in (body.get("endpoints") or {}).items():
            seen += 1
            where = f"venues.{venue}.endpoints.{name}"
            assert constant["source"] == "official", f"{where} is not tagged 官方"
            assert constant["value"].startswith(("https://", "wss://")), where
            assert not constant["value"].endswith("/"), f"{where} has a trailing slash"
            assert constant["doc"], f"{where} has no citation"
    assert seen >= 4, "the Binance base URLs (04 §2's three WS groups + REST) are missing"


# --------------------------------------------------------------------------- #
# A missing file, a missing venue, a missing bucket
# --------------------------------------------------------------------------- #
def test_a_missing_consumers_file_refuses_to_load(
    venues_path: Path, tmp_path: Path
) -> None:
    """§6.1: 「`reserved` 段缺失 … 即拒绝启动」. Never fall back to "nobody else"."""
    with pytest.raises(ConfigError, match="missing"):
        LedgerConfig.load(venues_path, tmp_path / "nope.yaml")


def test_a_consumer_that_omits_a_venue_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    """§6.1: 「旧采集器不碰 Binance」必须被断言，不能靠记忆.

    Deleting the Binance block is exactly the "remembered" version, and it is
    the change that would silently turn an assertion into a default.
    """
    text = _consumers_text(consumers_path)
    start = text.index("      binance:\n")
    end = text.index("      hyperliquid:\n", start)
    broken = text[:start] + text[end:]
    with pytest.raises(ConfigError, match="omission is not an assertion"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", broken))


def test_a_venue_entry_that_omits_a_bucket_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    text = _consumers_text(consumers_path)
    start = text.index("        futures_data:\n")
    end = text.index("      hyperliquid:\n", start)
    broken = text[:start] + text[end:]
    with pytest.raises(ConfigError, match="futures_data"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", broken))


def test_a_zero_reservation_without_its_assertion_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    """A bare 0 is the memory §6.1 forbids; it has to carry the claim."""
    text = _consumers_text(consumers_path)
    broken = re.sub(r"\n *assertion: >-\n(?: +.*\n)+", "\n", text, count=1)
    with pytest.raises(ConfigError, match="assertion"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", broken))


def test_an_unknown_consumer_bucket_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    text = _consumers_text(consumers_path).replace(
        "        futures_data:", "        futuresdata:"
    )
    with pytest.raises(ConfigError, match="unknown bucket"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", text))


# --------------------------------------------------------------------------- #
# Arithmetic the loader refuses to paper over
# --------------------------------------------------------------------------- #
def test_a_ceiling_that_disagrees_with_its_own_arithmetic_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    """960 is written down *and* recomputed; the loader will not pick a winner."""
    text = venues_path.read_text(encoding="utf-8").replace(
        "          value: 960\n          source: derived",
        "          value: 950\n          source: derived",
        1,
    )
    with pytest.raises(ConfigError, match="egress_ceiling_per_min says 950"):
        LedgerConfig.load(_write(tmp_path, "v.yaml", text), consumers_path)


def test_a_reserve_below_the_resident_steady_load_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    """§6.1 決定 A7: reserve 必须 ≥ resident 稳态用量.

    This is the exact defect the A5 review found (reserve 50 < steady 54), and
    the reason the opportunistic formula had to be rewritten. The loader now
    catches it instead of a person noticing a negative quota months later.
    """
    text = venues_path.read_text(encoding="utf-8").replace(
        "          value: 60\n          source: derived\n"
        '          doc: "03 §6.1 表：60 = 54 + 6',
        "          value: 50\n          source: derived\n"
        '          doc: "03 §6.1 表：60 = 54 + 6',
        1,
    )
    with pytest.raises(ConfigError, match="reserve is a FLOOR"):
        LedgerConfig.load(_write(tmp_path, "v.yaml", text), consumers_path)


def test_an_unknown_source_tag_refuses_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    text = venues_path.read_text(encoding="utf-8").replace(
        "source: official", "source: probably", 1
    )
    with pytest.raises(ConfigError, match="not one of"):
        LedgerConfig.load(_write(tmp_path, "v.yaml", text), consumers_path)


def test_reservations_larger_than_the_ceiling_refuse_to_load(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    # 1200 is not a synthetic number. It is the per-egress-IP budget the legacy
    # Hyperliquid collector gives ITSELF (M1-B, 2026-09-20), and it does not
    # know this project exists. Our whole egress ceiling is 1080, so that one
    # consumer's own configured cap is already over the line. This test is the
    # loader refusing the configuration we may actually be handed one day.
    text = set_hl_reservation(
        _consumers_text(consumers_path), "reserved_per_min: 1200\nsource: measured"
    )
    with pytest.raises(ConfigError, match="over the line"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", text))


# --------------------------------------------------------------------------- #
# Unknown is not zero
# --------------------------------------------------------------------------- #
def test_websocket_seats_are_now_counted_rather_than_unknown(
    config: LedgerConfig,
) -> None:
    """§6.1 row 4: 旧采集器已占的连接与 user 席位数 **未验证** —— 上机第一件事就是数.

    M1-B did the counting. The legacy Hyperliquid collector is pure REST
    against ``/info`` — no WebSocket client exists anywhere in its two runtime
    packages — so it holds no seats at all, and these stopped being `null`.
    All 8 connections / 800 subscriptions / 8 user seats are ours.
    """
    assert not config.consumers.unknown_seats("hyperliquid")
    assert not config.consumers.unknown_seats("binance")
    seats = [
        seat
        for seat in config.consumers.seats
        if seat.venue == "hyperliquid" and seat.consumer == "hub_legacy"
    ]
    assert {seat.seat for seat in seats} == {"connections", "distinct_users"}
    assert {seat.reserved for seat in seats} == {0}
    assert {seat.source.value for seat in seats} == {"measured"}


def test_an_unknown_seat_is_still_refused_as_unsubtractable(
    venues_path: Path, consumers_path: Path, tmp_path: Path
) -> None:
    """`null` is not `0`, and the distinction still has to survive.

    Rounding an *unknown* seat count to zero is the one answer that is
    certainly wrong — seats are indivisible and zero-sum (04 §4) — so the two
    must not be spelled the same way. Now that the real file says 0 because
    somebody counted, this is where that rule keeps being exercised.
    """
    text = _consumers_text(consumers_path).replace(
        "            reserved: 0\n            source: measured",
        "            reserved: null\n            source: unverified",
        1,
    )
    reloaded = LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", text))
    unknown = reloaded.consumers.unknown_seats("hyperliquid")
    assert [seat.seat for seat in unknown] == ["connections"]
    assert all(seat.reserved is None for seat in unknown)


def test_the_reclamation_edit_is_a_one_number_edit(
    venues_path: Path, tmp_path: Path, consumers_path: Path
) -> None:
    """§6.1 retirement: three steps, "不改任何代码"."""
    before = _consumers_text(consumers_path)
    after = reclaimed_consumers_yaml(before)
    assert before.count(HL_RESERVATION_MARKER) == 1
    assert after.count(HL_RESERVATION_MARKER) == 1
    assert "reserved_per_min: 0" in after.split(HL_RESERVATION_MARKER)[1]
    reclaimed = LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", after))
    assert reclaimed.bucket("hyperliquid:info_weight").our_ceiling_per_min == 1080


def test_venues_config_loads_on_its_own(venues_path: Path) -> None:
    venues = VenuesConfig.load(venues_path)
    assert {str(key) for key in venues.bucket_keys()} == {
        "binance:fapi_weight",
        "binance:futures_data",
        "binance:funding_rate",
        "hyperliquid:info_weight",
    }

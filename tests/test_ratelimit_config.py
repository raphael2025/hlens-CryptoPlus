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

from conftest import reclaimed_consumers_yaml
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


def test_venues_yaml_holds_no_endpoint_no_host_and_no_coin_list(
    venues_path: Path,
) -> None:
    """Scope, stated as a test.

    Endpoint URLs, base hosts and the symbol map belong to the adapters (build
    steps ⑤/⑥), not to the ledger, and AGENTS §3 forbids a real hostname or
    address anywhere in this repository outside the venue configuration this
    file does not yet contain.
    """
    text = venues_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("http://", "https://", "wss://", "ws://", "@", ".com", ".xyz"):
        assert forbidden not in body, f"{forbidden!r} does not belong in venues.yaml yet"
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", body), "no IP addresses in the repo"
    assert "BTC" not in body and "symbol" not in body.lower()


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
    text = _consumers_text(consumers_path).replace(
        "reserved_per_min: 960", "reserved_per_min: 1200"
    )
    with pytest.raises(ConfigError, match="over the line"):
        LedgerConfig.load(venues_path, _write(tmp_path, "c.yaml", text))


# --------------------------------------------------------------------------- #
# Unknown is not zero
# --------------------------------------------------------------------------- #
def test_unknown_websocket_seats_are_carried_as_unknown(config: LedgerConfig) -> None:
    """§6.1 row 4: 旧采集器已占的连接与 user 席位数 **未验证** —— 上机第一件事就是数.

    Rounding an unknown seat count to zero is the one answer that is certainly
    wrong: seats are indivisible and zero-sum (04 §4).
    """
    unknown = config.consumers.unknown_seats("hyperliquid")
    assert {seat.seat for seat in unknown} == {"connections", "distinct_users"}
    assert all(seat.reserved is None for seat in unknown)
    assert not config.consumers.unknown_seats("binance")


def test_the_reclamation_edit_is_a_one_number_edit(
    venues_path: Path, tmp_path: Path, consumers_path: Path
) -> None:
    """§6.1 retirement: three steps, "不改任何代码"."""
    before = _consumers_text(consumers_path)
    after = reclaimed_consumers_yaml(before)
    assert before.count("reserved_per_min: 960") == 1
    assert "reserved_per_min: 960" not in after
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

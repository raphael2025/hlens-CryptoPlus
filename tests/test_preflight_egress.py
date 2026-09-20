"""The egress hash and the three assertions of ``03`` §9 step ④.

The one test here that is about the repository rather than about the code is
:func:`test_the_address_never_appears_in_the_output`: ``03`` §8 says the report
prints 一致 / 不一致 and the first six hex digits, **永不打印 IP**, and this
repository is public.
"""

from __future__ import annotations

from decimal import Decimal

from hlens_core.preflight import EgressIdentity, HostFacts, Status, check_egress, egress_hash
from hlens_core.preflight.egress import HASH_LENGTH, PRINTED_PREFIX
from hlens_core.preflight.rows import EGRESS_CHANGE, IngestGapRow

NOW = 1_789_000_000_000
ADDRESS = "198.51.100.23"  # TEST-NET-2, RFC 5737. Never a real egress.
SALT = "unit-test-salt"


def _facts(**overrides: object) -> HostFacts:
    base = {
        "egress_ip": ADDRESS,
        "default_route_interface": "eth0",
        "tailscale_exit_node": None,
        "tailscale_present": True,
        "chrony_synchronized": True,
        "clock_offset_s": Decimal("0.01"),
    }
    base.update(overrides)
    return HostFacts(**base)  # type: ignore[arg-type]


def test_the_hash_is_salted_and_truncated() -> None:
    digest = egress_hash(SALT, ADDRESS)
    assert len(digest) == HASH_LENGTH
    # A different salt over the same address is a different digest — that is
    # what stops a 2^32 enumeration of IPv4 from reversing the published value.
    assert egress_hash("another-salt", ADDRESS) != digest
    assert egress_hash(SALT, "198.51.100.24") != digest


def test_all_three_assertions_pass_on_a_healthy_host() -> None:
    identity = EgressIdentity(salt=SALT, expected_hash=egress_hash(SALT, ADDRESS))
    checks, rows = check_egress(_facts(), identity, now_ms=NOW)
    assert [check.key for check in checks] == [
        "egress_default_route",
        "egress_exit_node",
        "egress_hash",
    ]
    assert {check.status for check in checks} == {Status.GREEN}
    assert rows == ()


def test_a_default_route_through_tailscale_fails() -> None:
    identity = EgressIdentity(salt=SALT, expected_hash=egress_hash(SALT, ADDRESS))
    checks, _ = check_egress(
        _facts(default_route_interface="tailscale0"), identity, now_ms=NOW
    )
    route = next(check for check in checks if check.key == "egress_default_route")
    assert route.status is Status.RED


def test_an_exit_node_fails_and_an_absent_tailscale_is_unknown() -> None:
    identity = EgressIdentity(salt=SALT, expected_hash=egress_hash(SALT, ADDRESS))
    checks, _ = check_egress(
        _facts(tailscale_exit_node="nodeid-abcdef123456"), identity, now_ms=NOW
    )
    exit_node = next(check for check in checks if check.key == "egress_exit_node")
    assert exit_node.status is Status.RED

    checks, _ = check_egress(
        _facts(tailscale_present=None, tailscale_exit_node=None), identity, now_ms=NOW
    )
    exit_node = next(check for check in checks if check.key == "egress_exit_node")
    assert exit_node.status is Status.UNKNOWN


def test_a_mismatch_is_red_and_writes_ingest_gap_egress_change() -> None:
    """§5's ten-value enum already has ``egress_change``; nothing new is coined."""
    identity = EgressIdentity(salt=SALT, expected_hash=egress_hash(SALT, "203.0.113.1"))
    checks, rows = check_egress(_facts(), identity, now_ms=NOW)
    hash_check = next(check for check in checks if check.key == "egress_hash")
    assert hash_check.status is Status.RED

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, IngestGapRow)
    assert row.cause == EGRESS_CHANGE
    assert row.from_ts == NOW
    assert row.source == "preflight"


def test_a_missing_salt_is_unknown_not_green() -> None:
    checks, rows = check_egress(
        _facts(), EgressIdentity(salt=None, expected_hash=None), now_ms=NOW
    )
    hash_check = next(check for check in checks if check.key == "egress_hash")
    assert hash_check.status is Status.UNKNOWN
    assert rows == ()
    assert "EGRESS_SALT" in hash_check.headline


def test_a_missing_address_is_unknown_not_green() -> None:
    identity = EgressIdentity(salt=SALT, expected_hash=egress_hash(SALT, ADDRESS))
    checks, _ = check_egress(_facts(egress_ip=None), identity, now_ms=NOW)
    hash_check = next(check for check in checks if check.key == "egress_hash")
    assert hash_check.status is Status.UNKNOWN


def test_the_address_never_appears_in_the_output() -> None:
    """``03`` §8: 永不打印 IP. Checked on both the matching and the failing path."""
    for expected in (egress_hash(SALT, ADDRESS), egress_hash(SALT, "203.0.113.1")):
        checks, rows = check_egress(
            _facts(), EgressIdentity(salt=SALT, expected_hash=expected), now_ms=NOW
        )
        printed = "\n".join(line for check in checks for line in check.render())
        printed += "\n" + "\n".join(str(row) for row in rows)
        assert ADDRESS not in printed
        assert SALT not in printed
        # The full digest is not printed either — only its first six digits.
        assert egress_hash(SALT, ADDRESS) not in printed
        assert egress_hash(SALT, ADDRESS)[:PRINTED_PREFIX] in printed

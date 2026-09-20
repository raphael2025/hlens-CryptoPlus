"""Seam ⑤ — wallet data has its own protocol. Interface only; M5 implements it.

``03`` §2 seam ⑤, verbatim: "钱包数据有自己的协议，与行情适配器分开。M1 只定义
接口、无实现；**行情适配器里不出现任何钱包方法或钱包能力**". §4 repeats it as a
module of its own: "``wallet`` 协议（只定接口不实现） … M1 定义 / M5 实现". The
cost of getting it wrong is written down too: "M5 要动 M1 的行情代码".

So this module is separate all the way down, and the separation is mechanical,
not a matter of discipline:

* it shares **no base class** with :mod:`hlens_core.adapters` — a wallet source
  is not a kind of market-data adapter and cannot be passed where one is
  expected;
* it shares **no enum**. :class:`WalletCapability` is a second, independent
  enumeration, which is what ``03`` §2 means by "钱包类能力不在这个枚举里"; the
  three answers are asked again here rather than imported, so that a change to
  the market-data vocabulary cannot silently redefine a wallet declaration;
* it shares **no import**, in either direction. ``tests/test_wallet_seam.py``
  asserts that by AST, and ``pyproject.toml``'s ``banned-api`` entry for
  ``hlens_core.wallet`` says the same thing to ruff.

There is no implementation here, deliberately: every method body is ``...`` and
every declaration type is data. The endpoints these methods will use are in
``04`` §10 and the constraints that will shape them are already known — WebSocket
``userFills`` is capped at **10 distinct users per egress IP** and those seats
are shared with the legacy collector, ``userFillsByTime`` reaches only the last
10000 fills, and there is no public leaderboard endpoint at all — but writing
any of that as code before M5 would be writing it without the measurements
``04`` §11 says have to come first.

``01`` §4.10 governs whatever is built on top of this: no identity linkage, no
cross-chain profiling, de-emotionalized wording, opt-out honoured within 7 days.
The protocol therefore speaks of addresses and never of people.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Protocol, TypeVar

from pydantic import Field

from hlens_core.contracts import Venue

__all__ = [
    "AnyWalletAdmission",
    "WalletAddress",
    "WalletCallCost",
    "WalletCapability",
    "WalletCapabilityDeclaration",
    "WalletCompleteness",
    "WalletDataSource",
    "WalletLanePriority",
    "WalletMode",
    "WalletRecord",
    "WalletSpendAuthority",
    "WalletSupport",
]

WalletPriorityT_contra = TypeVar("WalletPriorityT_contra", contravariant=True)
WalletGrantT = TypeVar("WalletGrantT")

#: A Hyperliquid account address, lower-case hex. A value, not an identity
#: (``01`` §4.10). No real address appears anywhere in this repository.
WalletAddress = Annotated[str, Field(pattern=r"^0x[0-9a-f]{40}$")]


class WalletCapability(StrEnum):
    """What a wallet source can be asked about. A second enumeration, on
    purpose: none of these values exists in the market-data one, and none of
    the market-data values exists here (seam ⑤)."""

    ACCOUNT_STATE = "account_state"
    """Current positions and margin for one address (``clearinghouseState``)."""

    FILLS = "fills"
    """Recent fills for one address (``userFills``). ``04`` §8: a forced fill is
    recognised by the **presence of the ``liquidation`` key**, never by its
    value — the key exists with ``null`` on ordinary fills."""

    FILLS_HISTORY = "fills_history"
    """Fills further back than the live window (``userFillsByTime``). Bounded
    by the venue at the last 10000 fills, which is why ``04`` §10 says the
    2023–2026 history must be imported rather than fetched."""

    FUNDING_LEDGER = "funding_ledger"
    """Funding settlements and transfers for one address."""

    FILLS_STREAM = "fills_stream"
    """Pushed fills (WebSocket ``userFills``). Limited to 10 distinct users per
    egress IP, and those seats are zero-sum with the legacy collector
    (``04`` §4, §10)."""

    LEADERBOARD = "leaderboard"
    """The candidate pool by account value. ``04`` §10/§11: the venue publishes
    **no** public leaderboard endpoint, so a source that declares this
    ``supported`` owes a reproducible answer to where it came from."""


class WalletSupport(StrEnum):
    """Answer 1 of 3, asked again here rather than imported (seam ⑤)."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class WalletMode(StrEnum):
    """Answer 2 of 3: how the data reaches us.

    ``imported`` has no counterpart on the market-data side and is the reason
    these enums are separate: the M5 history is a dataset copied from another
    machine, with its own lag (``04`` §8: median about 6 hours), not something
    this process observed.
    """

    PUSH_STREAM = "push_stream"
    POLL_SNAPSHOT = "poll_snapshot"
    POLL_HISTORY = "poll_history"
    IMPORTED = "imported"
    NOT_APPLICABLE = "not_applicable"


class WalletCompleteness(StrEnum):
    """Answer 3 of 3: how much of it we get.

    ``04`` §8 measured what the imported liquidation dataset actually is —
    51–93 % by coin in the live window, 1.6–8 % historically — so
    ``lower_bound`` and ``partial_history`` are the honest answers there and a
    total is never one of them.
    """

    FULL = "full"
    LOWER_BOUND = "lower_bound"
    PARTIAL_HISTORY = "partial_history"
    NOT_APPLICABLE = "not_applicable"


class WalletLanePriority(StrEnum):
    """Which tier a wallet call runs in. Same three tiers as the rest of the
    system (``03`` §6.1) and, like everything else here, its own enum."""

    FAST_LANE = "fast_lane"
    RESIDENT = "resident"
    OPPORTUNISTIC = "opportunistic"


@dataclass(frozen=True, slots=True)
class WalletCapabilityDeclaration:
    """Three separate answers about one wallet capability (``01`` §4.7).

    A declaration and nothing more — M1 defines, M5 implements — so the
    consistency rules are not enforced here yet. What is fixed now is the
    shape: three fields, never merged, plus the venue lag that ``01`` §4.5
    requires the display to honour.
    """

    capability: WalletCapability
    supported: WalletSupport
    mode: WalletMode
    completeness: WalletCompleteness
    source_lag_s: int | None = None
    """Typical lag of this source in seconds, where it is known. ``01`` §4.5:
    imported event data is shown against its own cut-off rather than a
    minute-level freshness budget, and that number is the reason why."""

    note: str | None = None


@dataclass(frozen=True, slots=True)
class WalletCallCost:
    """What one wallet call costs. The same idea as the market-data side, and
    again a separate type: ``04`` §10 prices these endpoints differently
    (``clearinghouseState`` is weight 2, ``userFills`` is 20 plus 1 per 20
    rows) and they charge the same Hyperliquid ``/info`` bucket the market-data
    lanes do — which is exactly why M5 cannot be planned without the shared
    budget in front of it."""

    bucket: str
    weight: int
    priority: WalletLanePriority
    lane: str | None = None
    endpoint: str | None = None


class WalletRecord(Protocol):
    """Seam ①'s five mandatory attributes, restated for wallet records.

    M5 adds the wallet contracts; until then this states the floor they must
    meet, and says that wallet data crosses this boundary normalized — a raw
    exchange fill object never does.
    """

    venue: Venue
    symbol: str
    ts: int
    ingest_ts: int
    source: str


class WalletSpendAuthority(Protocol[WalletPriorityT_contra, WalletGrantT]):
    """The rate-limit ledger, seen structurally — the wallet module's own copy.

    Not imported from :mod:`hlens_core.adapters`: seam ⑤ forbids the
    cross-reference, and structural typing means the duplication costs nothing
    — the same ``RateLimitLedger`` satisfies both, and neither module knows the
    other exists.
    """

    def acquire(
        self,
        key: str,
        *,
        cost: int,
        priority: WalletPriorityT_contra,
        lane: str | None = None,
    ) -> WalletGrantT: ...

    def settle(
        self,
        grant: WalletGrantT,
        *,
        actual_cost: int | None = None,
        status: int = 200,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None: ...

    def release(self, grant: WalletGrantT) -> None: ...

    def observe_response(
        self,
        key: str,
        *,
        status: int,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None: ...


#: What an M5 wallet method is handed: a ledger seen through the protocol
#: above, with the priority and grant types belonging to whoever wired it up.
#: The translation from :class:`WalletLanePriority` into the ledger's own
#: priority enum is the caller's one line, exactly as on the market-data side —
#: and it is written there, separately, because the two modules share nothing.
AnyWalletAdmission = WalletSpendAuthority[Any, Any]


class WalletDataSource(Protocol):
    """One venue's wallet data. Separate from every market-data type here.

    M5 implements this for Hyperliquid. Two constraints from ``04`` §10 are
    already in the signatures, because they change the shape rather than the
    implementation: the pushed channel takes a **sequence** of addresses and is
    hard-capped at 10 distinct users per egress IP, and the historical fills
    method is explicitly a window rather than "all of it".
    """

    @property
    def venue(self) -> Venue: ...

    @property
    def capabilities(self) -> tuple[WalletCapabilityDeclaration, ...]:
        """Three answers per wallet capability. Wallet capabilities are never
        declared on a market-data adapter, and market-data capabilities are
        never declared here (seam ⑤)."""
        ...

    def cost_of(
        self, capability: WalletCapability, *, addresses: int = 1, rows: int | None = None
    ) -> WalletCallCost: ...

    async def fetch_account_state(
        self, address: str, *, admission: AnyWalletAdmission
    ) -> WalletRecord:
        """Current positions and margin. A snapshot with no history behind it."""
        ...

    async def fetch_fills(
        self,
        address: str,
        *,
        admission: AnyWalletAdmission,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> tuple[WalletRecord, ...]:
        """Fills in a window. Bounded by the venue, not by us."""
        ...

    async def fetch_funding_ledger(
        self,
        address: str,
        *,
        admission: AnyWalletAdmission,
        start_ms: int,
        end_ms: int | None = None,
    ) -> tuple[WalletRecord, ...]: ...

    def stream_fills(
        self, addresses: Sequence[str], *, admission: AnyWalletAdmission
    ) -> AsyncIterator[WalletRecord]:
        """Pushed fills for at most 10 distinct addresses across the whole
        egress IP — a hard venue limit shared with the legacy collector, and
        with the on-demand wallet page (``04`` §10)."""
        ...

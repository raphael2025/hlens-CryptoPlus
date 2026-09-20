"""Where Binance lives, and what we ask it for.

Two halves, split on purpose:

* **hosts come from ``config/venues.yaml``.** AGENTS §2.3 ("no hard-coded hosts
  outside ``config/venues.yaml``") and ``03`` §6.1 ("``config/venues.yaml`` 里
  三个 base URL 分开配，标 `官方`") both require it, and the 2026-04-23
  WebSocket split is exactly why: ``!markPrice@arr@1s`` and ``!forceOrder@arr``
  moved to the ``/market`` group and the legacy ``…/stream`` endpoint stopped
  pushing permanently (``04`` §2, §13 第 1 行). A host spelled into code is a
  host nobody re-checks when the venue splits it again.
* **paths stay here.** ``config/venues.yaml``'s own header says endpoint URLs do
  not belong in it, because the file is read by the rate-limit ledger and "it
  does accounting and admission only, and never sends a request". Both rules are
  satisfiable at once: the *host* is configuration, the *path* is what this
  adapter knows how to call. See the PR's "Doc corrections" for the one line of
  that header that is now out of date.

Nothing in this module sends anything. It answers "which URL", and it refuses
to answer with a host it did not read from the config file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

__all__ = [
    "BINANCE_FUTURES_DATA_PATHS",
    "DEFAULT_KLINE_LIMIT",
    "MAX_FUNDING_RATE_LIMIT",
    "MAX_KLINE_LIMIT",
    "REST_EXCHANGE_INFO",
    "REST_FUNDING_INFO",
    "REST_FUNDING_RATE",
    "REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO",
    "REST_KLINES",
    "REST_OPEN_INTEREST",
    "REST_PREMIUM_INDEX",
    "REST_TAKER_LONG_SHORT_RATIO",
    "REST_TICKER_24H",
    "REST_TOP_LONG_SHORT_POSITION_RATIO",
    "WS_FORCE_ORDER_ALL",
    "WS_GROUP_MARKET",
    "WS_GROUP_PUBLIC",
    "WS_MARK_PRICE_ALL",
    "BinanceEndpoints",
    "EndpointError",
    "ws_mark_price_stream",
]


class EndpointError(ValueError):
    """``config/venues.yaml`` does not describe Binance's hosts usably.

    Raised at load time rather than at call time: a missing or mistyped base URL
    must stop the process before it opens a socket, not halfway through a lane.
    """


# --------------------------------------------------------------------------- #
# REST paths (04 §2). Paths, never URLs — CallCost refuses an endpoint with a
# scheme in it, and so does this module's own contract with the reader.
# --------------------------------------------------------------------------- #
REST_EXCHANGE_INFO: Final = "/fapi/v1/exchangeInfo"
REST_PREMIUM_INDEX: Final = "/fapi/v1/premiumIndex"
REST_OPEN_INTEREST: Final = "/fapi/v1/openInterest"
REST_TICKER_24H: Final = "/fapi/v1/ticker/24hr"
REST_KLINES: Final = "/fapi/v1/klines"
REST_FUNDING_RATE: Final = "/fapi/v1/fundingRate"
REST_FUNDING_INFO: Final = "/fapi/v1/fundingInfo"

#: The three ``/futures/data/*`` ratio endpoints — **three, not four**.
#: ``03`` §5 records three ``ls_ratio`` kinds and ``03`` §6's lane table names
#: these three; ``topLongShortAccountRatio`` appears in ``04`` §1/§2 but no
#: confirmed feature consumes it, so it is not called. See "Doc corrections".
REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO: Final = "/futures/data/globalLongShortAccountRatio"
REST_TOP_LONG_SHORT_POSITION_RATIO: Final = "/futures/data/topLongShortPositionRatio"
REST_TAKER_LONG_SHORT_RATIO: Final = "/futures/data/takerlongshortRatio"

BINANCE_FUTURES_DATA_PATHS: Final[frozenset[str]] = frozenset(
    {
        REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
        REST_TOP_LONG_SHORT_POSITION_RATIO,
        REST_TAKER_LONG_SHORT_RATIO,
    }
)

#: ``04`` §2: ``limit≤1500`` on klines, ``limit≤1000`` on ``fundingRate``.
MAX_KLINE_LIMIT: Final = 1500
MAX_FUNDING_RATE_LIMIT: Final = 1000
#: Binance's own default when ``limit`` is omitted. Written down because the
#: weight ladder is charged on it: an omitted ``limit`` is not a free call.
DEFAULT_KLINE_LIMIT: Final = 500

# --------------------------------------------------------------------------- #
# WebSocket groups and stream names (04 §2's change notice, 2026-04-23)
# --------------------------------------------------------------------------- #
#: ``StreamPlan.group`` names — the adapter's own word for a connection group,
#: never a URL. ``@markPrice`` and ``@forceOrder`` are both on ``market``;
#: ``public`` exists so M4's ``@depth`` cannot quietly share the connection.
WS_GROUP_MARKET: Final = "market"
WS_GROUP_PUBLIC: Final = "public"

WS_MARK_PRICE_ALL: Final = "!markPrice@arr@1s"
WS_FORCE_ORDER_ALL: Final = "!forceOrder@arr"


def ws_mark_price_stream(venue_symbol: str) -> str:
    """The per-symbol mark-price stream name. Binance spells stream names lower
    case even though the contract name is upper case."""
    return f"{venue_symbol.lower()}@markPrice@1s"


_SOURCE_TAGS: Final[frozenset[str]] = frozenset({"official", "measured", "unverified"})

#: The keys this adapter reads out of ``venues.binance.endpoints``, and the URL
#: scheme each one must carry. ``ws_private`` is deliberately absent from the
#: attributes below even though the config names it: ``03`` §3 designs this
#: project without authentication, so there is no code path that could use it,
#: and a field nobody reads is an invitation.
_REQUIRED: Final[Mapping[str, str]] = {
    "rest": "https://",
    "rest_mirror": "https://",
    "ws_market": "wss://",
    "ws_public": "wss://",
}


@dataclass(frozen=True, slots=True)
class BinanceEndpoints:
    """Binance's four reachable base URLs, as ``config/venues.yaml`` spells them."""

    rest: str
    """``fapi`` host. ``04`` §6: returns 451 from a US egress."""

    rest_mirror: str
    """Same paths on the ``www`` host. ``04`` §6: the only way out if the
    production egress turns out to be in the US — which is an on-machine
    measurement (``04`` §11 第 13 项), not something this adapter may assume."""

    ws_market: str
    """The ``/market`` group. ``!markPrice@arr@1s`` and ``!forceOrder@arr``."""

    ws_public: str
    """The ``/public`` group. M4's ``@depth`` only; nothing in M1 connects to
    it. Carried so that the split is visible in one place rather than
    rediscovered in M4."""

    def rest_base(self, *, mirror: bool = False) -> str:
        """Which REST host to use. The choice belongs to preflight, which is the
        only thing that knows where the egress actually is (``04`` §6, §7)."""
        return self.rest_mirror if mirror else self.rest

    def rest_url(self, path: str, *, mirror: bool = False) -> str:
        if not path.startswith("/"):
            raise EndpointError(f"a REST path starts with '/'; got {path!r}")
        return f"{self.rest_base(mirror=mirror)}{path}"

    def ws_url(self, streams: tuple[str, ...]) -> str:
        """The combined-stream URL for the ``/market`` group.

        Only ``/market`` is buildable here. M1 and M2 subscribe to nothing on
        ``/public``, and ``04`` §2 is explicit that the two groups can never
        share a connection — so the one-connection-per-group rule is enforced by
        there being no way to ask for a mixed URL.
        """
        if not streams:
            raise EndpointError("a stream URL needs at least one stream")
        return f"{self.ws_market}/stream?streams={'/'.join(streams)}"

    @classmethod
    def load(cls, path: Path | str) -> BinanceEndpoints:
        """Read ``venues.binance.endpoints`` out of ``config/venues.yaml``.

        Every value must be tagged ``official`` (``04`` §2 and its change
        notice are the venue's own documentation) and must carry the scheme its
        group uses. A ``measured`` or ``unverified`` host would mean somebody
        guessed at where the exchange is, which is not a thing to find out by
        connecting.
        """
        source = str(path)
        document: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise EndpointError(f"{source}: expected a mapping at the top level")
        venues = document.get("venues")
        if not isinstance(venues, Mapping):
            raise EndpointError(f"{source}: no 'venues' mapping")
        binance = venues.get("binance")
        if not isinstance(binance, Mapping):
            raise EndpointError(f"{source}: no 'venues.binance' mapping")
        node = binance.get("endpoints")
        if not isinstance(node, Mapping):
            raise EndpointError(
                f"{source}: venues.binance.endpoints is missing; 03 §6.1 requires the "
                "base URLs to live in this file, one per WebSocket group"
            )

        values: dict[str, str] = {}
        for name, scheme in _REQUIRED.items():
            values[name] = _constant(
                node.get(name),
                where=f"{source}.venues.binance.endpoints.{name}",
                scheme=scheme,
            )
        return cls(**values)


def _constant(node: object, *, where: str, scheme: str) -> str:
    """One ``{value, source, doc}`` constant, as every other number in this file
    is written — the source tag is not decoration, it is AGENTS §2.2."""
    if node is None:
        raise EndpointError(f"{where}: missing")
    if not isinstance(node, Mapping):
        raise EndpointError(f"{where}: expected a mapping with 'value' and 'source'")
    value = node.get("value")
    if not isinstance(value, str) or not value:
        raise EndpointError(f"{where}.value: expected a non-empty string")
    tag = node.get("source")
    if not isinstance(tag, str) or tag not in _SOURCE_TAGS:
        raise EndpointError(f"{where}.source: expected one of {sorted(_SOURCE_TAGS)}, got {tag!r}")
    if tag != "official":
        raise EndpointError(
            f"{where}.source: a base URL is either documented by the venue or unknown; "
            f"{tag!r} means somebody guessed where the exchange is"
        )
    if not value.startswith(scheme):
        raise EndpointError(f"{where}.value: expected a {scheme}… URL, got {value!r}")
    if value.endswith("/"):
        raise EndpointError(f"{where}.value: no trailing slash — paths are joined verbatim")
    return value

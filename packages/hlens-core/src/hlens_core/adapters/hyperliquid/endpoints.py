"""Where Hyperliquid is, and what we ask it for.

Same split as the Binance adapter's ``endpoints`` module, for the same two
rules pulling in opposite directions:

* **hosts come from ``config/venues.yaml``.** AGENTS §2.3 ("no hard-coded hosts
  outside ``config/venues.yaml``") and ``03`` §6.1 both require it;
* **paths and request names stay here.** ``config/venues.yaml``'s own header
  says the ledger "does accounting and admission only, and never sends a
  request", so what to ask a venue is not its business.

One host, one socket, one path
------------------------------
Hyperliquid is simpler than Binance in the one way that matters to this file:
every documented market-data read is a **POST** to the single path ``/info``
with a ``type`` field selecting the endpoint (``04`` §3), and every WebSocket
channel shares one address. So there is no mirror host (``04`` §6 records
``api.hyperliquid.xyz`` answering 200 from both a US and a non-US egress,
unlike ``fapi.binance.com``'s 451), no 2026-04-23 style group split, and no
per-group connection rule to encode.

What replaces them is a different kind of care: because one path serves every
endpoint, the **request type is the only thing that says what a call was** —
there is no URL to read in a log line — which is why the type travels inside
every :class:`~hlens_core.adapters.admission.CallCost`'s ``endpoint`` string.

Nothing in this module sends anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

__all__ = [
    "INFO_PATH",
    "MAX_CANDLE_HISTORY_ROWS",
    "MAX_ROWS_PER_RANGED_RESPONSE",
    "TYPE_CANDLE_SNAPSHOT",
    "TYPE_FUNDING_HISTORY",
    "TYPE_META",
    "TYPE_META_AND_ASSET_CTXS",
    "TYPE_PREDICTED_FUNDINGS",
    "WS_CHANNEL_ALL_MIDS",
    "WS_GROUP_INFO",
    "EndpointError",
    "HyperliquidEndpoints",
    "subscribe_frame",
]


class EndpointError(ValueError):
    """``config/venues.yaml`` does not describe Hyperliquid's hosts usably.

    Raised at load time rather than at call time: a missing or mistyped base URL
    must stop the process before it opens a socket, not halfway through a lane.
    """


#: The one REST path (``04`` §3). **POST**, not GET — every other venue habit
#: in this repository is a GET with query parameters, and this one is not.
INFO_PATH: Final = "/info"

# --------------------------------------------------------------------------- #
# `type` values — the field that actually selects an endpoint (04 §3)
# --------------------------------------------------------------------------- #
TYPE_META: Final = "meta"
TYPE_META_AND_ASSET_CTXS: Final = "metaAndAssetCtxs"
TYPE_FUNDING_HISTORY: Final = "fundingHistory"
TYPE_PREDICTED_FUNDINGS: Final = "predictedFundings"
TYPE_CANDLE_SNAPSHOT: Final = "candleSnapshot"

#: ``04`` §3 / §5, and they are **two different limits** — ``04`` §13 第 3 行
#: exists precisely because an earlier reading merged them:
#:
#: * a response carrying a time range returns at most 500 elements. A
#:   *pagination* limit, on every ranged endpoint;
#: * ``candleSnapshot`` retains only the most recent ~5000 bars. A *history
#:   depth* limit — about 3.5 days at 1m — which is why Hyperliquid's klines
#:   capability is ``partial_history`` and why F8 shows "数据积累中" instead of
#:   extrapolating.
MAX_ROWS_PER_RANGED_RESPONSE: Final = 500
MAX_CANDLE_HISTORY_ROWS: Final = 5000

# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
#: :attr:`~hlens_core.adapters.base.StreamPlan.group` — the adapter's own name
#: for a connection group, never a URL. Hyperliquid has exactly one, which is
#: the name ``StreamPlan``'s own docstring already gives it.
WS_GROUP_INFO: Final = "info"

#: ``04`` §3's channel list, of which M1 uses exactly one. ``trades`` and
#: ``l2Book`` are M4 (F20/F21) and the ``user*`` channels are seam ⑤'s, so
#: neither appears anywhere in this package.
WS_CHANNEL_ALL_MIDS: Final = "allMids"


def subscribe_frame(channel: str) -> dict[str, Any]:
    """The subscription message Hyperliquid expects on an open socket.

    Binance names its streams in the URL; Hyperliquid connects first and then
    **sends** a frame (``04`` §3's WebSocket section). That is the whole reason
    this adapter's WebSocket transport needs a ``send`` and Binance's does not.
    """
    return {"method": "subscribe", "subscription": {"type": channel}}


_SOURCE_TAGS: Final[frozenset[str]] = frozenset({"official", "measured", "unverified"})

#: The keys read out of ``venues.hyperliquid.endpoints``, and the URL scheme
#: each one must carry.
_REQUIRED: Final[Mapping[str, str]] = {"rest": "https://", "ws": "wss://"}


@dataclass(frozen=True, slots=True)
class HyperliquidEndpoints:
    """Hyperliquid's two base URLs, as ``config/venues.yaml`` spells them."""

    rest: str
    """The ``/info`` host. ``04`` §6: 200 from both a US and a non-US egress, so
    there is no mirror to switch to and no switch to get wrong."""

    ws: str
    """The single WebSocket address; every channel shares it."""

    def info_url(self) -> str:
        return f"{self.rest}{INFO_PATH}"

    def ws_url(self) -> str:
        return self.ws

    @classmethod
    def load(cls, path: Path | str) -> HyperliquidEndpoints:
        """Read ``venues.hyperliquid.endpoints`` out of ``config/venues.yaml``.

        Both values must be tagged ``official``: a ``measured`` or
        ``unverified`` host would mean somebody guessed where the exchange is,
        which is not a thing to find out by connecting — least of all from an
        egress already shared with a running collector of this same venue.
        """
        source = str(path)
        document: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise EndpointError(f"{source}: expected a mapping at the top level")
        venues = document.get("venues")
        if not isinstance(venues, Mapping):
            raise EndpointError(f"{source}: no 'venues' mapping")
        hyperliquid = venues.get("hyperliquid")
        if not isinstance(hyperliquid, Mapping):
            raise EndpointError(f"{source}: no 'venues.hyperliquid' mapping")
        node = hyperliquid.get("endpoints")
        if not isinstance(node, Mapping):
            raise EndpointError(
                f"{source}: venues.hyperliquid.endpoints is missing; 03 §6.1 requires "
                "the base URLs to live in this file"
            )
        values = {
            name: _constant(
                node.get(name),
                where=f"{source}.venues.hyperliquid.endpoints.{name}",
                scheme=scheme,
            )
            for name, scheme in _REQUIRED.items()
        }
        return cls(**values)


def _constant(node: object, *, where: str, scheme: str) -> str:
    """One ``{value, source, doc}`` constant, written exactly as every other
    number in that file is — the source tag is not decoration, it is AGENTS
    §2.2."""
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

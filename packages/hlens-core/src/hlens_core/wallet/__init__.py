"""Seam ⑤ — the wallet protocol, defined in M1 and implemented in M5.

Interface only. Read :mod:`hlens_core.wallet.base` for why this module shares
no base class, no enumeration and no import with :mod:`hlens_core.adapters`,
and for the ``04`` §10 limits that already shape the signatures.
"""

from __future__ import annotations

from .base import (
    AnyWalletAdmission,
    WalletAddress,
    WalletCallCost,
    WalletCapability,
    WalletCapabilityDeclaration,
    WalletCompleteness,
    WalletDataSource,
    WalletLanePriority,
    WalletMode,
    WalletRecord,
    WalletSpendAuthority,
    WalletSupport,
)

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

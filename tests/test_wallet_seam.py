"""Seam ⑤ — the wallet protocol is separate, and separate mechanically.

``03`` §2 seam ⑤: "钱包数据有自己的协议，与行情适配器分开。M1 只定义接口、无实现；
行情适配器里不出现任何钱包方法或钱包能力". The cost of losing it: "M5 要动 M1 的
行情代码".

Three things are asserted here, none of which relies on anybody remembering:
neither module imports the other, they share no base class and no enumeration,
and ``wallet/base.py`` contains no implementation at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hlens_core.adapters import Capability, MarketDataAdapter, Mode, Support
from hlens_core.wallet import (
    WalletCapability,
    WalletDataSource,
    WalletMode,
    WalletSupport,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = REPO_ROOT / "packages" / "hlens-core" / "src" / "hlens_core"
WALLET_FILES = sorted((CORE / "wallet").rglob("*.py"))
ADAPTER_FILES = sorted((CORE / "adapters").rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_scan_is_not_vacuous() -> None:
    assert WALLET_FILES and ADAPTER_FILES


@pytest.mark.parametrize("path", WALLET_FILES, ids=lambda p: p.name)
def test_the_wallet_protocol_does_not_reach_into_market_data(path: Path) -> None:
    assert not any(name.startswith("hlens_core.adapters") for name in _imported_modules(path))


@pytest.mark.parametrize("path", ADAPTER_FILES, ids=lambda p: p.name)
def test_no_market_data_file_reaches_into_the_wallet_protocol(path: Path) -> None:
    assert not any(name.startswith("hlens_core.wallet") for name in _imported_modules(path))


def test_the_two_protocols_share_no_base_class() -> None:
    """Not a subclass either way, and nothing in common above them but the
    machinery every protocol has."""
    assert MarketDataAdapter not in WalletDataSource.__mro__
    assert WalletDataSource not in MarketDataAdapter.__mro__
    shared = set(WalletDataSource.__mro__) & set(MarketDataAdapter.__mro__)
    assert {cls.__name__ for cls in shared} <= {"object", "Protocol", "Generic"}


def test_the_capability_enumerations_are_disjoint() -> None:
    """"钱包类能力不在这个枚举里" — and the wallet enum is its own class, not an
    alias, so renaming one cannot silently redefine the other."""
    assert WalletCapability is not Capability
    assert {w.value for w in WalletCapability}.isdisjoint({c.value for c in Capability})
    assert WalletSupport is not Support
    assert WalletMode is not Mode


def test_the_wallet_mode_says_something_the_market_one_must_not() -> None:
    """``imported`` exists only on the wallet side: the M5 history is a dataset
    copied from another machine with a ~6 hour median lag (``04`` §8), which is
    not a mode any market-data adapter may claim."""
    assert "imported" in {m.value for m in WalletMode}
    assert "imported" not in {m.value for m in Mode}


def test_no_wallet_method_name_appears_on_the_market_data_protocol() -> None:
    wallet_methods = {name for name in dir(WalletDataSource) if not name.startswith("_")}
    market_methods = {name for name in dir(MarketDataAdapter) if not name.startswith("_")}
    # `venue`, `capabilities` and `cost_of` are the same *questions* asked of
    # two unrelated protocols; every actual wallet method is absent from the
    # market-data one, which is what seam ⑤ forbids.
    assert wallet_methods & market_methods == {"venue", "capabilities", "cost_of"}
    assert not {"fetch_account_state", "fetch_fills", "stream_fills"} & market_methods


@pytest.mark.parametrize("path", WALLET_FILES, ids=lambda p: p.name)
def test_the_wallet_module_has_no_implementation(path: Path) -> None:
    """"M1 只定义接口、无实现" — every function body is a docstring and an
    ellipsis, and a test says so rather than a reviewer noticing."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for statement in node.body:
            assert isinstance(statement, ast.Expr) and isinstance(
                statement.value, ast.Constant
            ), f"{path.name}:{statement.lineno}: {node.name} has a body; M5 writes those"

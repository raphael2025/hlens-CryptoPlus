"""Seam ③, enforced: modules never import each other.

``docs/03-ARCHITECTURE.md`` §2 seam ③ names this file and says the reviewer's
check *is* running it. §3 adds: "接缝 ③ 的边界测试从第一个模块起就存在" — hence
it exists in M1-A1, with only ``contracts`` populated.

How it works
------------
Every module subpackage listed in §4 is a directory directly under a
distribution root (``packages/*/src/<dist>/<module>/``). This test parses every
``.py`` file under ``packages/`` with :mod:`ast`, resolves every ``import`` —
absolute and relative — to the module subpackage it lands in, and requires the
resulting edge to appear in :data:`ALLOWED_EDGES`.

:data:`ALLOWED_EDGES` is the single place to edit. A later step that genuinely
needs a new edge adds it there with a one-line reason, which makes the addition
visible in the diff and reviewable, instead of discovering the dependency years
later in a rewrite. Editing it is meant to feel like a decision.

Why it cannot pass on nothing
-----------------------------
A boundary test that silently scans zero files is worse than no test, because
it reports green. Three guards prevent that:
``test_the_scan_actually_reaches_code`` requires files and a known module to be
found, ``test_every_module_directory_is_registered`` fails on a module
directory that is not in :data:`MODULES`, and
``test_the_checker_still_detects_a_forbidden_edge`` runs the analyzer over
synthetic sources whose violations are known, so the analyzer itself cannot rot
into a function that always returns "clean".

ruff's ``flake8-tidy-imports`` ``banned-api`` (see ``pyproject.toml``) fences
the same rule at edit time. It is the fast check; this is the authority.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = REPO_ROOT / "packages"

#: Pseudo-owner for files that sit directly in a distribution root
#: (``packages/*/src/<dist>/*.py``) rather than inside a module subpackage.
#: It is allowed to import nothing: re-exporting modules from ``hlens_core``
#: would make ``import hlens_core`` a back door around the seam.
ROOT_OWNER = "__root__"

#: The module subpackages of §4's module table. `site` and `research` are not
#: Python packages, so they are not here.
MODULES: frozenset[str] = frozenset(
    {
        "adapters",
        "backfill",
        "collector",
        "compute",
        "contracts",
        "export",
        "health",
        "liquidation",
        "notify",
        "preflight",
        "ratelimit",
        "universe",
        "wallet",
    }
)

# --------------------------------------------------------------------------- #
# THE dependency graph. This map is the interface between milestones: adding an
# edge is a design decision, so state the reason on the line you add.
#
# Seam ③: "模块间只通过数据库表与契约通信，不互相 import". `contracts` is the
# one shared layer every module may import; nothing else is importable, and a
# module that thinks it needs another module's code needs a table instead.
# --------------------------------------------------------------------------- #
ALLOWED_EDGES: dict[str, frozenset[str]] = {
    ROOT_OWNER: frozenset(),
    "adapters": frozenset({"contracts"}),
    "backfill": frozenset({"contracts"}),
    "collector": frozenset({"contracts"}),
    "compute": frozenset({"contracts"}),
    "contracts": frozenset(),  # the shared layer depends on nothing
    "export": frozenset({"contracts"}),
    "health": frozenset({"contracts"}),
    "liquidation": frozenset({"contracts"}),
    "notify": frozenset({"contracts"}),
    "preflight": frozenset({"contracts"}),
    "ratelimit": frozenset({"contracts"}),
    "universe": frozenset({"contracts"}),
    "wallet": frozenset({"contracts"}),
}


@dataclass(frozen=True)
class ImportEdge:
    """One import, resolved to the module subpackages it connects."""

    owner: str
    target: str
    path: Path
    lineno: int
    statement: str

    def __str__(self) -> str:
        where = (
            self.path.relative_to(REPO_ROOT)
            if self.path.is_relative_to(REPO_ROOT)
            else self.path
        )
        return f"{where}:{self.lineno}: {self.owner} -> {self.target}  ({self.statement})"


def _distribution_roots() -> list[Path]:
    """``packages/*/src/<dist>/`` — the importable top-level packages."""
    roots: list[Path] = []
    if not PACKAGES_DIR.is_dir():
        return roots
    for package in sorted(PACKAGES_DIR.iterdir()):
        src = package / "src"
        if not src.is_dir():
            continue
        roots.extend(
            child
            for child in sorted(src.iterdir())
            if child.is_dir() and (child / "__init__.py").is_file()
        )
    return roots


def _module_directories(root: Path) -> list[str]:
    """Names of the module subpackages that actually exist under ``root``."""
    return [
        child.name
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.name.startswith((".", "_"))
    ]


def _owner_of(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return relative.parts[0] if len(relative.parts) > 1 else ROOT_OWNER


def _package_parts(root: Path, path: Path) -> list[str]:
    """Dotted path of the *package* a file lives in, as a list of parts.

    ``hlens_core/contracts/base.py`` and ``hlens_core/contracts/__init__.py``
    both live in the package ``hlens_core.contracts``.
    """
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    parts.pop()  # drop the module name, or `__init__`
    return parts


def _target_module(dotted: list[str], first_party: frozenset[str]) -> str | None:
    """The module subpackage a dotted import lands in, if it is first-party."""
    if len(dotted) < 2 or dotted[0] not in first_party:
        return None
    return dotted[1]


def edges_in_source(
    source: str,
    *,
    owner: str,
    package_parts: list[str],
    first_party: frozenset[str],
    path: Path = Path("<memory>"),
) -> list[ImportEdge]:
    """Resolve every first-party import in one file to an :class:`ImportEdge`.

    Relative imports are resolved against ``package_parts`` so that
    ``from ..ratelimit import Ledger`` is caught exactly like the absolute
    spelling — a relative import is the easiest way to leave your own module,
    and ruff's ``ban-relative-imports = "parents"`` is only the first fence.
    """
    edges: list[ImportEdge] = []
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        candidates: list[list[str]] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name.split(".") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level 1 is the file's own package, level 2 its parent, ...
                base = package_parts[: len(package_parts) - (node.level - 1)]
            else:
                base = []
            module_parts = node.module.split(".") if node.module else []
            prefix = base + module_parts
            if module_parts or not node.level:
                candidates = [prefix]
            else:
                # `from . import x` / `from .. import x`: each name is a submodule.
                candidates = [[*prefix, alias.name] for alias in node.names]
        else:
            continue

        for dotted in candidates:
            target = _target_module(dotted, first_party)
            if target is None or target == owner:
                continue
            edges.append(
                ImportEdge(
                    owner=owner,
                    target=target,
                    path=path,
                    lineno=node.lineno,
                    statement=".".join(dotted),
                )
            )
    return edges


def _iter_python_files(root: Path) -> Iterator[Path]:
    yield from sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def collect_edges() -> tuple[list[ImportEdge], list[Path], set[str]]:
    """Scan the repository. Returns (edges, files scanned, modules found)."""
    roots = _distribution_roots()
    first_party = frozenset(root.name for root in roots)

    edges: list[ImportEdge] = []
    scanned: list[Path] = []
    modules_found: set[str] = set()

    for root in roots:
        modules_found.update(_module_directories(root))
        for path in _iter_python_files(root):
            scanned.append(path)
            edges.extend(
                edges_in_source(
                    path.read_text(encoding="utf-8"),
                    owner=_owner_of(root, path),
                    package_parts=_package_parts(root, path),
                    first_party=first_party,
                    path=path,
                )
            )
    return edges, scanned, modules_found


def _violations(edges: list[ImportEdge]) -> list[str]:
    problems: list[str] = []
    for edge in edges:
        if edge.target not in MODULES:
            problems.append(f"{edge}  [target is not a registered module of §4]")
            continue
        if edge.owner not in ALLOWED_EDGES:
            problems.append(f"{edge}  [owner is not in ALLOWED_EDGES]")
            continue
        if edge.target not in ALLOWED_EDGES[edge.owner]:
            problems.append(f"{edge}  [edge not in ALLOWED_EDGES]")
    return problems


# --------------------------------------------------------------------------- #
# Guards on the map itself
# --------------------------------------------------------------------------- #
def test_allowed_edges_map_is_well_formed() -> None:
    assert set(ALLOWED_EDGES) == MODULES | {ROOT_OWNER}, (
        "every §4 module needs a row in ALLOWED_EDGES; an empty frozenset is "
        "the default and perfectly valid answer"
    )
    for owner, targets in ALLOWED_EDGES.items():
        unknown = targets - MODULES
        assert not unknown, f"{owner} is allowed to import unknown module(s) {sorted(unknown)}"
        assert owner not in targets, f"{owner} lists itself; intra-module imports are not edges"


# --------------------------------------------------------------------------- #
# Guards against passing on nothing
# --------------------------------------------------------------------------- #
def test_the_scan_actually_reaches_code() -> None:
    _, scanned, modules_found = collect_edges()
    assert scanned, f"no Python files found under {PACKAGES_DIR} — the scan is vacuous"
    assert "contracts" in modules_found, (
        "the `contracts` module subpackage was not found; either the layout of "
        "§4 changed or this test is no longer looking at the code"
    )


def test_every_module_directory_is_registered() -> None:
    _, _, modules_found = collect_edges()
    unregistered = modules_found - MODULES
    assert not unregistered, (
        f"module subpackage(s) {sorted(unregistered)} exist on disk but are not in "
        "MODULES; register them there and give them a row in ALLOWED_EDGES"
    )


def test_the_checker_still_detects_a_forbidden_edge() -> None:
    """The analyzer must report violations it is shown, in all four spellings."""
    first_party = frozenset({"hlens_core", "hlens_collector"})
    package_parts = ["hlens_core", "contracts"]
    forbidden = [
        "from hlens_core.ratelimit import Ledger",
        "import hlens_core.ratelimit",
        "from ..ratelimit import Ledger",
        "from hlens_collector.collector import run",
    ]
    for source in forbidden:
        edges = edges_in_source(
            source, owner="contracts", package_parts=package_parts, first_party=first_party
        )
        assert edges, f"analyzer saw no edge in {source!r}"
        assert _violations(edges), f"analyzer did not flag {source!r} as a violation"

    allowed = [
        "from .base import HlensRecord",  # intra-module, not an edge at all
        "from hlens_core.contracts import MarketRecord",  # every module may
        "import httpx",  # third party
    ]
    for source in allowed:
        edges = edges_in_source(
            source,
            owner="collector",
            package_parts=["hlens_collector", "collector"],
            first_party=first_party,
        )
        assert not _violations(edges), f"analyzer wrongly flagged {source!r}"


# --------------------------------------------------------------------------- #
# The assertion itself
# --------------------------------------------------------------------------- #
def test_no_import_edge_outside_the_allowed_map() -> None:
    edges, scanned, _ = collect_edges()
    problems = _violations(edges)
    assert not problems, (
        f"seam ③ violated in {len(problems)} place(s) across {len(scanned)} file(s):\n"
        + "\n".join(f"  {problem}" for problem in problems)
        + "\n\nModules communicate through database tables and the hlens-core "
        "contracts, never by importing each other (03-ARCHITECTURE.md §2 seam "
        "③). If the dependency is genuinely required, add it to ALLOWED_EDGES "
        "in this file with a reason."
    )

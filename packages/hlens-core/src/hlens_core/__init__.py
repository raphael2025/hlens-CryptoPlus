"""hlens-core — the pieces every hlens CryptoPlus module is allowed to share.

Seam ③ (``docs/03-ARCHITECTURE.md`` §2) says modules communicate through
database tables and the contracts, never by importing each other. This root
package therefore imports nothing from its own module subpackages: re-exporting
them here would turn ``import hlens_core`` into a back door around the seam.
Import the subpackage you need, e.g. ``from hlens_core.contracts import
MarketRecord``.
"""

__all__: list[str] = []

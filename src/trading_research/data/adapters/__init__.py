"""Private row adapters used by the data-layer integrity loader.

Adapters translate physical storage into the platform's vendor-neutral core
contracts.  They are intentionally not re-exported: callers enter row loading
through :class:`trading_research.data.integrity.MarketFrameLoader` only.
"""

__all__: list[str] = []

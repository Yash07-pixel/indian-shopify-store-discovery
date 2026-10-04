from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_USER_AGENT = (
    "RivyouShopifyResearch/0.2 "
    "(+https://github.com/Yash07-pixel/indian-shopify-store-discovery)"
)


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    database: Path
    results_dir: Path
    reports_dir: Path
    cache_dir: Path
    user_agent: str
    global_concurrency: int = 20
    per_domain_delay: float = 1.0
    timeout_seconds: float = 15.0
    retries: int = 2
    max_pages_per_store: int = 8
    max_response_bytes: int = 5 * 1024 * 1024
    max_cache_entry_bytes: int = 128 * 1024

    @classmethod
    def from_root(cls, root: Path | None = None) -> "Settings":
        base = (root or Path.cwd()).resolve()
        return cls(
            root=base,
            database=base / "data" / "pipeline.sqlite3",
            results_dir=base / "data" / "results",
            reports_dir=base / "reports",
            cache_dir=base / "data" / "cache",
            user_agent=os.environ.get("SHOPIFY_INDIA_USER_AGENT", DEFAULT_USER_AGENT),
        )

    def ensure_directories(self) -> None:
        for path in (self.database.parent, self.results_dir, self.reports_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)


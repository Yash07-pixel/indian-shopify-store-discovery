from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
from typing import Iterable

import httpx

from .db import Database
from .normalize import normalize_url, origin, registrable_domain


TEAMDUKAAN_URL = "https://raw.githubusercontent.com/TeamDukaan/performance/master/shopify%20stores%20-%20shopify.csv"
MASTER_LIST_URL = "https://raw.githubusercontent.com/growthenginenowoslawski/shopify-master-list/main/shopify_master_list.csv.gz"


def _candidate_values(path: Path) -> Iterable[str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".csv":
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return []
        header = [cell.strip().lower() for cell in rows[0]]
        column = next((header.index(name) for name in ("domain_url", "domain", "url", "website") if name in header), 0)
        start = 1 if any(name in header for name in ("domain_url", "domain", "url", "website")) else 0
        return [row[column] for row in rows[start:] if len(row) > column and row[column].strip()]
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def import_values(db: Database, values: Iterable[str], source_name: str) -> tuple[int, int]:
    rows: list[tuple[str, str, str, str]] = []
    for raw in values:
        url = origin(raw)
        if not url:
            continue
        if url.startswith("http://"):
            url = "https://" + url.removeprefix("http://")
        rows.append((url, registrable_domain(url), source_name, raw))
    return db.add_candidates(rows)


def import_seed_file(db: Database, path: Path) -> tuple[int, int]:
    return import_values(db, _candidate_values(path), f"file:{path.name}")


async def _download(url: str, destination: Path, user_agent: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    async with httpx.AsyncClient(headers={"User-Agent": user_agent}, follow_redirects=True, timeout=60) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
    temporary.replace(destination)


async def import_teamdukaan(db: Database, raw_dir: Path, user_agent: str) -> tuple[int, int]:
    path = raw_dir / "teamdukaan_shopify_stores.txt"
    await _download(TEAMDUKAAN_URL, path, user_agent)
    return import_values(db, _candidate_values(path), "teamdukaan_public_list")


def _looks_indian_master_row(row: dict[str, str]) -> bool:
    domain = (row.get("domain") or "").lower().strip()
    technology = " ".join((row.get("platform", ""), row.get("technologies", ""), row.get("ecom_verdict", ""))).lower()
    return domain.endswith((".in", ".co.in")) and "shopify" in technology


async def import_master_list(db: Database, raw_dir: Path, user_agent: str) -> tuple[int, int]:
    path = raw_dir / "shopify_master_list.csv.gz"
    await _download(MASTER_LIST_URL, path, user_agent)
    added = 0
    seen = 0
    batch: list[tuple[str, str, str, str]] = []
    with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            if not _looks_indian_master_row(row):
                continue
            domain = row.get("domain", "")
            url = normalize_url(domain)
            if not url:
                continue
            seen += 1
            batch.append((url, registrable_domain(url), "shopify_master_list_indian_tld", domain))
            if len(batch) >= 2000:
                batch_added, _ = db.add_candidates(batch)
                added += batch_added
                batch.clear()
    batch_added, _ = db.add_candidates(batch)
    added += batch_added
    return added, seen


async def common_crawl_captures(url: str, limit: int = 5) -> list[dict[str, object]]:
    """Return recent archive metadata for diagnosis, never live verification."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        collections = (await client.get("https://index.commoncrawl.org/collinfo.json")).json()
        endpoint = collections[0]["cdx-api"]
        response = await client.get(
            endpoint,
            params={"url": url, "output": "json", "filter": "status:200", "collapse": "digest"},
        )
        response.raise_for_status()
    output = []
    for line in response.text.splitlines()[:limit]:
        try:
            output.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return output


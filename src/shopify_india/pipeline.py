from __future__ import annotations

import asyncio
from collections.abc import Iterable
from urllib.parse import urlsplit

import dns.resolver

from .config import Settings
from .db import Database
from .extract import (
    accepted_india,
    accepted_shopify,
    discover_relevant_links,
    extract_contacts,
    extract_description,
    extract_logo_candidate,
    extract_socials,
    india_evidence,
    infer_category,
    is_unusable_store,
    shopify_evidence,
)
from .fetcher import FetchError, RespectfulFetcher
from .models import FetchRecord, StoreRecord
from .normalize import normalize_url, origin


async def resolve_cname(host: str) -> str:
    def lookup() -> str:
        try:
            answers = dns.resolver.resolve(host, "CNAME", lifetime=5)
            return str(next(iter(answers)).target).rstrip(".")
        except Exception:  # DNS errors are expected for apex A/AAAA records.
            return ""

    return await asyncio.to_thread(lookup)


class Pipeline:
    def __init__(self, settings: Settings, db: Database, fetcher: RespectfulFetcher):
        self.settings = settings
        self.db = db
        self.fetcher = fetcher

    async def process_candidate(self, row: object) -> StoreRecord | None:
        candidate_id = int(row["id"])  # type: ignore[index]
        url = str(row["normalized_url"])  # type: ignore[index]
        self.db.mark_running(candidate_id)
        try:
            home = await self.fetcher.fetch(url, use_cache=False)
            source_names = self.db.sources_for(candidate_id)
            if not home.robots_allowed:
                record = StoreRecord(
                    domain_url=origin(url), accepted=False,
                    rejection_reasons=["robots_disallowed"], source_names=source_names,
                )
                self.db.save_store(candidate_id, record)
                return record
            if reason := is_unusable_store(home):
                record = StoreRecord(
                    domain_url=origin(home.final_url or url), accepted=False,
                    rejection_reasons=[reason], source_names=source_names,
                )
                self.db.save_store(candidate_id, record)
                return record

            pages: list[FetchRecord] = [home]
            relevant = discover_relevant_links(home, limit=max(0, self.settings.max_pages_per_store - 2))
            secondary_results = await asyncio.gather(
                *(self.fetcher.fetch(link) for link in relevant), return_exceptions=True
            )
            for result in secondary_results:
                if isinstance(result, FetchRecord) and result.robots_allowed and result.status_code < 400 and "html" in result.content_type.lower():
                    pages.append(result)

            site_origin = origin(home.final_url)
            cart = await self.fetcher.fetch(f"{site_origin}/cart.js", use_cache=False)
            cname = await resolve_cname(urlsplit(home.final_url).hostname or "")
            shop_evidence = shopify_evidence(home, cart, cname)
            indian_evidence, state, state_conflict = india_evidence(pages)
            contacts, contact_sources = extract_contacts(pages)
            socials, social_sources = extract_socials(pages)
            description, description_source = extract_description(pages)
            category, category_source = infer_category(pages)
            logo, logo_source = extract_logo_candidate(pages)
            if logo and not await self.fetcher.validate_image(logo):
                logo = ""
                logo_source = ""

            shop_ok = accepted_shopify(shop_evidence)
            india_ok = accepted_india(indian_evidence)
            reasons: list[str] = []
            if not shop_ok:
                reasons.append("insufficient_shopify_evidence")
            if not india_ok:
                reasons.append("insufficient_india_evidence")
            if state_conflict:
                reasons.append("conflicting_state_evidence")

            extraction_sources = {**contact_sources, **social_sources}
            if description_source:
                extraction_sources["description"] = description_source
            if category_source:
                extraction_sources["category"] = category_source
            if logo_source:
                extraction_sources["logo"] = logo_source

            record = StoreRecord(
                domain_url=site_origin,
                contacts=contacts,
                socials=socials,
                category=category,
                description=description,
                logo_url=logo,
                state=state,
                shopify_score=sum(item.weight for item in shop_evidence),
                india_score=sum(item.weight for item in indian_evidence),
                accepted=shop_ok and india_ok and not state_conflict,
                rejection_reasons=reasons,
                shopify_evidence=shop_evidence,
                india_evidence=indian_evidence,
                extraction_sources=extraction_sources,
                redirect_history=home.redirect_history,
                source_names=source_names,
            )
            self.db.save_store(candidate_id, record)
            return record
        except FetchError as exc:
            attempts = int(row["attempts"]) + 1  # type: ignore[index]
            self.db.mark_error(candidate_id, str(exc), retry=attempts < 3)
            return None
        except Exception as exc:
            self.db.mark_error(candidate_id, f"{type(exc).__name__}: {exc}", retry=False)
            return None

    async def run(self, target: int = 1200, max_candidates: int | None = None) -> dict[str, int]:
        processed = 0
        batch_size = min(50, max(1, self.settings.global_concurrency * 2))
        while self.db.counts().get("accepted", 0) < target:
            remaining = None if max_candidates is None else max_candidates - processed
            if remaining is not None and remaining <= 0:
                break
            batch = self.db.pending_candidates(min(batch_size, remaining) if remaining is not None else batch_size)
            if not batch:
                break
            await asyncio.gather(*(self.process_candidate(row) for row in batch))
            processed += len(batch)
        return self.db.counts()

    async def revalidate_accepted(self) -> dict[str, int]:
        rows = self.db.candidates_with_status("accepted")
        for start in range(0, len(rows), self.settings.global_concurrency):
            await asyncio.gather(*(self.process_candidate(row) for row in rows[start : start + self.settings.global_concurrency]))
        return self.db.counts()


from __future__ import annotations

import asyncio
from collections.abc import Iterable
from collections.abc import Callable
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
from .models import FetchRecord, StoreRecord, utc_now
from .normalize import normalize_url, origin


EXTRACTION_VERSION = 2


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
            category, category_source = infer_category(pages, description)
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

    async def run(
        self,
        target: int = 1200,
        max_candidates: int | None = None,
        source_name: str | None = None,
        unique_target: int | None = None,
    ) -> dict[str, int]:
        processed = 0
        batch_size = min(50, max(1, self.settings.global_concurrency * 2))
        def target_reached() -> bool:
            if unique_target is not None:
                return self.db.accepted_unique_domain_count() >= unique_target
            return self.db.counts().get("accepted", 0) >= target

        while not target_reached():
            remaining = None if max_candidates is None else max_candidates - processed
            if remaining is not None and remaining <= 0:
                break
            batch = self.db.pending_candidates(
                min(batch_size, remaining) if remaining is not None else batch_size,
                source_name=source_name,
            )
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

    async def refresh_extractions(
        self,
        domains: set[str] | None = None,
        limit: int | None = None,
        validate_images: bool = False,
        force: bool = False,
        progress: Callable[[int, int, int, int], None] | None = None,
    ) -> dict[str, object]:
        """Re-run extraction using a fresh homepage and cached secondary pages.

        This deliberately retains the earlier Shopify proof, avoiding DNS and
        cart requests. India evidence is recalculated because state/address
        context is one of the extractors being repaired.
        """
        rows = self.db.candidate_rows_with_records() if domains is not None else self.db.accepted_candidate_rows()
        if domains is not None:
            wanted = {value.lower().rstrip("/") for value in domains}
            rows = [
                row for row in rows
                if StoreRecord.model_validate_json(row["record_json"]).domain_url.lower().rstrip("/") in wanted
            ]
        if not force:
            rows = [
                row for row in rows
                if StoreRecord.model_validate_json(row["record_json"]).extraction_version < EXTRACTION_VERSION
            ]
        if limit is not None:
            rows = rows[:limit]
        refreshed = 0
        refresh_errors = 0
        removed = 0
        refresh_error_details: list[dict[str, str]] = []

        async def refresh(row: object) -> None:
            nonlocal refreshed, refresh_errors, removed
            old = StoreRecord.model_validate_json(row["record_json"])  # type: ignore[index]
            candidate_url = str(row["normalized_url"])  # type: ignore[index]
            try:
                home = await self.fetcher.fetch(candidate_url, use_cache=True)
                reason = is_unusable_store(home) if home.robots_allowed else "robots_disallowed"
                if reason:
                    old.accepted = False
                    old.rejection_reasons = [reason]
                    old.checked_at = utc_now()
                    old.extraction_version = EXTRACTION_VERSION
                    self.db.save_store(int(row["id"]), old)  # type: ignore[index]
                    refreshed += 1
                    removed += 1
                    return
                pages = [home]
                relevant = discover_relevant_links(home, limit=max(0, self.settings.max_pages_per_store - 1))
                results = await asyncio.gather(
                    *(self.fetcher.fetch(link) for link in relevant), return_exceptions=True
                )
                pages.extend(
                    result for result in results
                    if isinstance(result, FetchRecord) and result.robots_allowed
                    and result.status_code < 400 and "html" in result.content_type.lower()
                )

                india_items, state, state_conflict = india_evidence(pages)
                contacts, contact_sources = extract_contacts(pages)
                socials, social_sources = extract_socials(pages)
                description, description_source = extract_description(pages)
                category, category_source = infer_category(pages, description)
                logo, logo_source = extract_logo_candidate(pages)
                if logo and validate_images and not await self.fetcher.validate_image(logo):
                    logo = ""
                    logo_source = ""

                sources = {**contact_sources, **social_sources}
                for key, value in (
                    ("description", description_source), ("category", category_source), ("logo", logo_source),
                ):
                    if value:
                        sources[key] = value
                reasons: list[str] = []
                if not accepted_india(india_items):
                    reasons.append("insufficient_india_evidence")
                if state_conflict:
                    reasons.append("conflicting_state_evidence")
                old.domain_url = origin(home.final_url)
                old.contacts = contacts
                old.socials = socials
                old.category = category
                old.description = description
                old.logo_url = logo
                old.state = state
                old.india_evidence = india_items
                old.india_score = sum(item.weight for item in india_items)
                old.accepted = accepted_shopify(old.shopify_evidence) and accepted_india(india_items) and not state_conflict
                old.rejection_reasons = reasons
                old.extraction_sources = sources
                old.redirect_history = home.redirect_history
                old.checked_at = utc_now()
                old.extraction_version = EXTRACTION_VERSION
                self.db.save_store(int(row["id"]), old)  # type: ignore[index]
                refreshed += 1
                removed += int(not old.accepted)
            except FetchError as exc:
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    old.accepted = False
                    old.rejection_reasons = ["tls_certificate_invalid"]
                    old.checked_at = utc_now()
                    old.extraction_version = EXTRACTION_VERSION
                    self.db.save_store(int(row["id"]), old)  # type: ignore[index]
                    refreshed += 1
                    removed += 1
                    return
                refresh_errors += 1
                refresh_error_details.append({
                    "domain_url": old.domain_url,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
                return
            except Exception as exc:
                # A refresh must never destroy a previously accepted record
                # merely because a transient request failed.
                refresh_errors += 1
                refresh_error_details.append({
                    "domain_url": old.domain_url,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
                return

        for start in range(0, len(rows), self.settings.global_concurrency):
            await asyncio.gather(*(refresh(row) for row in rows[start:start + self.settings.global_concurrency]))
            if progress:
                progress(min(start + self.settings.global_concurrency, len(rows)), len(rows), refreshed, refresh_errors)
        return {
            **self.db.counts(),
            "refresh_selected": len(rows),
            "refresh_completed": refreshed,
            "refresh_errors": refresh_errors,
            "refresh_error_details": refresh_error_details,
            "refresh_removed": removed,
        }


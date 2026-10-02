from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from html import unescape
from typing import Any, Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import Evidence, FetchRecord
from .normalize import canonical_social_url, extract_emails, extract_indian_phones, extract_phones, normalize_url
from .states import find_gstins, find_states, has_indian_pin, state_from_gstin


SOCIAL_HOSTS = {
    "instagram.com": "instagram", "facebook.com": "facebook", "fb.com": "facebook",
    "twitter.com": "x", "x.com": "x", "linkedin.com": "linkedin",
    "youtube.com": "youtube", "youtu.be": "youtube",
}
SOCIAL_BLOCKED_PATHS = ("/share", "/sharer", "/intent", "/dialog", "/login")

CATEGORY_KEYWORDS = {
    "skincare/beauty": ("skincare", "skin care", "cosmetic", "makeup", "beauty", "haircare", "serum"),
    "women's apparel": ("women's clothing", "women wear", "women’s wear", "for women", "ladies wear", "ladies", "kurti", "kurtis", "saree", "sarees", "lehenga", "dresses", "blouse"),
    "men's apparel": ("men's clothing", "men's wear", "men’s wear", "menswear", "shirt", "shirts", "t-shirt", "t-shirts", "trouser", "trousers", "suit", "suits", "blazer", "blazers", "jeans"),
    "kids/baby": ("baby", "kids", "children", "infant", "toddler"),
    "jewelry/accessories": ("jewelry", "jewellery", "necklace", "earring", "bracelet", "watch", "accessories"),
    "footwear": ("footwear", "shoes", "sneaker", "sandals", "slippers"),
    "home decor/furniture": ("home decor", "furniture", "furnishing", "bedding", "lamp", "kitchenware"),
    "food/beverage": ("food", "snack", "coffee", "tea", "chocolate", "spice", "beverage", "grocery"),
    "health/wellness": ("wellness", "supplement", "ayurveda", "nutrition", "protein", "health"),
    "electronics": ("electronics", "gadget", "gadgets", "earbud", "earbuds", "earphone", "earphones", "headphone", "headphones", "speaker", "speakers", "smartwatch", "smartwatches", "charger", "chargers", "mobile", "computer"),
    "sports/outdoors": ("sports", "fitness", "outdoor", "cycling", "gym", "yoga"),
    "pet supplies": ("pet", "dog", "cat", "aquarium"),
    "books/stationery": ("book", "stationery", "notebook", "pen", "journal"),
    "gifts": ("gift hamper", "gift hampers", "gifting", "personalized gift", "personalised gift"),
    "toys/games": ("toy", "game", "puzzle"),
    "automotive": ("automotive", "car care", "motorcycle", "helmet", "vehicle"),
    "arts/crafts": ("artwork", "arts and crafts", "craft", "crafts", "painting", "paintings", "fabric", "yarn", "handmade"),
}

RELEVANT_LINK_WORDS = (
    "contact", "about", "shipping", "return", "refund", "privacy", "terms",
    "location", "store", "company", "legal", "हमारे बारे", "संपर्क",
)
PASSWORD_MARKERS = ("online store is currently unavailable", "enter using password", "opening soon")
PARKED_MARKERS = ("domain is for sale", "buy this domain", "parked free")


def soup_for(page: FetchRecord) -> BeautifulSoup:
    return BeautifulSoup(page.body, "html.parser")


def json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                output.extend(child for child in graph if isinstance(child, dict))
            output.append(item)
    return output


def visible_text(soup: BeautifulSoup) -> str:
    for node in soup(["script", "style", "noscript", "svg", "template"]):
        node.decompose()
    return re.sub(r"\s+", " ", unescape(soup.get_text(" ", strip=True))).strip()


def discover_relevant_links(page: FetchRecord, limit: int = 6) -> list[str]:
    soup = soup_for(page)
    origin_host = urlsplit(page.final_url).hostname
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        label = f"{anchor.get_text(' ', strip=True)} {anchor.get('href', '')}".lower()
        score = sum(1 for word in RELEVANT_LINK_WORDS if word in label)
        if not score:
            continue
        url = normalize_url(anchor.get("href", ""), page.final_url)
        if not url or urlsplit(url).hostname != origin_host or url in seen:
            continue
        seen.add(url)
        scored.append((score, url))
    return [url for _, url in sorted(scored, key=lambda item: (-item[0], len(item[1])))[:limit]]


def extract_contacts(pages: Iterable[FetchRecord]) -> tuple[dict[str, list[str]], dict[str, str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    sources: dict[str, str] = {}
    for page in pages:
        soup = soup_for(page)
        text = visible_text(soup)
        page_emails = set(extract_emails(text))
        page_phones = set(extract_phones(text))
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if href.lower().startswith("mailto:"):
                page_emails.update(extract_emails(href[7:].split("?", 1)[0]))
            elif href.lower().startswith("tel:"):
                page_phones.update(extract_phones(href[4:]))
        for email in page_emails:
            sources.setdefault(f"email:{email}", page.final_url)
        for phone in page_phones:
            sources.setdefault(f"phone:{phone}", page.final_url)
        emails.update(page_emails)
        phones.update(page_phones)
    return {"emails": sorted(emails), "phones": sorted(phones)}, sources


def extract_socials(pages: Iterable[FetchRecord]) -> tuple[dict[str, list[str]], dict[str, str]]:
    found: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, str] = {}
    for page in pages:
        for anchor in soup_for(page).select("a[href]"):
            url = canonical_social_url(anchor.get("href", ""))
            if not url:
                continue
            parsed = urlsplit(url)
            host = (parsed.hostname or "").removeprefix("www.")
            platform = next((name for domain, name in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
            if not platform or not parsed.path.strip("/") or parsed.path.lower().startswith(SOCIAL_BLOCKED_PATHS):
                continue
            found[platform].add(url)
            sources.setdefault(f"social:{platform}:{url}", page.final_url)
    return {key: sorted(values) for key, values in sorted(found.items())}, sources


def extract_description(pages: list[FetchRecord]) -> tuple[str, str]:
    if not pages:
        return "", ""
    for page in pages:
        soup = soup_for(page)
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta and len((value := re.sub(r"\s+", " ", meta.get("content", "")).strip())) >= 20:
            return value[:500], page.final_url
        for item in json_ld_objects(soup):
            value = item.get("description")
            if isinstance(value, str) and len(value.strip()) >= 20:
                return re.sub(r"\s+", " ", value).strip()[:500], page.final_url
    for page in pages:
        soup = soup_for(page)
        node = soup.select_one("main h1, main h2, .banner__text, .hero__text, article p, main p")
        if node and len((value := re.sub(r"\s+", " ", node.get_text(" ", strip=True))) >= 20):
            return value[:500], page.final_url
    return "", ""


def extract_logo_candidate(pages: list[FetchRecord]) -> tuple[str, str]:
    for page in pages:
        soup = soup_for(page)
        for item in json_ld_objects(soup):
            if str(item.get("@type", "")).lower() not in {"organization", "store", "brand"}:
                continue
            logo = item.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if isinstance(logo, str) and (url := normalize_url(logo, page.final_url)):
                return url, page.final_url
    for page in pages:
        soup = soup_for(page)
        selectors = (
            'header a[href="/"] img', 'header img[class*="logo" i]',
            'img[alt*="logo" i]', 'img[class*="logo" i]',
        )
        for selector in selectors:
            for image in soup.select(selector):
                src = image.get("src") or image.get("data-src") or image.get("srcset", "").split(",")[0].split(" ")[0]
                url = normalize_url(src or "", page.final_url)
                if not url or any(token in url.lower() for token in ("favicon", "icon", "sprite", "pixel")):
                    continue
                width = str(image.get("width", ""))
                height = str(image.get("height", ""))
                if width.isdigit() and height.isdigit() and int(width) <= 64 and int(height) <= 64:
                    continue
                return url, page.final_url
    return "", ""


def infer_category(pages: list[FetchRecord]) -> tuple[str, str]:
    counter: Counter[str] = Counter()
    source = ""
    for page in pages[:3]:
        soup = soup_for(page)
        weighted_chunks: list[tuple[str, int]] = []
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta and meta.get("content"):
            weighted_chunks.append((str(meta.get("content")), 5))
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        headings = " ".join(node.get_text(" ", strip=True) for node in soup.select("h1, h2")[:20])
        if title or headings:
            weighted_chunks.append((f"{title} {headings}", 3))
        for item in json_ld_objects(soup):
            for key in ("category", "productType", "name", "description"):
                if isinstance(item.get(key), str):
                    weighted_chunks.append((item[key], 4))
        main = soup.select_one("main")
        weighted_chunks.append((visible_text(main or soup)[:20000], 1))
        for chunk, weight in weighted_chunks:
            text = chunk.lower()
            for category, keywords in CATEGORY_KEYWORDS.items():
                matches = sum(len(re.findall(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text)) for keyword in keywords)
                if matches:
                    counter[category] += matches * weight
                    source = source or page.final_url
    if not counter:
        return "general merchandise", pages[0].final_url if pages else ""
    return counter.most_common(1)[0][0], source


def shopify_evidence(home: FetchRecord, cart: FetchRecord | None, cname: str = "") -> list[Evidence]:
    output: list[Evidence] = []
    lowered = home.body.lower()
    headers = home.headers
    if cname.lower().rstrip(".").endswith("shops.myshopify.com"):
        output.append(Evidence(kind="shopify_dns", value=cname, source_url=home.final_url, weight=5, strength="strong"))
    header_names = {"x-shopid", "x-shopify-stage", "x-sorting-hat-podid", "x-shopify-shop-api-call-limit"}
    present = sorted(header_names.intersection(headers))
    if present:
        output.append(Evidence(kind="shopify_headers", value=",".join(present), source_url=home.final_url, weight=5, strength="strong"))
    if re.search(r"\bShopify\.(?:shop|theme|routes)\b", home.body):
        output.append(Evidence(kind="shopify_runtime", value="Shopify runtime object", source_url=home.final_url, weight=5, strength="strong"))
    if "/cdn/shop/" in lowered or "cdn.shopify.com" in lowered:
        output.append(Evidence(kind="shopify_cdn", value="Shopify CDN asset", source_url=home.final_url, weight=2, strength="supporting"))
    if "shopify-section" in lowered or "shopify-payment-button" in lowered:
        output.append(Evidence(kind="shopify_markup", value="Shopify theme markup", source_url=home.final_url, weight=2, strength="supporting"))
    if cart and cart.status_code < 400 and "json" in cart.content_type.lower():
        try:
            data = json.loads(cart.body)
            if isinstance(data, dict) and {"items", "item_count"}.issubset(data):
                output.append(Evidence(kind="shopify_cart", value="Shopify cart schema", source_url=cart.final_url, weight=5, strength="strong"))
        except json.JSONDecodeError:
            pass
    return output


def india_evidence(pages: list[FetchRecord]) -> tuple[list[Evidence], str, bool]:
    output: list[Evidence] = []
    state_candidates: set[str] = set()
    gst_states: set[str] = set()
    for page in pages:
        soup = soup_for(page)
        text = visible_text(soup)
        gstins = find_gstins(text)
        for gstin in gstins:
            output.append(Evidence(kind="gstin", value=gstin, source_url=page.final_url, weight=5, strength="strong"))
            if state := state_from_gstin(gstin):
                gst_states.add(state)
        page_states = set(find_states(text))
        if page_states and has_indian_pin(text):
            for state in page_states:
                output.append(Evidence(kind="state_pin", value=state, source_url=page.final_url, weight=4, strength="strong"))
            state_candidates.update(page_states)
        lowered = text.lower()
        if re.search(r"(?:registered|corporate|business|office|address).{0,160}\bindia\b", lowered):
            output.append(Evidence(kind="registered_india", value="registered/business address names India", source_url=page.final_url, weight=4, strength="strong"))
        for item in json_ld_objects(soup):
            address = item.get("address")
            if isinstance(address, dict):
                country = address.get("addressCountry", "")
                region = str(address.get("addressRegion", ""))
                if str(country).strip().lower() in {"in", "india"}:
                    output.append(Evidence(kind="structured_india_address", value=json.dumps(address, ensure_ascii=False), source_url=page.final_url, weight=5, strength="strong"))
                    state_candidates.update(find_states(region))
        if extract_indian_phones(text):
            output.append(Evidence(kind="india_phone", value="public Indian phone", source_url=page.final_url, weight=2, strength="supporting"))
        if "₹" in text or re.search(r"\b(?:inr|rs\.)\s*\d", text, re.I):
            output.append(Evidence(kind="inr", value="INR storefront", source_url=page.final_url, weight=1, strength="supporting"))
        if re.search(r"\b(?:ship|deliver|shipping|returns?)\b.{0,100}\bindia\b", lowered):
            output.append(Evidence(kind="india_shipping", value="India shipping/returns language", source_url=page.final_url, weight=1, strength="supporting"))
    host = (urlsplit(pages[0].final_url).hostname or "") if pages else ""
    if host.endswith((".in", ".co.in")):
        output.append(Evidence(kind="india_domain", value=host, source_url=pages[0].final_url, weight=1, strength="supporting"))
    # De-duplicate repeated page-level signals without losing their first provenance.
    deduped: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for item in output:
        key = (item.kind, item.value)
        if key not in seen:
            deduped.append(item)
            seen.add(key)
    authoritative = gst_states or state_candidates
    conflict = len(authoritative) > 1
    state = next(iter(authoritative)) if len(authoritative) == 1 else ""
    return deduped, state, conflict


def is_unusable_store(home: FetchRecord) -> str | None:
    if home.status_code >= 400 or not home.body:
        return f"homepage_status_{home.status_code}"
    lowered = home.body.lower()
    if any(marker in lowered for marker in PASSWORD_MARKERS):
        return "password_protected"
    if any(marker in lowered for marker in PARKED_MARKERS):
        return "parked_domain"
    if "captcha" in lowered and len(lowered) < 10000:
        return "captcha_only"
    return None


def accepted_shopify(items: list[Evidence]) -> bool:
    strong_kinds = {item.kind for item in items if item.strength == "strong"}
    all_kinds = {item.kind for item in items}
    return len(strong_kinds) >= 2 or (len(strong_kinds) >= 1 and len(all_kinds) >= 2)


def accepted_india(items: list[Evidence]) -> bool:
    return any(item.strength == "strong" for item in items) and any(item.strength == "supporting" for item in items)


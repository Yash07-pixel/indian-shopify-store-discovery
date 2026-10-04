from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from html import unescape
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .models import Evidence, FetchRecord
from .normalize import canonical_social_url, extract_emails, extract_indian_phones, extract_phones, normalize_url
from .states import find_gstins, find_pins, find_states, has_indian_pin, state_from_gstin, state_from_pin


SOCIAL_HOSTS = {
    "instagram.com": "instagram", "facebook.com": "facebook", "fb.com": "facebook",
    "twitter.com": "x", "x.com": "x", "linkedin.com": "linkedin",
    "youtube.com": "youtube", "youtu.be": "youtube",
}
SOCIAL_BLOCKED_PATHS = ("/share", "/sharer", "/intent", "/dialog", "/login")

CATEGORY_KEYWORDS = {
    "skincare/beauty": ("skincare", "skin care", "cosmetic", "makeup", "beauty", "haircare", "hair care", "hair oil", "hair oils", "serum", "shampoo"),
    "women's apparel": ("women's clothing", "women wear", "women’s wear", "for women", "ladies wear", "ladies", "kurti", "kurtis", "saree", "sarees", "lehenga", "dresses", "blouse"),
    "men's apparel": ("men's clothing", "men's wear", "men’s wear", "menswear", "shirt", "shirts", "t-shirt", "t-shirts", "trouser", "trousers", "suit", "suits", "blazer", "blazers", "jeans"),
    "kids/baby": ("baby", "kids", "children", "infant", "toddler"),
    "jewelry/accessories": ("jewelry", "jewellery", "necklace", "earring", "bracelet", "watch", "accessories"),
    "footwear": ("footwear", "shoes", "sneaker", "sandals", "slippers"),
    "home decor/furniture": ("home decor", "furniture", "furnishing", "bedding", "bedsheet", "bed sheet", "pillow", "lamp", "kitchenware", "garden plant", "nursery", "orchid", "planter"),
    "food/beverage": ("food", "snack", "cookie", "cookies", "gelato", "coffee", "tea", "chocolate", "spice", "beverage", "grocery", "pickle", "pickles"),
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
COMMON_SECONDARY_PATHS = {
    "contact": ("/pages/contact", "/pages/contact-us"),
    "about": ("/pages/about-us",),
    "legal": ("/policies/privacy-policy",),
}
PASSWORD_MARKERS = ("online store is currently unavailable", "enter using password", "opening soon")
PARKED_MARKERS = ("domain is for sale", "buy this domain", "parked free")

LEGAL_PATH_WORDS = ("return", "refund", "shipping", "privacy", "terms", "policy", "legal", "track-order")
CONTACT_PATH_WORDS = ("contact", "reach-us", "customer-care", "support")
ABOUT_PATH_WORDS = ("about", "our-story", "our_story", "who-we-are")
CONTACT_SELECTORS = (
    "footer", "address", "[itemtype*='PostalAddress']", "[class*='contact' i]",
    "[id*='contact' i]", "[class*='address' i]", "[id*='address' i]",
)
THIRD_PARTY_CONTACT_MARKERS = (
    "website by", "designed by", "developed by", "powered by", "technology partner",
    "domain registrar", "registrar abuse", "technical support by",
)
DESCRIPTION_BOILERPLATE = (
    "return policy", "refund policy", "shipping policy", "privacy policy", "terms and conditions",
    "orders cannot be", "return or exchange", "eligible for return", "cancellation policy",
)
LOGO_BLOCKED_TOKENS = (
    "favicon", "apple-touch", "sprite", "tracking", "payment", "visa", "mastercard",
    "featured-in", "featured_in", "trust-badge", "trust_badge", "product", "placeholder",
)


@lru_cache(maxsize=32)
def _parsed_html(body: str) -> BeautifulSoup:
    return BeautifulSoup(body, "html.parser")


def soup_for(page: FetchRecord) -> BeautifulSoup:
    # The same page is inspected independently for contacts, socials,
    # description, category, logo, and location. Reusing the immutable parse
    # avoids parsing a large Shopify homepage six times in one record.
    return _parsed_html(page.body)


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
    # Read without mutating or cloning the tree. Extractors still need JSON-LD
    # and image attributes later, and cloning large Shopify pages is expensive.
    blocked = {"script", "style", "noscript", "svg", "template"}
    parts = []
    for node in soup.find_all(string=True):
        if any(parent.name in blocked for parent in node.parents):
            continue
        value = str(node).strip()
        if value:
            parts.append(value)
    return re.sub(r"\s+", " ", unescape(" ".join(parts))).strip()


def page_role(url: str) -> str:
    path = urlsplit(url).path.lower()
    if not path or path == "/":
        return "home"
    if any(word in path for word in LEGAL_PATH_WORDS):
        return "legal"
    if any(word in path for word in ABOUT_PATH_WORDS):
        return "about"
    if any(word in path for word in CONTACT_PATH_WORDS):
        return "contact"
    return "other"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def is_boilerplate_description(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in DESCRIPTION_BOILERPLATE)


def _contact_regions(soup: BeautifulSoup, role: str) -> list[str]:
    regions: list[str] = []
    selectors = CONTACT_SELECTORS
    if role == "contact":
        selectors = ("main", "article", *CONTACT_SELECTORS)
    for selector in selectors:
        for node in soup.select(selector):
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                regions.append(value)
    body = soup.body or soup
    if role == "contact":
        value = clean_text(body.get_text(" ", strip=True))
        if value:
            regions.append(value)
    if role == "legal":
        value = clean_text(body.get_text(" ", strip=True))
        for match in re.finditer(r"(?i)(?:contact us|e-?mail us|grievance|complaint|by mail|mailing us)", value):
            regions.append(value[max(0, match.start() - 120):match.end() + 600])
    return regions


def _near_third_party_marker(text: str, value: str) -> bool:
    index = text.lower().find(value.lower())
    if index < 0:
        return False
    context = text[max(0, index - 100): index + len(value) + 100].lower()
    return any(marker in context for marker in THIRD_PARTY_CONTACT_MARKERS)


def discover_relevant_links(page: FetchRecord, limit: int = 6) -> list[str]:
    soup = soup_for(page)
    origin_host = urlsplit(page.final_url).hostname
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        label = f"{anchor.get_text(' ', strip=True)} {anchor.get('href', '')}".lower()
        score = sum(
            (8 if word in {"contact", "संपर्क"} else 6 if word in {"about", "हमारे बारे"}
             else 5 if word in {"location", "store", "company"} else 2)
            for word in RELEVANT_LINK_WORDS if word in label
        )
        if not score:
            continue
        url = normalize_url(anchor.get("href", ""), page.final_url)
        if not url or urlsplit(url).hostname != origin_host or url in seen:
            continue
        seen.add(url)
        scored.append((score, url))
    output: list[str] = []
    role_counts: Counter[str] = Counter()
    role_limits = {"contact": 2, "about": 1, "legal": 2, "other": 2, "home": 0}
    for _, url in sorted(scored, key=lambda item: (-item[0], len(item[1]))):
        role = page_role(url)
        if role_counts[role] >= role_limits[role]:
            continue
        output.append(url)
        role_counts[role] += 1
        if len(output) >= limit:
            break
    site_origin = f"{urlsplit(page.final_url).scheme}://{urlsplit(page.final_url).netloc}"
    present_roles = {page_role(url) for url in output}
    for role, paths in COMMON_SECONDARY_PATHS.items():
        if role in present_roles:
            continue
        for path in paths:
            if len(output) >= limit:
                break
            url = normalize_url(path, site_origin)
            if url and url not in seen and url not in output:
                output.append(url)
    return output


def extract_contacts(pages: Iterable[FetchRecord]) -> tuple[dict[str, list[str]], dict[str, str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    sources: dict[str, str] = {}
    for page in pages:
        soup = soup_for(page)
        role = page_role(page.final_url)
        page_emails: set[str] = set()
        page_phones: set[str] = set()
        regions = _contact_regions(soup, role)
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if href.lower().startswith("mailto:"):
                page_emails.update(extract_emails(href[7:].split("?", 1)[0]))
            elif href.lower().startswith("tel:"):
                page_phones.update(extract_phones(href[4:]))
            else:
                parsed = urlsplit(href)
                host = (parsed.hostname or "").lower().removeprefix("www.")
                if host in {"wa.me", "api.whatsapp.com", "web.whatsapp.com"}:
                    raw = parsed.path.strip("/") or parse_qs(parsed.query).get("phone", [""])[0]
                    digits = re.sub(r"\D", "", raw)
                    if len(digits) == 12 and digits.startswith("91"):
                        page_phones.add("+" + digits)
        for text in regions:
            for email in extract_emails(text):
                if not _near_third_party_marker(text, email):
                    page_emails.add(email)
            page_phones.update(extract_phones(text))
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
    ordered = sorted(pages, key=lambda page: {"home": 0, "about": 1, "other": 2, "contact": 3, "legal": 4}[page_role(page.final_url)])
    for page in ordered:
        if page_role(page.final_url) in {"contact", "legal"}:
            continue
        soup = soup_for(page)
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta and len((value := clean_text(meta.get("content", "")))) >= 20 and not is_boilerplate_description(value):
            return value[:500], page.final_url
        for item in json_ld_objects(soup):
            item_type = str(item.get("@type", "")).lower()
            if item_type not in {"organization", "store", "brand", "website"}:
                continue
            value = item.get("description")
            if isinstance(value, str) and len(value.strip()) >= 20 and not is_boilerplate_description(value):
                return clean_text(value)[:500], page.final_url
    for page in ordered:
        if page_role(page.final_url) in {"contact", "legal"}:
            continue
        soup = soup_for(page)
        for node in soup.select(".banner__text, .hero__text, [class*='hero' i] p, article p, main p")[:20]:
            value = clean_text(node.get_text(" ", strip=True))
            if 20 <= len(value) <= 2000 and not is_boilerplate_description(value):
                return value[:500], page.final_url
    return "", ""


def extract_logo_candidate(pages: list[FetchRecord]) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for page in pages:
        soup = soup_for(page)
        for item in json_ld_objects(soup):
            if str(item.get("@type", "")).lower() not in {"organization", "store", "brand"}:
                continue
            logo = item.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if isinstance(logo, str) and (url := normalize_url(logo, page.final_url)):
                if not any(token in url.lower() for token in LOGO_BLOCKED_TOKENS):
                    candidates.append((100, url, page.final_url))
    for page in sorted(pages, key=lambda item: page_role(item.final_url) != "home"):
        soup = soup_for(page)
        selectors: tuple[tuple[str, int], ...] = (
            ('header a[href="/"] img', 90), ('header a[href="./"] img', 90),
            ('header img[class*="logo" i]', 85), ('header img[alt*="logo" i]', 85),
            ('img[class*="logo" i]', 65), ('img[alt*="logo" i]', 60),
        )
        for selector, base_score in selectors:
            for image in soup.select(selector):
                srcset = image.get("srcset") or image.get("data-srcset") or ""
                srcset_items = [item.strip().split(" ")[0] for item in srcset.split(",") if item.strip()]
                src = (
                    image.get("data-src") or image.get("data-lazy-src") or image.get("data-original")
                    or (srcset_items[-1] if srcset_items else "") or image.get("src")
                )
                url = normalize_url(src or "", page.final_url)
                lowered = (url or "").lower()
                if not url or any(token in lowered for token in LOGO_BLOCKED_TOKENS):
                    continue
                width = str(image.get("width", ""))
                height = str(image.get("height", ""))
                if width.isdigit() and height.isdigit() and int(width) <= 64 and int(height) <= 64:
                    continue
                score = base_score + (10 if "logo" in lowered else 0) + (5 if page_role(page.final_url) == "home" else 0)
                candidates.append((score, url, page.final_url))
    if candidates:
        _, url, source = max(candidates, key=lambda item: item[0])
        return url, source
    return "", ""


def infer_category(pages: list[FetchRecord], description_hint: str = "") -> tuple[str, str]:
    counter: Counter[str] = Counter()
    source_scores: dict[str, tuple[int, str]] = {}

    def add_chunk(chunk: str, weight: int, source_url: str) -> None:
        text = clean_text(chunk).lower()
        if not text:
            return
        for category, keywords in CATEGORY_KEYWORDS.items():
            matches = sum(
                min(3, len(re.findall(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text)))
                for keyword in keywords
            )
            if matches:
                points = matches * weight
                counter[category] += points
                if points > source_scores.get(category, (0, ""))[0]:
                    source_scores[category] = (points, source_url)

    if description_hint and pages:
        # The store's own concise description is less noisy than dozens of
        # repeated navigation/product-card labels (for example food gift
        # hampers being misclassified as a general gift store).
        add_chunk(description_hint, 1000, pages[0].final_url)
    ordered = sorted(pages, key=lambda page: {"home": 0, "about": 1, "other": 2, "contact": 3, "legal": 4}[page_role(page.final_url)])
    for page in ordered:
        if page_role(page.final_url) == "legal":
            continue
        soup = soup_for(page)
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta and meta.get("content"):
            add_chunk(str(meta.get("content")), 50 if page_role(page.final_url) == "home" else 8, page.final_url)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        headings = " ".join(node.get_text(" ", strip=True) for node in soup.select("h1, h2")[:20])
        if title or headings:
            add_chunk(f"{title} {headings}", 6, page.final_url)
        for item in json_ld_objects(soup):
            item_type = str(item.get("@type", "")).lower()
            for key in ("category", "productType", "name", "description"):
                if isinstance(item.get(key), str):
                    weight = 16 if item_type in {"product", "itemlist", "offer"} or key in {"category", "productType"} else 5
                    add_chunk(item[key], weight, page.final_url)
        if page_role(page.final_url) == "home":
            commerce_links = " ".join(
                f"{anchor.get_text(' ', strip=True)} {anchor.get('href', '')}"
                for anchor in soup.select("header a[href], nav a[href], a[href*='/collections/']")[:200]
            )
            add_chunk(commerce_links, 7, page.final_url)
        content_root = soup.select_one("main") or soup.body or soup
        add_chunk(visible_text(content_root)[:20000], 1, page.final_url)
    if not counter:
        return "general merchandise", pages[0].final_url if pages else ""
    category = counter.most_common(1)[0][0]
    return category, source_scores.get(category, (0, pages[0].final_url if pages else ""))[1]


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
    state_scores: dict[str, int] = defaultdict(int)

    def add_state(state: str, score: int) -> None:
        if state:
            state_scores[state] = max(state_scores[state], score)

    for page in pages:
        soup = soup_for(page)
        text = visible_text(soup)
        role = page_role(page.final_url)
        gstins = find_gstins(text)
        for gstin in gstins:
            output.append(Evidence(kind="gstin", value=gstin, source_url=page.final_url, weight=5, strength="strong"))
            if state := state_from_gstin(gstin):
                add_state(state, 100)

        # State names count only when they occur in an address-sized region
        # with a PIN. This prevents shipping lists from becoming business
        # locations merely because some unrelated PIN appears on the page.
        address_regions: list[tuple[str, int]] = []
        fresh_soup = soup_for(page)
        for selector in ("address", "[itemtype*='PostalAddress']", "[class*='address' i]", "[id*='address' i]"):
            address_regions.extend(
                (clean_text(node.get_text(" ", strip=True)), 82 if role == "contact" else 72)
                for node in fresh_soup.select(selector)
            )
        if role != "legal":
            for pin_match in re.finditer(r"(?<!\d)[1-9][0-9]{5}(?!\d)", text):
                address_regions.append((text[max(0, pin_match.start() - 220):pin_match.end() + 120], 80 if role == "contact" else 70))
        for label_match in re.finditer(r"(?i)\b(registered|corporate|business|office|store|postal)\s+address\b", text):
            priority = 95 if label_match.group(1).lower() == "registered" else 90
            address_regions.append((text[label_match.start():label_match.end() + 400], priority))
        if role == "legal":
            for mail_match in re.finditer(r"(?i)(?:mailing us at|by mail using the details|contact information below)", text):
                address_regions.append((text[mail_match.start():mail_match.end() + 500], 85))
        for region, priority in address_regions:
            region_states = find_states(region)
            if not region_states:
                region_states = sorted({state_from_pin(pin) for pin in find_pins(region)} - {""})
            if len(region_states) == 1 and has_indian_pin(region):
                state = region_states[0]
                output.append(Evidence(kind="state_pin", value=state, source_url=page.final_url, weight=4, strength="strong"))
                add_state(state, priority)

        lowered = text.lower()
        if re.search(
            r"(?:registered\s+(?:office|address)|corporate\s+(?:office|address)|"
            r"business\s+address|office\s+address).{0,200}\bindia\b",
            lowered,
        ):
            output.append(Evidence(kind="registered_india", value="registered/business address names India", source_url=page.final_url, weight=4, strength="strong"))
        for item in json_ld_objects(soup):
            address = item.get("address")
            if isinstance(address, dict):
                country = address.get("addressCountry", "")
                region = str(address.get("addressRegion", ""))
                if str(country).strip().lower() in {"in", "india"}:
                    output.append(Evidence(kind="structured_india_address", value=json.dumps(address, ensure_ascii=False), source_url=page.final_url, weight=5, strength="strong"))
                    structured_states = find_states(region)
                    if len(structured_states) == 1:
                        add_state(structured_states[0], 90)
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
    state = ""
    conflict = False
    if state_scores:
        best_score = max(state_scores.values())
        best_states = sorted(state for state, score in state_scores.items() if score == best_score)
        conflict = len(best_states) > 1
        if not conflict:
            state = best_states[0]
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


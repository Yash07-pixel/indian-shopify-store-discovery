from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import phonenumbers
import tldextract


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
TRACKING_PREFIXES = ("utm_",)
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9][a-z0-9._%+-]*@[a-z0-9.-]+\.[a-z]{2,})(?![\w-])")
OBFUSCATED_EMAIL_RE = re.compile(
    r"(?ix)\b([a-z0-9][a-z0-9._%+-]*)\s*(?:\[at\]|\(at\)|\sat\s)\s*"
    r"([a-z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*([a-z]{2,})\b"
)
TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())


def normalize_url(value: str, base: str | None = None) -> str:
    value = value.strip()
    if base:
        value = urljoin(base, value)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        value = "https://" + value.lstrip("/")
    parts = urlsplit(value)
    host = (parts.hostname or "").rstrip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if not host or "." not in host:
        return ""
    port = parts.port
    netloc = host
    if port and not ((parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    query = urlencode([
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_KEYS and not k.lower().startswith(TRACKING_PREFIXES)
    ])
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(((parts.scheme or "https").lower(), netloc, path, query, ""))


def origin(value: str) -> str:
    normalized = normalize_url(value)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def hostname(value: str) -> str:
    return (urlsplit(normalize_url(value)).hostname or "").lower()


def registrable_domain(value: str) -> str:
    host = hostname(value)
    result = TLD_EXTRACTOR(host)
    return result.top_domain_under_public_suffix or host


def canonical_social_url(value: str) -> str:
    normalized = normalize_url(value)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/")
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def extract_emails(text: str) -> list[str]:
    found = {m.group(1).lower().strip(".,;:") for m in EMAIL_RE.finditer(text)}
    for match in OBFUSCATED_EMAIL_RE.finditer(text):
        found.add(f"{match.group(1)}@{match.group(2)}.{match.group(3)}".lower())
    blocked = ("example.com", "email.com", "domain.com", "sentry.io", "wixpress.com")
    return sorted(
        email for email in found
        if not email.startswith(("noreply@", "no-reply@")) and not email.endswith(blocked)
    )


def extract_phones(text: str) -> list[str]:
    output: set[str] = set()
    for match in phonenumbers.PhoneNumberMatcher(text, "IN"):
        number = match.number
        if phonenumbers.is_valid_number(number):
            output.add(phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164))
    return sorted(output)


def extract_indian_phones(text: str) -> list[str]:
    return [number for number in extract_phones(text) if number.startswith("+91")]


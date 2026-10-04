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
TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


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
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    segments = [segment for segment in parts.path.split("/") if segment]
    lowered = [segment.lower() for segment in segments]

    # A post, reel, sharing dialog, or login page is not a business profile.
    # Keeping only profile-shaped URLs also prevents hundreds of embedded feed
    # links from being reported as separate social accounts.
    if host == "instagram.com":
        if not segments or lowered[0] in {
            "p", "reel", "reels", "stories", "explore", "accounts", "share", "direct",
        }:
            return ""
        path = "/" + segments[0]
    elif host in {"twitter.com", "x.com"}:
        if not segments or lowered[0] in {"intent", "share", "search", "home", "i", "hashtag"}:
            return ""
        host = "x.com"
        path = "/" + segments[0]
    elif host in {"facebook.com", "fb.com"}:
        if not segments or lowered[0] in {
            "share", "sharer", "sharer.php", "dialog", "login", "plugins", "watch",
            "photo", "photos", "posts", "reel", "reels", "events",
        }:
            return ""
        host = "facebook.com"
        if lowered[0] == "profile.php":
            profile_id = next((v for k, v in parse_qsl(parts.query) if k.lower() == "id" and v.isdigit()), "")
            if not profile_id:
                return ""
            return f"https://facebook.com/profile.php?id={profile_id}"
        keep = 3 if lowered[0] == "people" else 1
        path = "/" + "/".join(segments[:keep])
    elif host == "linkedin.com":
        if len(segments) < 2 or lowered[0] not in {"company", "in", "school", "showcase"}:
            return ""
        path = "/" + "/".join(segments[:2])
    elif host in {"youtube.com", "youtu.be"}:
        if host == "youtu.be" or not segments or lowered[0] in {"watch", "shorts", "playlist", "results"}:
            return ""
        host = "youtube.com"
        if segments[0].startswith("@"):
            path = "/" + segments[0]
        elif lowered[0] in {"channel", "user", "c"} and len(segments) >= 2:
            path = "/" + "/".join(segments[:2])
        else:
            return ""
    else:
        path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


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


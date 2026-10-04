from shopify_india.normalize import (
    canonical_social_url,
    extract_emails,
    extract_indian_phones,
    normalize_url,
    registrable_domain,
)
from shopify_india.states import find_gstins, find_states, state_from_gstin, state_from_pin


def test_normalize_url_and_domain() -> None:
    value = normalize_url("HTTP://WWW.Example.CO.IN:80/a//b/?utm_source=x&ok=1#fragment")
    assert value == "http://example.co.in/a/b?ok=1"
    assert registrable_domain(value) == "example.co.in"


def test_contacts_are_normalized_and_placeholders_removed() -> None:
    text = "Email Hello@Brand.IN or sales [at] brand [dot] in. Call +91 98765 43210. Ignore me@example.com"
    assert extract_emails(text) == ["hello@brand.in", "sales@brand.in"]
    assert extract_indian_phones(text) == ["+919876543210"]


def test_social_query_is_removed() -> None:
    assert canonical_social_url("https://www.instagram.com/brand/?utm_source=site") == "https://instagram.com/brand"
    assert canonical_social_url("https://instagram.com/p/ABC") == ""
    assert canonical_social_url("https://facebook.com/profile.php?id=123") == "https://facebook.com/profile.php?id=123"


def test_states_and_gstin() -> None:
    gstin = "27ABCDE1234F1Z5"
    assert find_gstins(f"GSTIN: {gstin}") == [gstin]
    assert state_from_gstin(gstin) == "Maharashtra"
    assert find_states("Registered in Orissa and NCT of Delhi") == ["Delhi", "Odisha"]
    assert state_from_pin("500033") == "Telangana"
    assert state_from_pin("400067") == "Maharashtra"


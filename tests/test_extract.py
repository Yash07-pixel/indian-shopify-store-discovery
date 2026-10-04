from shopify_india.extract import (
    accepted_india,
    accepted_shopify,
    discover_relevant_links,
    extract_contacts,
    extract_description,
    extract_logo_candidate,
    extract_socials,
    india_evidence,
    infer_category,
    shopify_evidence,
)
from shopify_india.models import FetchRecord


def page(html: str, url: str = "https://brand.in/") -> FetchRecord:
    return FetchRecord(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        body=html,
    )


def test_shopify_requires_independent_signals() -> None:
    incidental = page('<a href="https://shopify.com">Shopify</a>')
    assert not accepted_shopify(shopify_evidence(incidental, None))

    real = page('<script>Shopify.shop="brand.myshopify.com";</script><img src="/cdn/shop/logo.png">')
    evidence = shopify_evidence(real, None)
    assert accepted_shopify(evidence)
    assert {item.kind for item in evidence} == {"shopify_runtime", "shopify_cdn"}


def test_secondary_discovery_adds_only_missing_page_roles() -> None:
    current = page("""
      <a href="/pages/contact-us">Contact</a>
      <a href="/pages/our-story">About us</a>
      <a href="/policies/refund-policy">Refund policy</a>
      <a href="/policies/privacy-policy">Privacy policy</a>
      <a href="/policies/terms-of-service">Terms</a>
    """)
    links = discover_relevant_links(current, 7)
    assert "https://brand.in/pages/contact" not in links
    assert "https://brand.in/pages/about-us" not in links
    assert len([link for link in links if "/policies/" in link]) == 2
    assert len(links) == 4


def test_india_needs_strong_and_supporting_evidence() -> None:
    weak = page("Prices ₹999. We ship throughout India.")
    evidence, state, conflict = india_evidence([weak])
    assert not accepted_india(evidence)
    assert state == ""
    assert not conflict

    customer_address = page("Enter your delivery address in India. Call +91 98765 43210. Prices ₹999.")
    evidence, state, conflict = india_evidence([customer_address])
    assert not accepted_india(evidence)
    assert not any(item.kind == "registered_india" for item in evidence)

    strong = page("Registered office: Pune, Maharashtra 411001, India. GSTIN 27ABCDE1234F1Z5. Call +91 98765 43210")
    evidence, state, conflict = india_evidence([strong])
    assert accepted_india(evidence)
    assert state == "Maharashtra"
    assert not conflict


def test_shipping_or_returns_state_does_not_override_business_state() -> None:
    first = page("Office Maharashtra 411001 India. +91 9876543210")
    second = page("Returns Delhi 110001 India", "https://brand.in/returns")
    _, state, conflict = india_evidence([first, second])
    assert state == "Maharashtra"
    assert not conflict


def test_field_precedence_and_favicon_rejection() -> None:
    html = """
    <html><head>
      <meta name="description" content="Natural skincare made in India for everyday routines.">
      <link rel="icon" href="/favicon.ico">
      <script type="application/ld+json">{"@type":"Organization","logo":"/assets/brand-logo.png"}</script>
    </head><body>
      <a href="https://instagram.com/brand/?utm_source=footer">Instagram</a>
      <p>Shop our skincare serum and beauty collection.</p>
    </body></html>
    """
    current = page(html)
    assert extract_description([current])[0].startswith("Natural skincare")
    assert extract_logo_candidate([current])[0] == "https://brand.in/assets/brand-logo.png"
    assert extract_socials([current])[0] == {"instagram": ["https://instagram.com/brand"]}
    assert infer_category([current])[0] == "skincare/beauty"


def test_category_keywords_do_not_match_inside_words() -> None:
    current = page("<p>Smartwatches, wireless headphones, earbuds and mobile chargers.</p>")
    assert infer_category([current])[0] == "electronics"


def test_meta_description_outweighs_generic_menu_terms() -> None:
    current = page("""
      <head><meta name="description" content="Premium men's wear: shirts, suits, trousers and blazers."></head>
      <body><main><nav>Shoes Gift Card</nav><p>Premium shirts and suits.</p></main></body>
    """)
    assert infer_category([current])[0] == "men's apparel"


def test_womens_audience_language_is_a_category_signal() -> None:
    current = page("""
      <head><meta name="description" content="Designer ethnic and western wear for women and ladies: suit sets, lehengas and sarees."></head>
      <body><main><p>Shop our apparel collection.</p></main></body>
    """)
    assert infer_category([current])[0] == "women's apparel"


def test_social_posts_and_incomplete_facebook_profiles_are_rejected() -> None:
    current = page("""
      <a href="https://instagram.com/brand">Profile</a>
      <a href="https://instagram.com/p/ABC123">Post</a>
      <a href="https://instagram.com/reel/XYZ123">Reel</a>
      <a href="https://facebook.com/profile.php">Broken Facebook</a>
      <a href="https://facebook.com/profile.php?id=12345">Facebook</a>
    """)
    assert extract_socials([current])[0] == {
        "facebook": ["https://facebook.com/profile.php?id=12345"],
        "instagram": ["https://instagram.com/brand"],
    }


def test_policy_description_is_not_used() -> None:
    legal = page(
        '<meta name="description" content="Return policy: orders cannot be returned or exchanged after dispatch.">',
        "https://brand.in/policies/returns",
    )
    about = page("<main><p>We make naturally dyed clothing with local artisans in India.</p></main>", "https://brand.in/pages/about")
    assert extract_description([legal, about])[0].startswith("We make naturally dyed")


def test_contact_page_is_not_used_as_brand_description() -> None:
    contact = page("<main><p>Contact us for returns, orders, and customer support.</p></main>", "https://brand.in/pages/contact")
    assert extract_description([contact]) == ("", "")


def test_contacts_use_business_regions_and_reject_developer_credit() -> None:
    current = page("""
      <main><p>Random order reference 9876543210</p></main>
      <footer>
        <a href="mailto:hello@brand.in">hello@brand.in</a>
        <p>Website developed by Niche Technologies nichetechpl@nichetechpl.com</p>
        <a href="tel:+91 98765 43210">Call us</a>
      </footer>
    """)
    contacts, _ = extract_contacts([current])
    assert contacts == {"emails": ["hello@brand.in"], "phones": ["+919876543210"]}


def test_whatsapp_business_number_is_a_contact() -> None:
    current = page('<footer><a href="https://wa.me/919876543210">WhatsApp</a></footer>')
    assert extract_contacts([current])[0]["phones"] == ["+919876543210"]


def test_public_legal_contact_section_is_extracted() -> None:
    legal = page(
        "For privacy complaints, contact us by email at grievance@brand.in or call +91 98765 43210.",
        "https://brand.in/policies/privacy-policy",
    )
    assert extract_contacts([legal])[0] == {
        "emails": ["grievance@brand.in"], "phones": ["+919876543210"],
    }


def test_labeled_legal_address_can_prove_state_but_shipping_list_cannot() -> None:
    legal = page(
        "Registered office address: Ahmedabad, Gujarat 380015, India. Contact +91 98765 43210.",
        "https://brand.in/policies/privacy-policy",
    )
    evidence, state, conflict = india_evidence([legal])
    assert accepted_india(evidence)
    assert state == "Gujarat"
    assert not conflict


def test_lazy_loaded_header_logo_beats_featured_in_image() -> None:
    current = page("""
      <header><a href="/"><img data-src="/assets/real-logo.svg" alt="Brand logo"></a></header>
      <img class="logo-wall" src="/assets/featured_in_logos.png" alt="Featured logos">
    """)
    assert extract_logo_candidate([current])[0] == "https://brand.in/assets/real-logo.svg"


def test_product_context_outweighs_policy_and_menu_noise() -> None:
    current = page("""
      <head><meta name="description" content="Cookies and artisan gelato made fresh for dessert lovers."></head>
      <body><nav>Corporate Gifts Electronics</nav><main><p>Shop cookies, gelato and chocolate.</p></main></body>
    """)
    assert infer_category([current])[0] == "food/beverage"


def test_own_description_is_the_strongest_category_hint() -> None:
    current = page("<main>Corporate gifts tea table coffee table restaurant projects.</main>")
    assert infer_category([current], "An award-winning furniture design and manufacturing brand.")[0] == "home decor/furniture"
    assert infer_category([current], "Handmade pickles, seasonings, snacks and sweets.")[0] == "food/beverage"
    assert infer_category([current], "Natural hair oils based on ancient remedies.")[0] == "skincare/beauty"


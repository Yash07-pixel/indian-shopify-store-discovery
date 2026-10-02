from shopify_india.extract import (
    accepted_india,
    accepted_shopify,
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


def test_india_needs_strong_and_supporting_evidence() -> None:
    weak = page("Prices ₹999. We ship throughout India.")
    evidence, state, conflict = india_evidence([weak])
    assert not accepted_india(evidence)
    assert state == ""
    assert not conflict

    strong = page("Registered office: Pune, Maharashtra 411001, India. GSTIN 27ABCDE1234F1Z5. Call +91 98765 43210")
    evidence, state, conflict = india_evidence([strong])
    assert accepted_india(evidence)
    assert state == "Maharashtra"
    assert not conflict


def test_conflicting_states_are_flagged() -> None:
    first = page("Office Maharashtra 411001 India. +91 9876543210")
    second = page("Returns Delhi 110001 India", "https://brand.in/returns")
    _, state, conflict = india_evidence([first, second])
    assert state == ""
    assert conflict


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


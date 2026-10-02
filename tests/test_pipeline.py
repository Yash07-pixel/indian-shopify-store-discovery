import json

import httpx
import pytest

from shopify_india.config import Settings
from shopify_india.db import Database
from shopify_india.fetcher import RespectfulFetcher
from shopify_india.pipeline import Pipeline


HOME = """
<html><head>
  <meta name="description" content="Thoughtful natural skincare made by an Indian business.">
  <script>Shopify.shop = "brand.myshopify.com";</script>
  <script type="application/ld+json">{"@type":"Organization","logo":"/logo.png","address":{"@type":"PostalAddress","addressRegion":"Maharashtra","addressCountry":"IN"}}</script>
</head><body class="shopify-section">
  <img src="/cdn/shop/product.jpg"><a href="/pages/contact">Contact</a>
  <p>Skincare, beauty and serum products. Prices ₹999.</p>
</body></html>
"""

CONTACT = "Registered office: Pune, Maharashtra 411001, India. GSTIN 27ABCDE1234F1Z5. Email hello@brand.in. Phone +91 98765 43210."


@pytest.mark.asyncio
async def test_mocked_end_to_end_pipeline(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        if path == "/":
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html", "x-shopid": "123"})
        if path == "/pages/contact":
            return httpx.Response(200, text=f"<html><body>{CONTACT}</body></html>", headers={"content-type": "text/html"})
        if path == "/cart.js":
            return httpx.Response(200, text=json.dumps({"items": [], "item_count": 0}), headers={"content-type": "application/json"})
        if path == "/logo.png":
            return httpx.Response(200, content=b"PNG" * 100, headers={"content-type": "image/png"})
        return httpx.Response(404, text="not found")

    async def no_cname(_: str) -> str:
        return ""

    monkeypatch.setattr("shopify_india.pipeline.resolve_cname", no_cname)
    settings = Settings.from_root(tmp_path)
    settings.ensure_directories()
    db = Database(settings.database)
    db.add_candidate("https://brand.in/", "brand.in", "test", "brand.in")

    async with RespectfulFetcher(settings, transport=httpx.MockTransport(handler)) as fetcher:
        counts = await Pipeline(settings, db, fetcher).run(target=1)

    assert counts["accepted"] == 1
    record = db.accepted_records()[0]
    assert record.state == "Maharashtra"
    assert record.contacts["emails"] == ["hello@brand.in"]
    assert record.logo_url == "https://brand.in/logo.png"
    assert record.shopify_score >= 7
    assert record.india_score >= 7


from shopify_india.db import Database
from shopify_india.models import StoreRecord


def test_pending_candidates_can_be_filtered_by_source(tmp_path) -> None:
    db = Database(tmp_path / "pipeline.sqlite3")
    db.add_candidate("https://first.in", "first.in", "source_a", "first.in")
    db.add_candidate("https://second.in", "second.in", "source_b", "second.in")
    assert [row["normalized_url"] for row in db.pending_candidates(source_name="source_b")] == ["https://second.in"]


def test_unique_accepted_domain_count_ignores_redirect_duplicates(tmp_path) -> None:
    db = Database(tmp_path / "pipeline.sqlite3")
    for url in ("https://first.in", "https://alias.in"):
        db.add_candidate(url, url.removeprefix("https://"), "test", url)
        row = db.pending_candidates()[0]
        db.save_store(row["id"], StoreRecord(domain_url="https://final.in", accepted=True))
    assert db.accepted_unique_domain_count() == 1

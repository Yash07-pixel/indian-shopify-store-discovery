from shopify_india.db import Database
from shopify_india.sources import import_values


def test_import_values_batches_and_deduplicates_origins(tmp_path) -> None:
    db = Database(tmp_path / "pipeline.sqlite3")
    added, seen = import_values(
        db,
        ["https://www.example.in/a", "http://example.in/other", "https://shop.example.in/path"],
        "test",
    )
    assert (added, seen) == (2, 3)
    # Paths and www are normalized, so these are already present.
    added_again, seen_again = import_values(db, ["https://example.in/new"], "second")
    assert (added_again, seen_again) == (0, 1)

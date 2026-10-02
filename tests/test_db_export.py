import csv
import json

from shopify_india.db import Database
from shopify_india.exporter import CSV_FIELDS, export_results, write_audit_sample, write_report
from shopify_india.models import Evidence, StoreRecord


def accepted_record() -> StoreRecord:
    return StoreRecord(
        domain_url="https://brand.in",
        contacts={"emails": ["hello@brand.in"], "phones": ["+919876543210"]},
        socials={"instagram": ["https://instagram.com/brand"]},
        category="skincare/beauty",
        description="Indian skincare",
        logo_url="https://brand.in/logo.png",
        state="Maharashtra",
        shopify_score=7,
        india_score=8,
        accepted=True,
        shopify_evidence=[Evidence(kind="shopify_runtime", value="yes", source_url="https://brand.in", weight=5, strength="strong")],
        india_evidence=[Evidence(kind="gstin", value="27ABCDE1234F1Z5", source_url="https://brand.in", weight=5, strength="strong")],
        source_names=["test"],
    )


def test_database_is_resumable_and_exports_exact_schema(tmp_path) -> None:
    db = Database(tmp_path / "pipeline.sqlite3")
    assert db.add_candidate("https://brand.in/", "brand.in", "test", "brand.in")
    assert not db.add_candidate("https://brand.in/", "brand.in", "other", "brand.in")
    row = db.pending_candidates()[0]
    db.save_store(row["id"], accepted_record())
    assert db.counts() == {"accepted": 1}

    csv_path, evidence_path = export_results(db, tmp_path / "results")
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == CSV_FIELDS
    assert json.loads(rows[0]["contacts"])["emails"] == ["hello@brand.in"]
    assert json.loads(evidence_path.read_text(encoding="utf-8").splitlines()[0])["accepted"] is True

    assert write_report(db, tmp_path / "reports").exists()
    assert write_audit_sample(db, tmp_path / "reports", 100, 1).exists()


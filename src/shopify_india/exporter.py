from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .db import Database
from .models import StoreRecord, utc_now


CSV_FIELDS = ["domain_url", "contacts", "socials", "category", "description", "logo_url", "state"]


def export_results(db: Database, results_dir: Path) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    records = db.accepted_records()
    csv_path = results_dir / "indian_shopify_stores.csv"
    evidence_path = results_dir / "audit_evidence.jsonl"
    seen: set[str] = set()
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            if record.domain_url in seen:
                continue
            seen.add(record.domain_url)
            writer.writerow({
                "domain_url": record.domain_url,
                "contacts": json.dumps(record.contacts, ensure_ascii=False, sort_keys=True),
                "socials": json.dumps(record.socials, ensure_ascii=False, sort_keys=True),
                "category": record.category,
                "description": record.description,
                "logo_url": record.logo_url,
                "state": record.state,
            })
    with evidence_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.audit_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return csv_path, evidence_path


def _manual_audit_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"status": "not_reviewed", "reviewed": 0}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    reviewed = [row for row in rows if row.get("shopify_correct", "").strip() and row.get("india_correct", "").strip()]
    if not reviewed:
        return {"status": "not_reviewed", "reviewed": 0}

    def is_yes(value: str) -> bool:
        return value.strip().lower() in {"yes", "y", "true", "1"}

    shopify_precision = sum(is_yes(row["shopify_correct"]) for row in reviewed) / len(reviewed)
    india_precision = sum(is_yes(row["india_correct"]) for row in reviewed) / len(reviewed)
    return {
        "status": "pass" if shopify_precision >= 0.95 and india_precision >= 0.95 else "fail",
        "reviewed": len(reviewed),
        "shopify_precision_percent": round(shopify_precision * 100, 2),
        "india_precision_percent": round(india_precision * 100, 2),
        "required_precision_percent": 95.0,
    }


def build_quality_report(db: Database, audit_path: Path | None = None) -> dict[str, object]:
    records = db.all_records()
    accepted = [record for record in records if record.accepted]
    missing = Counter()
    for record in accepted:
        for field in ("contacts", "socials", "category", "description", "logo_url", "state"):
            value = getattr(record, field)
            if not value or value == {"emails": [], "phones": []}:
                missing[field] += 1
    rejected = Counter(reason for record in records if not record.accepted for reason in record.rejection_reasons)
    source_yield: dict[str, dict[str, int]] = defaultdict(lambda: {"seen": 0, "accepted": 0})
    for record in records:
        for source in record.source_names:
            source_yield[source]["seen"] += 1
            source_yield[source]["accepted"] += int(record.accepted)
    total = len(accepted)
    return {
        "generated_at": utc_now(),
        "candidate_status_counts": db.counts(),
        "processed_records": len(records),
        "accepted_records": total,
        "rejection_reasons": dict(rejected),
        "missing_fields": {
            field: {"count": missing[field], "percent": round(missing[field] * 100 / total, 2) if total else 0.0}
            for field in ("contacts", "socials", "category", "description", "logo_url", "state")
        },
        "state_distribution": dict(Counter(record.state or "missing" for record in accepted)),
        "category_distribution": dict(Counter(record.category or "missing" for record in accepted)),
        "source_yield": dict(source_yield),
        "confidence": {
            "average_shopify_score": round(mean([r.shopify_score for r in accepted]), 2) if accepted else 0,
            "average_india_score": round(mean([r.india_score for r in accepted]), 2) if accepted else 0,
            "minimum_shopify_score": min([r.shopify_score for r in accepted], default=0),
            "minimum_india_score": min([r.india_score for r in accepted], default=0),
        },
        "manual_audit": _manual_audit_summary(audit_path) if audit_path else {"status": "not_generated", "reviewed": 0},
    }


def write_report(db: Database, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "quality_report.json"
    path.write_text(
        json.dumps(build_quality_report(db, reports_dir / "manual_audit.csv"), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_audit_sample(db: Database, reports_dir: Path, size: int, seed: int) -> Path:
    records = db.accepted_records()
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[StoreRecord]] = defaultdict(list)
    for record in records:
        confidence = "high" if record.shopify_score + record.india_score >= 15 else "threshold"
        groups[(record.state or "missing", confidence)].append(record)
    selected: list[StoreRecord] = []
    keys = list(groups)
    rng.shuffle(keys)
    while keys and len(selected) < min(size, len(records)):
        next_keys = []
        for key in keys:
            if groups[key] and len(selected) < size:
                selected.append(groups[key].pop(rng.randrange(len(groups[key]))))
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "manual_audit.csv"
    fields = ["domain_url", "state", "shopify_score", "india_score", "shopify_correct", "india_correct", "fields_correct", "reviewer_notes"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in selected:
            writer.writerow({
                "domain_url": record.domain_url, "state": record.state,
                "shopify_score": record.shopify_score, "india_score": record.india_score,
                "shopify_correct": "", "india_correct": "", "fields_correct": "", "reviewer_notes": "",
            })
    return path


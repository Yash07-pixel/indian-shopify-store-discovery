from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from .config import Settings
from .db import Database
from .exporter import export_results, write_audit_sample, write_report
from .fetcher import RespectfulFetcher
from .pipeline import Pipeline
from .sources import common_crawl_captures, import_master_list, import_seed_file, import_teamdukaan


app = typer.Typer(
    no_args_is_help=True,
    help="Discover and verify Indian Shopify stores using public evidence.",
)


def context(root: Path | None) -> tuple[Settings, Database]:
    settings = Settings.from_root(root)
    settings.ensure_directories()
    return settings, Database(settings.database)


async def discover_async(settings: Settings, db: Database, seeds: list[Path], skip_master: bool) -> dict[str, object]:
    raw_dir = settings.root / "data" / "raw"
    summary: dict[str, object] = {}
    team_added, team_seen = await import_teamdukaan(db, raw_dir, settings.user_agent)
    summary["teamdukaan"] = {"added": team_added, "eligible": team_seen}
    if not skip_master:
        master_added, master_seen = await import_master_list(db, raw_dir, settings.user_agent)
        summary["master_list"] = {"added": master_added, "eligible": master_seen}
    for seed in seeds:
        added, seen = import_seed_file(db, seed)
        summary[f"file:{seed.name}"] = {"added": added, "eligible": seen}
    summary["status"] = db.counts()
    return summary


@app.command()
def discover(
    seed: Annotated[list[Path] | None, typer.Option("--seed", help="Additional TXT or CSV seed file.")] = None,
    skip_master: Annotated[bool, typer.Option(help="Skip the large public master-list download.")] = False,
    root: Annotated[Path | None, typer.Option(help="Project root; defaults to the current directory.")] = None,
) -> None:
    """Download public candidate lists and add normalized candidates to SQLite."""
    settings, db = context(root)
    summary = asyncio.run(discover_async(settings, db, seed or [], skip_master))
    typer.echo(json.dumps(summary, indent=2))


@app.command(name="run")
def run_pipeline(
    target: Annotated[int, typer.Option(min=1, help="Stop after this many accepted stores.")] = 1200,
    max_candidates: Annotated[int | None, typer.Option(min=1, help="Optional cap for a smoke or staged run.")] = None,
    skip_discovery: Annotated[bool, typer.Option(help="Do not auto-discover when the database is empty.")] = False,
    skip_master: Annotated[bool, typer.Option(help="Skip the large public master list during auto-discovery.")] = False,
    root: Annotated[Path | None, typer.Option(help="Project root; defaults to the current directory.")] = None,
) -> None:
    """Resume crawling pending candidates until the verified target is reached."""
    settings, db = context(root)

    async def execute() -> dict[str, int]:
        if not db.counts() and not skip_discovery:
            await discover_async(settings, db, [], skip_master)
        async with RespectfulFetcher(settings) as fetcher:
            return await Pipeline(settings, db, fetcher).run(target=target, max_candidates=max_candidates)

    typer.echo(json.dumps(asyncio.run(execute()), indent=2))


@app.command()
def export(
    revalidate: Annotated[bool, typer.Option("--revalidate/--no-revalidate", help="Freshly recrawl accepted stores before writing output.")] = True,
    root: Annotated[Path | None, typer.Option(help="Project root; defaults to the current directory.")] = None,
) -> None:
    """Write the seven-field CSV and full evidence JSONL."""
    settings, db = context(root)
    if revalidate and db.counts().get("accepted", 0):
        async def refresh() -> None:
            async with RespectfulFetcher(settings) as fetcher:
                await Pipeline(settings, db, fetcher).revalidate_accepted()
        asyncio.run(refresh())
    csv_path, evidence_path = export_results(db, settings.results_dir)
    report_path = write_report(db, settings.reports_dir)
    typer.echo(f"CSV: {csv_path}\nEvidence: {evidence_path}\nReport: {report_path}")


@app.command(name="audit-sample")
def audit_sample(
    size: Annotated[int, typer.Option(min=1)] = 100,
    seed: Annotated[int, typer.Option()] = 20261002,
    root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Create a reproducible, stratified manual-review worksheet."""
    settings, db = context(root)
    typer.echo(write_audit_sample(db, settings.reports_dir, size, seed))


@app.command()
def report(root: Annotated[Path | None, typer.Option()] = None) -> None:
    """Regenerate aggregate quality and missing-field statistics."""
    settings, db = context(root)
    typer.echo(write_report(db, settings.reports_dir))


@app.command()
def status(root: Annotated[Path | None, typer.Option()] = None) -> None:
    """Show resumable pipeline progress."""
    _, db = context(root)
    typer.echo(json.dumps(db.counts(), indent=2))


@app.command(name="diagnose-common-crawl")
def diagnose_common_crawl(
    url: str,
    limit: Annotated[int, typer.Option(min=1, max=20)] = 5,
) -> None:
    """Show archive metadata for diagnosis; it never counts as live proof."""
    typer.echo(json.dumps(asyncio.run(common_crawl_captures(url, limit)), indent=2))


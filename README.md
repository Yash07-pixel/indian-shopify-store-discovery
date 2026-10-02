# Indian Shopify Store Discovery

This repository contains a resumable pipeline for discovering Shopify stores whose public websites provide evidence that the operating business is situated in India.

> **Current snapshot status:** the checked-in result is a four-store live smoke sample used to validate the pipeline end to end. It is **not the final assignment submission**. Run the full `--target 1200` crawl, complete the 100-row manual audit, confirm at least 95% precision, and commit the regenerated outputs before sending the repository to an evaluator.

## Why the pipeline verifies twice

A `.in` domain does not prove that a business is Indian. Likewise, a page can mention Shopify without being hosted by Shopify. Seed lists also become stale as stores close or migrate platforms.

So the real problem is not collecting URLs. It is independently proving two facts:

1. Is the current live storefront powered by Shopify?
2. Does the current public site identify an operating or registered location in India?

Candidate lists only answer “where should we look?” They never make a store pass verification.

## Exact approach

### 1. Discover candidates

The `discover` command downloads two public seed lists:

- [TeamDukaan's Indian Shopify list](https://github.com/TeamDukaan/performance/blob/master/shopify%20stores%20-%20shopify.csv)
- [Shopify master domain list](https://github.com/growthenginenowoslawski/shopify-master-list), filtered to Shopify-labelled `.in` and `.co.in` domains

The raw third-party files are cached locally and ignored by Git. Their contents are treated as untrusted leads. Additional TXT or CSV files can be supplied with `--seed`.

The importer converts Unicode hostnames to IDNA, removes `www`, fragments, default ports and tracking parameters, and deduplicates normalized URLs in SQLite. The source of each candidate is preserved.

Common Crawl is available through `diagnose-common-crawl`. It is deliberately diagnostic only: an archived page cannot prove that a store is currently live.

### 2. Fetch responsibly

The crawler reads `robots.txt`, permits only one active request per host, waits at least one second between requests to a host, and allows 20 requests globally. It visits at most eight useful endpoints per store: the homepage, Shopify cart endpoint, and up to six internal Contact/About/Shipping/Returns/Privacy/Terms/location pages.

Requests time out after 15 seconds. HTTP 429 and transient 5xx responses receive two exponential-backoff retries, including `Retry-After` support. Successful responses are cached. Password-only, parked, CAPTCHA-only, unreachable, and unverifiable robots-blocked sites are rejected rather than forced into the result.

### 3. Prove Shopify

The verifier records evidence rather than a single yes/no heuristic. Strong signals are a Shopify DNS CNAME, Shopify-specific response headers, Shopify runtime objects, or a correctly shaped `/cart.js` response. Shopify CDN URLs and theme section markup corroborate those signals.

A store passes only with two strong signal types, or one strong signal plus an independent corroborating type. Text saying “Shopify,” an app badge, or a link to Shopify does not count.

### 4. Prove India

“Indian” means the website identifies an Indian operating or registered business location. A `.in` domain, INR prices, or India shipping by itself is insufficient.

Strong evidence is a GSTIN, structured Indian organization address, registered/business-office text naming India, or an Indian state next to a valid six-digit PIN. Supporting evidence includes a valid `+91` phone, `.in` domain, INR storefront, or India shipping/returns language.

Acceptance requires at least one strong signal and one independent supporting signal. State names are normalized across all states and union territories; GSTIN prefixes can provide the state. Conflicting state evidence rejects the row for manual review instead of guessing.

### 5. Extract requested fields

The final CSV has exactly seven columns:

| Column | Rule |
|---|---|
| `domain_url` | Final live storefront origin after redirects |
| `contacts` | JSON with all public, valid business emails and Indian phone numbers |
| `socials` | JSON with canonical Instagram, Facebook, X, LinkedIn and YouTube profile URLs |
| `category` | Controlled category inferred from product structured data, collections, navigation and copy |
| `description` | Own-site meta description, structured description, hero text or About introduction, in that order |
| `logo_url` | Organization structured-data logo or visible header logo; favicon/icon URLs are rejected |
| `state` | Structured address, public address text, or GSTIN-derived canonical state |

Every accepted row also has a JSONL audit record containing evidence, scores, source pages, redirects, extraction provenance and check time.

### 6. Deduplicate and validate

Redirect targets and normalized domains prevent trivial duplicates. The export command freshly revalidates accepted stores by default. A deterministic, state- and confidence-stratified sample of 100 rows is written for manual review. The submission target is at least 95% precision for both Shopify and Indian-business status; thresholds must be tightened and the crawl repeated if the audit misses that mark.

## Setup on Windows PowerShell

Python 3.12 or newer is required.

```powershell
cd C:\Users\Dell\Desktop\r
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

No paid API or API key is required. Before a public crawl, set a user agent that identifies the published repository:

```powershell
$env:SHOPIFY_INDIA_USER_AGENT = "RivyouShopifyResearch/0.1 (+https://github.com/YOUR_USERNAME/indian-shopify-store-discovery)"
```

## Running

Start with a small staged run:

```powershell
python -m shopify_india discover
python -m shopify_india run --target 10 --max-candidates 100
python -m shopify_india export --no-revalidate
python -m shopify_india audit-sample --size 10 --seed 20261002
```

Then resume toward the real target:

```powershell
python -m shopify_india run --target 1200
python -m shopify_india export
python -m shopify_india audit-sample --size 100 --seed 20261002
python -m shopify_india report
```

`run` resumes pending/retry rows from `data/pipeline.sqlite3`; stopping it does not discard completed work. Use `python -m shopify_india status` to see progress. `--skip-master` avoids the large master-list download during a quick smoke test.

## Outputs

- `data/results/indian_shopify_stores.csv` — submission file
- `data/results/audit_evidence.jsonl` — row-level proof and provenance
- `reports/quality_report.json` — counts, rejection reasons, missingness, distributions and source yield
- `reports/manual_audit.csv` — reproducible human-review worksheet

The report discloses missing values rather than inventing them. Many legitimate stores do not publish every phone, social profile, description, logo or unambiguous state.

## Assumptions and edge cases

- India-specific storefronts owned by foreign brands pass only when the site names an Indian operating or registered entity/address.
- A global store that merely ships to India does not pass.
- A Shopify-powered marketing page without a usable storefront does not pass.
- Public business inboxes and phone numbers are collected; personal WHOIS data and authenticated content are not.
- When multiple state signals conflict, the store is rejected pending review.
- Static HTML is used because Shopify themes normally render the needed metadata server-side. JavaScript browser automation is intentionally excluded from this version.

## Known limitations and scaling

At 10× scale, SQLite writes and a single residential network become bottlenecks. Move the queue to PostgreSQL, store responses in object storage, partition work by registrable domain, and run distributed workers with a shared per-domain rate limiter.

At 100× scale, candidate coverage becomes the harder problem. Query the Common Crawl columnar index in bulk, process certificate-transparency and DNS datasets, add continuous freshness checks, and separate collection from extraction with a durable job system. Browser rendering should remain a measured fallback because it greatly increases cost.

Public pages change after collection, false signals remain possible, and restrictive sites will be underrepresented. This is why the output includes timestamps, evidence, rejection reasons, and a manual precision audit.

## Runtime and effort

The software records timestamps and pipeline counts, but actual wall-clock runtime depends on network conditions, robots rules and retries. Fill in the following only after completing the final run and manual audit:

- Development and validation time: **TBD after completion**
- Full crawl runtime: **TBD after completion**
- Manual audit precision: **TBD after reviewing `reports/manual_audit.csv`**

## License

The code is MIT licensed. Third-party seed lists retain their original terms and are not committed to this repository. The generated dataset is a dated compilation of public business information; verify current site terms and applicable law before reuse.


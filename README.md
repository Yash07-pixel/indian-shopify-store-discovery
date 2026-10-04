# Indian Shopify Store Discovery

This repository contains a resumable pipeline for discovering Shopify stores whose public websites provide evidence that the operating business is situated in India.



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

Strong evidence is a GSTIN, structured Indian organization address, explicit registered/corporate/business-office text naming India, or an Indian state next to a valid six-digit PIN. Generic customer-facing phrases such as “delivery address in India” are not registered-office evidence. Conservative, unambiguous PIN prefixes can normalize a missing state name. Supporting evidence includes a valid `+91` phone, `.in` domain, INR storefront, or India shipping/returns language.

Acceptance requires at least one strong signal and one independent supporting signal. State names are normalized across all states and union territories; GSTIN prefixes can provide the state. Registered-office evidence outranks ordinary contact, manufacturing, and footer addresses. Shipping lists never determine state. Equally strong conflicting states reject the row for manual review instead of guessing.

### 5. Extract requested fields

The final CSV has exactly seven columns:

| Column | Rule |
|---|---|
| `domain_url` | Final live storefront origin after redirects |
| `contacts` | JSON with public business mailto/tel links, Contact/footer/legal-contact details and WhatsApp numbers; masked and third-party developer/registrar contacts are rejected |
| `socials` | JSON with canonical Instagram, Facebook, X, LinkedIn and YouTube profile URLs; posts, reels, sharing links and incomplete profiles are rejected |
| `category` | Controlled category led by the store's own description, then product structured data, collections and navigation |
| `description` | Homepage meta/organization description, hero text or About introduction; Contact and policy boilerplate are rejected |
| `logo_url` | Organization structured-data or scored header logo using `src`, lazy-load and `srcset`; favicon, product, payment and logo-wall assets are rejected |
| `state` | GSTIN, structured/registered address, context-sized address evidence or conservative PIN-derived state |

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
$env:SHOPIFY_INDIA_USER_AGENT = "RivyouShopifyResearch/0.2 (+https://github.com/Yash07-pixel/indian-shopify-store-discovery)"
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

If a manual audit reveals systematic field-extraction errors, repair the rules and re-extract without repeating Shopify DNS/cart verification:

```powershell
# Test only rows already reviewed in the worksheet
python -m shopify_india reextract --audit-file reports\manual_audit.csv --only-reviewed

# Apply the current extractor version to every accepted record
python -m shopify_india reextract

# Recheck only rows whose sole strong India proof is registered-office text
python -m shopify_india reextract --only-registered-india
```

`reextract` fetches a fresh homepage, reuses cached secondary pages, and only probes missing Contact/About/Privacy page roles. It writes an extraction-version checkpoint after each successful record. If interrupted, run the same command again; completed rows are skipped and transient errors are retried. Use `--force` only when intentionally repeating the current extractor version.

To add independently sourced stores after the first run, import the public
master list and process only candidates attributed to that source. For example,
the following grows a 1,171-domain result to 1,550 unique final domains:

```powershell
python -m shopify_india discover
python -m shopify_india run --source shopify_master_list_indian_tld --unique-target 1550 --skip-discovery
```

`run` resumes pending/retry rows from `data/pipeline.sqlite3`; stopping it does not discard completed work. Use `python -m shopify_india status` to see progress. `--skip-master` avoids the large master-list download during a quick smoke test.

## Outputs

- `data/results/indian_shopify_stores.csv` — submission file
- `data/results/audit_evidence.jsonl` — row-level proof and provenance
- `reports/quality_report.json` — counts, rejection reasons, missingness, distributions and source yield
- `reports/manual_audit.csv` — reproducible human-review worksheet
- `reports/manual_audit_v2.csv` — post-repair 100-row worksheet; this separate file preserves the first audit attempt

The report discloses missing values rather than inventing them. Many legitimate stores do not publish every phone, social profile, description, logo or unambiguous state. The audit worksheet records Shopify and India correctness separately and includes per-field checks for contacts, socials, category, description, logo and state.

### Current output checks

- 1,251 CSV rows and 1,251 matching JSONL evidence records
- 1,251 unique canonical domains; zero export duplicates
- zero malformed `contacts` or `socials` JSON values
- zero favicon, apple-touch, sprite, or tracking URLs accepted as logos
- 28 automated tests passing
- missing contacts: 9 (0.72%)
- missing socials: 143 (11.43%)
- missing category: 0 (0.00%)
- missing description: 10 (0.80%)
- missing logo: 46 (3.68%)
- missing state: 16 (1.28%)

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

The software records timestamps and pipeline counts, but actual wall-clock runtime depends on network conditions, robots rules and retries. The context-aware refresh ran across resumable sessions from `2026-10-04T02:06:44Z` to `2026-10-04T08:15:19Z`, an observed elapsed span of **6 hours 8 minutes 35 seconds**, including staged validation, intentional restarts, and retry diagnosis.

- Development and validation time: **submitter must record actual hands-on time before submission; it is not inferred from file timestamps**
- Candidate crawl runtime: **run over multiple resumable sessions; record the submitter's observed total before submission**
- Context-aware extraction refresh: **6h 08m 35s elapsed**
- Manual audit precision: **pending review of `reports/manual_audit_v2.csv`**

## License

The code is MIT licensed. Third-party seed lists retain their original terms and are not committed to this repository. The generated dataset is a dated compilation of public business information; verify current site terms and applicable law before reuse.


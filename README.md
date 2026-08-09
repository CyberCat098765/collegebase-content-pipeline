# CollegeBase Content Pipeline

Python backend MVP for collecting college admissions content, cleaning it, chunking it, tagging it, and exporting citation-ready JSON plus simple content briefs.

This is a data ingestion and research pipeline for CollegeBase. It is not a website, chatbot, LangChain app, vector database, or frontend.

## What Works Now

- Article/blog ingestion is proven in the live smoke test.
- Article extraction uses `trafilatura` first, with `readability-lxml` and BeautifulSoup fallbacks.
- The pipeline keeps only admissions-process content. Chunks below the admissions relevance threshold are dropped before export.
- Each item and chunk includes `admissions_relevance_score`, `admissions_topics`, and `drop_reason`.
- Live YouTube transcript collection worked in the latest smoke test.
- Manual YouTube/podcast transcript files are supported as a reliable fallback for `.txt`, `.vtt`, and `.srt`.
- Focused `r/ApplyingToCollege` filtering, scoring, deduplication, ranking, and review output are proven through deterministic offline tests.
- Reddit acquisition is provider-based: authorized PRAW for production candidates, plus bounded curated A2C and Atom sources for zero-cost development/calibration. The curated masterpost was live-verified; broader live collection was rate limited in the latest run.
- Controlled runs work: source jobs, dry-run, run budgets, checkpoints, source registry caching, resume, run summaries, and JSON output.
- Processing/export works: text cleaning, chunking, topic tags, audience tags, content-use labels, usefulness scores, and citation metadata.
- Content briefs are generated with simple rule-based grouping. No OpenAI API or paid API is used.

## Admissions Scope

This project intentionally keeps CollegeBase admissions-process content only. It keeps material about college admissions strategy, essays, supplemental essays, Common App, activities lists, recommendation letters, financial aid, FAFSA, scholarships, early decision, early action, regular decision, application timelines, interviews, demonstrated interest, college lists, acceptance rates, Common Data Set, College Scorecard, admissions mistakes, and admissions-related advice for first-gen, low-income, transfer, international, or underrepresented applicants.

It drops material about general high school life, random teen lifestyle content, dorm life after admission, parties, social life, generic career advice, unrelated education news, politics without a direct admissions-policy angle, generic productivity advice, rankings articles without application-process value, and promotional pages without useful admissions content.

## Current Limitations

- Live YouTube transcript collection depends on transcript availability and request access. It worked in the latest local smoke test, but it can still fail or be blocked from other networks.
- Manual transcript files are the reliable YouTube/podcast fallback.
- Authorized Reddit discovery requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT`. PRAW is not live-verified in this environment, and Reddit may require a separate agreement for commercial use.
- Current unauthenticated Reddit JSON routes return `403`. Atom routes have returned both `200` and `429`; they are bounded development/calibration fallbacks, not a production authorization substitute.
- College Scorecard support is intentionally small and requires `COLLEGE_SCORECARD_API_KEY` when enabled.
- Common Data Set parsing is not included yet.
- Rule-based admissions relevance, tags, and briefs are heuristic, not model-generated analysis.

## Setup

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Safe Empty Run

The safe sample config has empty source lists and should run without network access:

```bash
python -m src.main --config examples/sample_sources.yaml --out data/processed/output.json
```

## Validate Config

```bash
python -m src.main --config examples/live_test_sources.yaml --validate-only
```

This checks config shape, source-job counts, run budget settings, obvious invalid source entries, and credential-gated collectors such as Reddit.

## Dry Run

Dry-run shows the source queue without scraping or writing output files:

```bash
python -m src.main --config examples/live_test_sources.yaml --dry-run
```

It reports how many source jobs would be created, which collectors would run, and which jobs would be skipped because credentials are missing.

## Live Smoke Test

The live config contains:

- 6 real admissions-related article/blog URLs from multiple domains.
- 2 real YouTube admissions videos.
- 1 original safe manual transcript file.
- 1 small Reddit search that only runs when PRAW credentials are available.

Run the live pipeline and generate content briefs:

```bash
python -m src.main --config examples/live_test_sources.yaml --out data/processed/live_test_output.json --briefs data/processed/content_briefs.json
```

Latest local live result:

- 10 sources attempted.
- 9 sources succeeded.
- 6 real article sources worked.
- 2 live YouTube transcript sources worked through `youtube-transcript-api`.
- 1 manual transcript file worked.
- 114 admissions-relevant chunks kept.
- 0 chunks dropped in this run because the configured sources were admissions-focused.
- 10 content briefs generated.
- Reddit skipped cleanly because credentials were missing.

Generated files under `data/processed/` are ignored by git. `examples/live_sample_output.json` is a trimmed preview-only sample, not a full scraped dataset.

## Resume A Run

Normal runs create checkpoint files under ignored `data/runtime/`:

```bash
data/runtime/jobs.json
data/runtime/checkpoint.json
data/runtime/source_registry.json
```

Resume without reprocessing successful jobs:

```bash
python -m src.main --config examples/live_test_sources.yaml --out data/processed/live_test_output.json --briefs data/processed/content_briefs.json --resume
```

Use `--force` with `--resume` to reprocess all jobs.

## Source Registry Cache

Successful sources are recorded in ignored `data/runtime/source_registry.json`. On later non-force runs, sources that already succeeded are skipped and marked in `run_summary.json` under `sources_skipped_from_cache`.

Use `--force` to reprocess cached successful sources:

```bash
python -m src.main --config examples/live_test_sources.yaml --out data/processed/live_test_output.json --briefs data/processed/content_briefs.json --force
```

This keeps repeat runs from scraping the same successful article, YouTube, transcript, Reddit, or official source every time.

## Manual Transcript Files

Use manual transcripts when YouTube transcripts are blocked or unavailable:

```yaml
sources:
  youtube:
    videos:
      - url: "https://www.youtube.com/watch?v=..."
    transcript_files:
      - path: "transcripts/sample_transcript.vtt"
        source_url: "https://example.com/original-video"
        title: "Admissions Planning Podcast"
        channel: "CollegeBase"
```

Supported formats:

- `.txt`
- `.vtt`
- `.srt`

VTT and SRT timestamps are preserved in chunk citation metadata when possible.

## Focused Reddit Discovery

The Reddit path is intentionally limited to `r/ApplyingToCollege`. All providers feed one normalizer and one deterministic quality pipeline; provider origin, keyword match, or upvote count never bypasses content evaluation.

The processing sequence is:

```text
candidate acquisition
-> normalization
-> structural and junk filters
-> usefulness scoring and topics
-> exact and near deduplication
-> ranking
-> machine output and human review
```

Provider behavior is evidence-based and deliberately bounded:

| Provider | Credentials | Cost | Current evidence | Data available | Intended use |
| --- | --- | --- | --- | --- | --- |
| `public-json` | None | $0 | Live-tested `403` on top, new, and search routes | None in the current environment | Explicit diagnostic/development only |
| `curated` | None | $0 | Masterpost Atom feed live-tested `200`; 100 unique A2C links found; linked post fetches then hit `429` | Curated URLs and post text when detail feeds allow it | Development/calibration seed source |
| `rss` | None | $0 | Listing feeds returned `200` during the initial probe and `429` in the latest capability/smoke run | Post title/body/author when available; no score, ratio, or comment count | Bounded development/calibration fallback |
| `praw` | Three `REDDIT_*` values | $0 API cost; approval dependent | Offline/integration-tested; not live-verified here because credentials are absent | Authorized listings/search, post metadata/text, optional comments | Preferred production candidate after CollegeBase confirms authorization |
| `import` | None | $0 | Offline-tested | Supplied JSON/JSONL records | Deterministic testing and fallback |
| `manual` | None | $0 | Offline-tested; live retrieval depends on Atom access | Explicit A2C post URLs | Small curated seed lists |

`--provider auto` is deterministic. It uses PRAW when all three authorized credentials are configured. Otherwise it records PRAW as skipped and runs `curated` followed by `rss`. It does not silently use public JSON. A configured but invalid PRAW integration fails closed instead of silently switching a production-intended run to a development provider.

The default development/calibration cost is `$0`. No paid provider or LLM is required; usefulness ranking is deterministic.

### Offline Proof

The checked-in fixture contains original synthetic examples of strong guides, weak posts, removed content, and duplicate observations. It is not scraped Reddit data. Validate it without network access:

```bash
python scripts/discover_reddit_resources.py --input-json tests/fixtures/reddit_candidates.json --validate-only
```

Run the complete offline pipeline:

```bash
python scripts/discover_reddit_resources.py --input-json tests/fixtures/reddit_candidates.json --no-llm --force --output-dir outputs/reddit_offline_smoke
```

Verified offline result:

- 18 candidates imported.
- 3 accepted resources and 3 useful human-review candidates.
- 2 structurally invalid posts and 8 obvious junk posts rejected.
- 1 exact duplicate and 1 near duplicate identified.
- 0 acquisition or processing errors.

The accepted sample covers essays, financial aid, and waitlist/deferral guidance. The review queue preserves borderline but potentially useful application-planning, college-list, and activities guidance instead of silently accepting it.

### Live Reddit Access

Run the cheap capability matrix first. It makes five bounded public requests plus an OAuth check only when credentials exist:

```bash
python scripts/check_reddit_access.py
```

Run a bounded zero-cost development/calibration sample:

```bash
python scripts/discover_reddit_resources.py --provider auto --subreddit ApplyingToCollege --quick --candidate-limit 100 --accepted-limit 25 --no-llm --output-dir outputs/reddit_live_smoke --verbose
```

The latest live run made 5 HTTP requests, obtained 1 real candidate from the curated masterpost feed, accepted 0, and initially rejected 1. Detail and listing Atom requests remained rate limited after one bounded retry. A no-network replay after a targeted calibration change placed that real masterpost in human review. This is useful access evidence, but it is not enough data to claim broad live calibration.

Reddit's current Data API requires OAuth, a descriptive user agent, and authorized access. Eligible free use is rate limited, deleted content must be removed, and Reddit states that commercial use may require a separate agreement. Review the current [Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Developer Interfaces guidance](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Reddit-Developer-Interfaces), [Data API Terms](https://redditinc.com/policies/data-api-terms), and [Developer Terms](https://redditinc.com/policies/developer-terms).

For authorized PRAW access:

1. Request or register Data API access for CollegeBase according to Reddit's current process.
2. Put the approved client values in a local `.env`; keep `.env.example` blank:

```text
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
```

3. Use a descriptive user agent associated with the approved integration. Do not add a Reddit password, session cookie, or user token.
4. Verify read-only access:

```bash
python scripts/check_reddit_auth.py
```

5. Run a bounded authorized sample:

```bash
python scripts/discover_reddit_resources.py --provider praw --subreddit ApplyingToCollege --quick --candidate-limit 100 --accepted-limit 25 --no-llm --output-dir outputs/reddit_praw_smoke --verbose
```

Comments are disabled by default. Add `--include-comments --max-comments-per-post 15` only after the post-level filters are working with the approved account.

Maximum-range mode is explicit and still bounded:

```bash
python scripts/discover_reddit_resources.py --provider praw --subreddit ApplyingToCollege --max-range --candidate-limit 500 --include-comments --max-comments-per-post 15 --minimum-usefulness-score 70 --resume --output-dir outputs/reddit_applyingtocollege --verbose
```

For a small manually curated URL list:

```bash
python scripts/discover_reddit_resources.py --provider manual --reddit-url "https://www.reddit.com/r/ApplyingToCollege/comments/POST_ID/POST_SLUG/" --candidate-limit 10 --no-llm --output-dir outputs/reddit_manual
```

Manual URLs still depend on bounded Atom access. They do not bypass a `403` or persistent `429`.

### Safety and Retention

- Requests use an honest descriptive user agent, a hard cap, short timeouts, and at most one bounded retry.
- `403` stops that provider. `429` honors a reasonable `Retry-After` and then stops if throttling persists. No proxies, cookie reuse, CAPTCHA bypass, or identity rotation are used.
- Successful HTTP responses are cached for at most 24 hours. Expired/corrupt cache files are pruned, and only two output-history generations are retained.
- Generated Reddit content remains under ignored `outputs/`. Delete an output immediately if its source post is removed; production use needs a routine deletion-sync process.
- Technical reachability is not commercial or production authorization. CollegeBase must confirm its intended use with Reddit.

### Reddit Outputs

Each completed run writes these eight bundle files:

- `raw_candidates.jsonl`: normalized candidates and acquisition provenance.
- `accepted_resources.json`: ranked CollegeBase-ready records with full cleaned text and Reddit citation URLs.
- `human_review.json`: borderline or cautionary resources.
- `rejected_candidates.jsonl`: rejected posts with reason codes.
- `duplicate_clusters.json`: retained representatives, rejected members, and similarity evidence.
- `source_registry.json`: content hashes and prior processing outcomes.
- `run_summary.json`: acquisition, filtering, duplicate, scoring, cache, and error counts.
- `review_report.md`: highest-ranked resources plus rejection and error samples for manual quality review.

These are research outputs derived from community posts, not official admissions guidance. Generated data remains under ignored `outputs/`. Material engineering choices are recorded in [docs/reddit_discovery_decisions.md](docs/reddit_discovery_decisions.md).

Another admissions subreddit is not enabled by a CLI string alone. Add it to the source registry, confirm its rules and access model, add provider fixtures, and update the scope validation before enabling it.

## College Scorecard Key

College Scorecard collection is disabled in the live smoke-test config. If it is enabled, set the API key locally outside git:

Windows:

```bash
set COLLEGE_SCORECARD_API_KEY=your_scorecard_api_key
```

macOS/Linux:

```bash
export COLLEGE_SCORECARD_API_KEY=your_scorecard_api_key
```

If the key is missing when Scorecard is enabled, the collector skips cleanly and records a non-fatal error.

## Output Files

Main pipeline output:

```bash
data/processed/live_test_output.json
```

Includes:

- `run_summary`
- collected items
- source metadata
- chunks
- citation labels
- source URLs
- `admissions_relevance_score`
- `admissions_topics`
- `drop_reason`
- topic and audience tags
- non-fatal errors

Run summary output:

```bash
data/processed/run_summary.json
```

Includes `started_at`, `finished_at`, `runtime_seconds`, source status counts, chunks kept/dropped, `stop_reason`, errors, and source-job statuses.

Content brief output:

```bash
data/processed/content_briefs.json
```

Each brief groups useful chunks into a possible CollegeBase content idea with source citations and short previews.

Committed examples:

- `examples/sample_output.json`: safe empty output shape.
- `examples/live_sample_output.json`: preview-only live sample with short text previews.
- `examples/transcripts/sample_transcript.vtt`: original safe transcript fixture.

## Tests

```bash
python -m pytest
python -m compileall src
```

Tests cover cleaning, chunking, tagging, admissions relevance filtering, source dropping when most chunks are irrelevant, article fallback behavior, transcript parsing for TXT/VTT/SRT, Reddit authentication failures, API normalization, offline import, junk filtering, scoring, deduplication, ranking, bounded retries, checkpoint/resume behavior, output schemas, content brief generation, config loading, source registry caching, and JSON schema basics.

## Future Improvements

- Add Common Data Set parsing for official admissions data.
- Add source caching for repeatable live tests.
- Add mocked HTTP integration tests for article and YouTube collectors.
- Add richer rule-based brief templates for admissions topics.
- Add optional authenticated YouTube/caption workflows if CollegeBase needs more reliable video ingestion.
- Run a bounded authenticated Reddit quality sample after CollegeBase receives approved API access, then calibrate heuristics against the human review set.

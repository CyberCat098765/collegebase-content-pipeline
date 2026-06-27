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
- Reddit uses authenticated PRAW access and skips cleanly when credentials are missing.
- Controlled runs work: source jobs, dry-run, run budgets, checkpoints, source registry caching, resume, run summaries, and JSON output.
- Processing/export works: text cleaning, chunking, topic tags, audience tags, content-use labels, usefulness scores, and citation metadata.
- Content briefs are generated with simple rule-based grouping. No OpenAI API or paid API is used.

## Admissions Scope

This project intentionally keeps CollegeBase admissions-process content only. It keeps material about college admissions strategy, essays, supplemental essays, Common App, activities lists, recommendation letters, financial aid, FAFSA, scholarships, early decision, early action, regular decision, application timelines, interviews, demonstrated interest, college lists, acceptance rates, Common Data Set, College Scorecard, admissions mistakes, and admissions-related advice for first-gen, low-income, transfer, international, or underrepresented applicants.

It drops material about general high school life, random teen lifestyle content, dorm life after admission, parties, social life, generic career advice, unrelated education news, politics without a direct admissions-policy angle, generic productivity advice, rankings articles without application-process value, and promotional pages without useful admissions content.

## Current Limitations

- Live YouTube transcript collection depends on transcript availability and request access. It worked in the latest local smoke test, but it can still fail or be blocked from other networks.
- Manual transcript files are the reliable YouTube/podcast fallback.
- Reddit requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT`.
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

## Reddit Credentials

Create a local `.env` or set environment variables outside git:

Windows:

```bash
set REDDIT_CLIENT_ID=your_reddit_client_id
set REDDIT_CLIENT_SECRET=your_reddit_client_secret
set REDDIT_USER_AGENT=collegebase-content-pipeline/0.1.0
```

macOS/Linux:

```bash
export REDDIT_CLIENT_ID=your_reddit_client_id
export REDDIT_CLIENT_SECRET=your_reddit_client_secret
export REDDIT_USER_AGENT=collegebase-content-pipeline/0.1.0
```

If these are missing, Reddit collection returns a clean error and the rest of the pipeline continues.

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

Tests cover cleaning, chunking, tagging, admissions relevance filtering, source dropping when most chunks are irrelevant, article fallback behavior, transcript parsing for TXT/VTT/SRT, Reddit missing credentials, mocked Reddit output schema, content brief generation, config loading, dry-run behavior, checkpoint/resume behavior, source registry caching, and JSON schema basics.

## Future Improvements

- Add Common Data Set parsing for official admissions data.
- Add source caching for repeatable live tests.
- Add mocked HTTP integration tests for article and YouTube collectors.
- Add richer rule-based brief templates for admissions topics.
- Add optional authenticated YouTube/caption workflows if CollegeBase needs more reliable video ingestion.

# Reddit Discovery Decisions

Status reviewed: 2026-08-09

## Acquisition Providers

**Decision:** Keep PRAW as the authorized production candidate and add bounded curated A2C, Atom, public JSON diagnostic, import, and manual-URL providers behind one normalized pipeline.

**Evidence:** Reddit's current documentation requires OAuth for Data API traffic. Live probes returned `403` for unauthenticated top, new, and search JSON; the organized A2C masterpost Atom feed returned `200` with 100 unique linked posts; listing Atom routes returned `200` during the initial probe and later `429`; PRAW credentials were absent.

**Alternatives:** PRAW-only acquisition left development blocked. HTML scraping, proxy rotation, cookie reuse, and paid data providers were rejected. External search was not needed as a pipeline provider.

**Choice:** `--provider auto` uses PRAW when credentials are complete. Without credentials it records PRAW as skipped and runs curated A2C then bounded Atom. Public JSON is explicit diagnostic/development behavior and is not in auto after the live `403` result.

**Fallback:** JSON/JSONL import and manual A2C URL seeds use the same downstream processor. A configured but invalid PRAW setup fails closed.

Official references:

- https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- https://support.reddithelp.com/hc/en-us/articles/14945211791892-Reddit-Developer-Interfaces
- https://redditinc.com/policies/data-api-terms
- https://redditinc.com/policies/developer-terms
- https://praw.readthedocs.io/en/latest/getting_started/authentication.html

## Acquisition Bounds

**Decision:** Use a shared HTTP client with a hard request cap, short timeout, one bounded retry, and a 24-hour successful-response cache.

**Evidence:** Reddit access changed from successful Atom responses to persistent `429` during the same engineering run. Repeating the same request path would not improve evidence and could increase throttling.

**Alternatives:** Unbounded pagination, long retry loops, and proxy/identity rotation were rejected.

**Choice:** `403` stops a provider. `429` uses a reasonable `Retry-After` when present and then stops. `5xx` and network failures receive at most one retry. Search Atom is not a default route after repeated `429` and no demonstrated value beyond curated/top/new inputs.

**Fallback:** Reuse a successful cache entry, process a supplied export offline, or wait for authorized PRAW access.

## Usefulness and Calibration

**Decision:** Preserve deterministic filtering/scoring and add only a narrow human-review override for relevant curated resource/masterpost records scoring 50-59.

**Evidence:** The one real post body obtained was the organized A2C masterpost. It scored 56 and was rejected even though its curated directory role makes review appropriate. It is old and potentially stale, so automatic acceptance would be unjustified.

**Alternatives:** Raising every score threshold, accepting all curated posts, or adding an LLM would overfit or weaken inspection.

**Choice:** Relevant curated guides/resources/masterposts in the narrow band go to `CURATED_RESOURCE_REVIEW`. Normal junk, staleness, topic, dedupe, and score checks still apply.

**Fallback:** Human reviewers decide whether the directory is current enough to expose. Broader ranking calibration remains blocked until more real post bodies can be acquired legitimately.

## Duplicate Handling

**Decision:** Retain exact post ID, canonical Reddit URL, outbound URL, normalized-title, and TF-IDF body deduplication.

**Evidence:** Existing offline tests cover exact and near duplicates. New provider tests confirmed that the same post from curated and RSS providers merges into one candidate while preserving both discovery origins.

**Alternatives:** Provider-specific dedupe would duplicate quality logic and lose cross-provider evidence.

**Choice:** Providers only normalize candidates. The shared dedupe/ranking path chooses representatives and emits similarity diagnostics.

**Fallback:** Duplicate clusters remain inspectable in `duplicate_clusters.json`.

## Persistence and Deletion

**Decision:** Keep atomic output bundles while bounding cached Reddit content and run history.

**Evidence:** Reddit requires removed/deleted content to be deleted and recommends short retention. Unbounded output archives would conflict with data minimization.

**Alternatives:** A database or permanent Reddit archive is unnecessary for this MVP.

**Choice:** Successful HTTP cache entries expire after 24 hours, corrupt/expired entries are pruned, and only two output-history generations are retained. Generated content stays under ignored `outputs/`.

**Fallback:** CollegeBase still needs a routine deletion-sync process before production deployment; local output can be removed immediately when a source disappears.

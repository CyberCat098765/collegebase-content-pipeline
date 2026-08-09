from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.reddit_discovery.models import RedditCandidate
from src.reddit_discovery.title_index import title_similarity_edges


DUPLICATE_POST_ID = "DUPLICATE_POST_ID"
DUPLICATE_CANONICAL_URL = "DUPLICATE_CANONICAL_URL"
NEAR_DUPLICATE_LOWER_QUALITY = "NEAR_DUPLICATE_LOWER_QUALITY"
DEFAULT_TITLE_SIMILARITY_THRESHOLD = 0.90
DEFAULT_TEXT_SIMILARITY_THRESHOLD = 0.88
SPARSE_SIMILARITY_CHUNK_SIZE = 256
_GENERIC_TITLE_PREFIXES = {"guide", "megathread", "psa"}
_TRACKING_PARAMETERS = {
    "fbclid", "gclid", "ref", "ref_source", "source", "mc_cid", "mc_eid",
}


@dataclass(slots=True)
class DuplicateRecord:
    candidate: RedditCandidate
    reason_code: str
    duplicate_of_resource_id: str
    similarity: float | None = None
    title_similarity: float | None = None
    text_similarity: float | None = None

    @property
    def candidate_resource_id(self) -> str:
        return ensure_resource_id(self.candidate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_resource_id": self.candidate_resource_id,
            "reddit_post_id": self.candidate.reddit_post_id,
            "title": self.candidate.title,
            "canonical_url": self.candidate.canonical_url,
            "reason_code": self.reason_code,
            "duplicate_of_resource_id": self.duplicate_of_resource_id,
            "similarity": self.similarity,
            "title_similarity": self.title_similarity,
            "text_similarity": self.text_similarity,
            "discovered_by": list(self.candidate.discovered_by),
        }


@dataclass(frozen=True, slots=True)
class DuplicateCluster:
    cluster_id: str
    retained_resource_id: str
    retained_reddit_post_id: str
    member_resource_ids: tuple[str, ...]
    members: tuple[DuplicateRecord, ...]
    match_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "retained_resource_id": self.retained_resource_id,
            "retained_reddit_post_id": self.retained_reddit_post_id,
            "member_resource_ids": list(self.member_resource_ids),
            "members": [member.to_dict() for member in self.members],
            "match_reasons": list(self.match_reasons),
        }


@dataclass(slots=True)
class DedupeResult:
    retained: list[RedditCandidate]
    duplicates: list[DuplicateRecord]
    clusters: list[DuplicateCluster]

    @property
    def rejected_candidates(self) -> list[RedditCandidate]:
        return [record.candidate for record in self.duplicates]


@dataclass(frozen=True, slots=True)
class SimilarityEdge:
    left: int
    right: int
    title_similarity: float | None = None
    text_similarity: float | None = None

    @property
    def similarity(self) -> float:
        return max(self.title_similarity or 0.0, self.text_similarity or 0.0)

    @property
    def reasons(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.title_similarity is not None:
            values.append("normalized_title")
        if self.text_similarity is not None:
            values.append("tfidf_text")
        return tuple(values)


def deduplicate_candidates(
    candidates: Iterable[RedditCandidate],
    *,
    title_similarity_threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
    text_similarity_threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD,
) -> DedupeResult:
    values = list(candidates)
    for candidate in values:
        ensure_resource_id(candidate)

    by_id, id_records, id_clusters = _dedupe_exact(
        values, key=_post_id_key, reason_code=DUPLICATE_POST_ID
    )
    by_url, url_records, url_clusters = _dedupe_exact(
        by_id, key=_canonical_url_key, reason_code=DUPLICATE_CANONICAL_URL
    )
    by_external, external_records, external_clusters = _dedupe_exact(
        by_url, key=_external_url_key, reason_code=DUPLICATE_CANONICAL_URL
    )
    near = cluster_near_duplicates(
        by_external,
        title_similarity_threshold=title_similarity_threshold,
        text_similarity_threshold=text_similarity_threshold,
    )
    return DedupeResult(
        retained=near.retained,
        duplicates=[*id_records, *url_records, *external_records, *near.duplicates],
        clusters=[*id_clusters, *url_clusters, *external_clusters, *near.clusters],
    )


def cluster_near_duplicates(
    candidates: Iterable[RedditCandidate],
    *,
    title_similarity_threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
    text_similarity_threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD,
) -> DedupeResult:
    values = list(candidates)
    _validate_threshold(title_similarity_threshold, "title_similarity_threshold")
    _validate_threshold(text_similarity_threshold, "text_similarity_threshold")
    if len(values) < 2:
        return DedupeResult(values, [], [])

    normalized_titles = [normalize_title(candidate.title) for candidate in values]
    edge_map = {
        (left, right): SimilarityEdge(left, right, title_similarity=similarity)
        for left, right, similarity in title_similarity_edges(
            normalized_titles, title_similarity_threshold
        )
    }

    for left, right, similarity in tfidf_similarity_edges(values, text_similarity_threshold):
        previous = edge_map.get((left, right))
        edge_map[(left, right)] = SimilarityEdge(
            left,
            right,
            title_similarity=previous.title_similarity if previous else None,
            text_similarity=similarity,
        )
    if not edge_map:
        return DedupeResult(values, [], [])

    groups = _union_find_groups(len(values), edge_map.values())
    retained: list[RedditCandidate] = []
    records: list[DuplicateRecord] = []
    clusters: list[DuplicateCluster] = []
    for indices in groups:
        members = [values[index] for index in indices]
        if len(members) == 1:
            retained.append(members[0])
            continue
        representative = choose_representative(members)
        retained.append(representative)
        representative_id = ensure_resource_id(representative)
        cluster_edges = [
            edge for edge in edge_map.values() if edge.left in indices and edge.right in indices
        ]
        member_records: list[DuplicateRecord] = []
        for index in indices:
            candidate = values[index]
            if candidate is representative:
                continue
            adjacent = [edge for edge in cluster_edges if index in {edge.left, edge.right}]
            best = max(adjacent, key=lambda edge: edge.similarity, default=None)
            record = _mark_duplicate(
                candidate,
                NEAR_DUPLICATE_LOWER_QUALITY,
                representative_id,
                similarity=best.similarity if best else None,
                title_similarity=best.title_similarity if best else None,
                text_similarity=best.text_similarity if best else None,
            )
            records.append(record)
            member_records.append(record)
        clusters.append(
            _cluster(
                representative,
                member_records,
                {reason for edge in cluster_edges for reason in edge.reasons},
            )
        )
    return DedupeResult(retained, records, clusters)


def normalize_title(title: str) -> str:
    value = title.casefold()
    value = re.sub(
        r"\b(?:19|20)\d{2}(?:\s*[-/\u2013\u2014]\s*(?:(?:19|20)?\d{2}))?\b",
        " ",
        value,
    )
    tokens = re.sub(r"[^a-z0-9]+", " ", value).split()
    while tokens and tokens[0] in _GENERIC_TITLE_PREFIXES:
        tokens.pop(0)
    return " ".join(tokens)


def normalized_title_similarity(left: str, right: str, *, already_normalized: bool = False) -> float:
    first = left if already_normalized else normalize_title(left)
    second = right if already_normalized else normalize_title(right)
    if not first or not second:
        return 0.0
    return round(SequenceMatcher(None, first, second, autojunk=False).ratio(), 6)


def tfidf_similarity_edges(
    candidates: list[RedditCandidate], threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD
) -> list[tuple[int, int, float]]:
    _validate_threshold(threshold, "threshold")
    if len(candidates) < 2:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for Reddit near-duplicate detection.") from exc

    documents = [_dedupe_text(candidate) for candidate in candidates]
    try:
        matrix = TfidfVectorizer(
            strip_accents="unicode", lowercase=True, stop_words="english",
            ngram_range=(1, 2), norm="l2",
        ).fit_transform(documents)
    except ValueError:
        return []
    return _sparse_similarity_edges(matrix, threshold)


def _sparse_similarity_edges(matrix: Any, threshold: float) -> list[tuple[int, int, float]]:
    edges: list[tuple[int, int, float]] = []
    for start in range(0, matrix.shape[0], SPARSE_SIMILARITY_CHUNK_SIZE):
        stop = min(matrix.shape[0], start + SPARSE_SIMILARITY_CHUNK_SIZE)
        products = (matrix[start:stop] @ matrix.T).tocoo()
        for row, column, value in zip(products.row, products.col, products.data):
            left, right = start + int(row), int(column)
            similarity = float(value)
            if right > left and similarity + 1e-12 >= threshold:
                edges.append((left, right, round(similarity, 6)))
    return sorted(edges)




def choose_representative(candidates: Iterable[RedditCandidate]) -> RedditCandidate:
    values = list(candidates)
    if not values:
        raise ValueError("Cannot choose a representative from an empty collection.")
    time_sensitive = any(candidate.freshness_status != "durable" for candidate in values)

    def ranking(candidate: RedditCandidate) -> tuple[Any, ...]:
        created = float(candidate.created_utc or 0.0) if time_sensitive else 0.0
        return (
            -int((candidate.distinguished or "").casefold() == "moderator"),
            -int(candidate.stickied),
            -int(candidate.final_usefulness_score),
            -created,
            -int(candidate.content_depth),
            -_component_score(candidate, "engagement_signal"),
            ensure_resource_id(candidate),
        )

    return sorted(values, key=ranking)[0]


def ensure_resource_id(candidate: RedditCandidate) -> str:
    if candidate.resource_id:
        return candidate.resource_id
    if candidate.reddit_post_id:
        candidate.resource_id = f"reddit_{candidate.reddit_post_id}"
    else:
        seed = f"{candidate.canonical_url}\n{candidate.title}"
        candidate.resource_id = f"reddit_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"
    return candidate.resource_id


def normalize_canonical_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return value.strip().casefold().rstrip("/")
    host = parsed.hostname.casefold()
    port = parsed.port
    netloc = f"{host}:{port}" if port and port not in {80, 443} else host
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_PARAMETERS
    ]
    return urlunparse(("https", netloc, path, "", urlencode(sorted(query)), ""))


def _dedupe_exact(
    candidates: list[RedditCandidate], *, key: Any, reason_code: str
) -> tuple[list[RedditCandidate], list[DuplicateRecord], list[DuplicateCluster]]:
    groups: dict[str, list[RedditCandidate]] = {}
    unkeyed: list[RedditCandidate] = []
    for candidate in candidates:
        value = key(candidate)
        (groups.setdefault(value, []) if value else unkeyed).append(candidate)
    retained = list(unkeyed)
    records: list[DuplicateRecord] = []
    clusters: list[DuplicateCluster] = []
    for value in sorted(groups):
        members = groups[value]
        representative = choose_representative(members)
        if reason_code == DUPLICATE_POST_ID:
            origins = [origin for member in members for origin in member.discovered_by]
            representative.discovered_by = []
            representative.merge_discovered_by(origins)
        retained.append(representative)
        if len(members) == 1:
            continue
        representative_id = ensure_resource_id(representative)
        member_records = [
            _mark_duplicate(member, reason_code, representative_id, similarity=1.0)
            for member in members if member is not representative
        ]
        records.extend(member_records)
        clusters.append(_cluster(representative, member_records, {reason_code.casefold()}))
    return retained, records, clusters


def _mark_duplicate(
    candidate: RedditCandidate,
    reason_code: str,
    representative_id: str,
    *,
    similarity: float | None = None,
    title_similarity: float | None = None,
    text_similarity: float | None = None,
) -> DuplicateRecord:
    candidate.duplicate_of = representative_id
    candidate.rejection_reason = reason_code
    candidate.processing_status = "rejected"
    return DuplicateRecord(
        candidate, reason_code, representative_id, similarity, title_similarity, text_similarity
    )


def _cluster(
    representative: RedditCandidate,
    members: list[DuplicateRecord],
    reasons: set[str],
) -> DuplicateCluster:
    retained_id = ensure_resource_id(representative)
    member_ids = tuple(sorted({retained_id, *(member.candidate_resource_id for member in members)}))
    seed = "\n".join([*member_ids, *sorted(reasons)])
    return DuplicateCluster(
        cluster_id=f"duplicate_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}",
        retained_resource_id=retained_id,
        retained_reddit_post_id=representative.reddit_post_id,
        member_resource_ids=member_ids,
        members=tuple(members),
        match_reasons=tuple(sorted(reasons)),
    )


def _union_find_groups(size: int, edges: Iterable[SimilarityEdge]) -> list[list[int]]:
    parent = list(range(size))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for edge in edges:
        left, right = find(edge.left), find(edge.right)
        if left != right:
            parent[max(left, right)] = min(left, right)
    groups: dict[int, list[int]] = {}
    for index in range(size):
        groups.setdefault(find(index), []).append(index)
    return [groups[key] for key in sorted(groups)]


def _post_id_key(candidate: RedditCandidate) -> str:
    return candidate.reddit_post_id.strip().casefold()


def _canonical_url_key(candidate: RedditCandidate) -> str:
    return normalize_canonical_url(candidate.canonical_url or candidate.permalink)


def _external_url_key(candidate: RedditCandidate) -> str:
    return normalize_canonical_url(candidate.external_url or "")


def _dedupe_text(candidate: RedditCandidate) -> str:
    return f"{normalize_title(candidate.title)}\n{candidate.selftext.strip()}".strip()




def _component_score(candidate: RedditCandidate, name: str) -> int:
    value = candidate.score_breakdown.get(name, 0)
    if isinstance(value, dict):
        value = value.get("score", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _validate_threshold(value: float, name: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1.")

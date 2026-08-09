from __future__ import annotations

from difflib import SequenceMatcher


TITLE_EXHAUSTIVE_LIMIT = 100
TITLE_CDIST_CHUNK_SIZE = 256


def title_similarity_edges(
    normalized_titles: list[str], threshold: float
) -> list[tuple[int, int, float]]:
    if len(normalized_titles) <= TITLE_EXHAUSTIVE_LIMIT:
        pairs = (
            (left, right)
            for left in range(len(normalized_titles))
            for right in range(left + 1, len(normalized_titles))
        )
    else:
        pairs = blocked_title_pairs(normalized_titles, threshold)
    edges: list[tuple[int, int, float]] = []
    for left, right in pairs:
        if not _possible_ratio(normalized_titles[left], normalized_titles[right], threshold):
            continue
        similarity = _sequence_similarity(normalized_titles[left], normalized_titles[right])
        if similarity >= threshold:
            edges.append((left, right, similarity))
    return edges


def blocked_title_pairs(
    normalized_titles: list[str], threshold: float = 0.90
) -> list[tuple[int, int]]:
    """Return an exact spanning graph without retaining a quadratic edge set.

    RapidFuzz's normalized indel score is recall-safe here: SequenceMatcher's
    matching blocks form a common subsequence, so any qualifying exact score
    also reaches the same prefilter cutoff. SequenceMatcher remains the final
    decision metric below.
    """

    groups: dict[str, list[int]] = {}
    for index, title in enumerate(normalized_titles):
        if title:
            groups.setdefault(title, []).append(index)
    parent = list(range(len(normalized_titles)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> bool:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return False
        parent[max(left_root, right_root)] = min(left_root, right_root)
        return True

    pairs: set[tuple[int, int]] = set()
    for indices in groups.values():
        for index in indices[1:]:
            if union(indices[0], index):
                pairs.add((indices[0], index))
    unique_titles = list(groups)
    if len(unique_titles) < 2:
        return sorted(pairs)
    try:
        import numpy as np
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise RuntimeError("RapidFuzz is required for Reddit title deduplication.") from exc

    representatives = [groups[title][0] for title in unique_titles]
    components_remaining = len(unique_titles)
    cutoff = threshold * 100.0
    for start in range(0, len(unique_titles) - 1, TITLE_CDIST_CHUNK_SIZE):
        stop = min(len(unique_titles), start + TITLE_CDIST_CHUNK_SIZE)
        scores = process.cdist(
            unique_titles[start:stop],
            unique_titles[start + 1 :],
            scorer=fuzz.ratio,
            score_cutoff=cutoff,
            score_hint=cutoff,
            dtype=np.float32,
            workers=-1,
        )
        for row, column in zip(*scores.nonzero()):
            left, right = start + int(row), start + 1 + int(column)
            if right <= left:
                continue
            left_index, right_index = representatives[left], representatives[right]
            if find(left_index) == find(right_index):
                continue
            similarity = _sequence_similarity(unique_titles[left], unique_titles[right])
            if similarity >= threshold and union(left_index, right_index):
                pairs.add((left_index, right_index))
                components_remaining -= 1
                if components_remaining == 1:
                    break
        if components_remaining == 1:
            break
    return sorted(pairs)


def _sequence_similarity(left: str, right: str) -> float:
    return round(SequenceMatcher(None, left, right, autojunk=False).ratio(), 6)


def _possible_ratio(left: str, right: str, threshold: float) -> bool:
    if not left or not right:
        return False
    return (2 * min(len(left), len(right)) / (len(left) + len(right))) + 1e-12 >= threshold

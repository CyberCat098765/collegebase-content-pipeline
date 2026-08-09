from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


def build_review_report(
    run_summary: Mapping[str, Any],
    *,
    accepted_resources: Sequence[Mapping[str, Any]] = (),
    human_review: Sequence[Mapping[str, Any]] = (),
    rejected_candidates: Sequence[Mapping[str, Any]] = (),
    duplicate_clusters: Sequence[Mapping[str, Any]] = (),
    sample_limit: int = 10,
) -> str:
    lines = [
        '# Reddit Discovery Review Report', '', '## Run Summary', '',
        '- Candidates: {}'.format(run_summary.get('candidate_count', 0)),
        '- Accepted: {}'.format(run_summary.get('accepted_count', 0)),
        '- Human review: {}'.format(run_summary.get('human_review_count', 0)),
        '- Rejected: {}'.format(run_summary.get('rejected_count', 0)),
        '- Duplicate clusters: {}'.format(run_summary.get('duplicate_cluster_count', 0)),
        '- Errors: {}'.format(run_summary.get('error_count', 0)), '',
        'Reddit posts are community-authored resources, not official admissions guidance.',
    ]
    selected = sorted(
        accepted_resources,
        key=lambda item: (-_number(item.get('final_usefulness_score')), str(item.get('title', ''))),
    )[:sample_limit]
    _append_resource_table(lines, 'Highest-Ranked Selected Resources', selected, ranked=True)
    _append_resource_table(lines, 'Human Review Queue', human_review[:sample_limit])

    lines.extend(('', '## Rejection Reasons', ''))
    reasons = Counter(_reason(item) for item in rejected_candidates)
    if reasons:
        lines.extend(('| Reason | Count |', '| --- | ---: |'))
        lines.extend(f'| {_markdown(reason)} | {count} |' for reason, count in sorted(reasons.items()))
    else:
        lines.append('No rejected candidates.')

    lines.extend(('', '## Rejected Sample', ''))
    if rejected_candidates:
        lines.extend(('| Post | Reason |', '| --- | --- |'))
        for item in rejected_candidates[:sample_limit]:
            lines.append(f'| {_post_link(item)} | {_markdown(_reason(item))} |')
    else:
        lines.append('No rejected candidates.')

    lines.extend(('', '## Duplicate Clusters', ''))
    if duplicate_clusters:
        lines.extend(('| Cluster | Retained resource | Duplicate members |', '| --- | --- | ---: |'))
        for item in duplicate_clusters:
            lines.append(
                '| {} | '.format(_markdown(item.get('cluster_id', '')))
                + '{} | '.format(_markdown(item.get('retained_resource_id', '')))
                + f'{_duplicate_member_count(item)} |'
            )
    else:
        lines.append('No duplicate clusters.')

    lines.extend(('', '## Errors', ''))
    errors = run_summary.get('errors', [])
    if isinstance(errors, list) and errors:
        for error in errors:
            message = error.get('message', error) if isinstance(error, Mapping) else error
            lines.append(f'- {_markdown(message)}')
    else:
        lines.append('No collection or processing errors were recorded.')
    return '\n'.join(lines).rstrip() + '\n'


def _append_resource_table(
    lines: list[str],
    heading: str,
    resources: Sequence[Mapping[str, Any]],
    *,
    ranked: bool = False,
) -> None:
    lines.extend(('', f'## {heading}', ''))
    if not resources:
        lines.append('No resources in this section.')
        return
    first_column = 'Rank' if ranked else 'Status'
    lines.extend((
        f'| {first_column} | Score | Post | Topic | Reason |',
        '| ---: | ---: | --- | --- | --- |',
    ))
    for index, item in enumerate(resources, start=1):
        marker = str(index) if ranked else 'Review'
        reason = item.get('why_useful') if ranked else _reason(item)
        lines.append(
            '| {} | {} | '.format(marker, _markdown(item.get('final_usefulness_score', '')))
            + '{} | {} | '.format(_post_link(item), _markdown(item.get('primary_topic', 'other')))
            + f'{_markdown(reason)} |'
        )


def _post_link(item: Mapping[str, Any]) -> str:
    title = _markdown(item.get('title', item.get('reddit_post_id', 'Untitled')))
    url = str(item.get('permalink') or item.get('canonical_url', ''))
    return f'[{title}]({url})'


def _reason(item: Mapping[str, Any]) -> str:
    return str(
        item.get('rejection_reason')
        or item.get('hard_rejection_reason')
        or 'UNSPECIFIED'
    )


def _duplicate_member_count(cluster: Mapping[str, Any]) -> int:
    members = cluster.get('members', cluster.get('member_resource_ids', []))
    return len(members) if isinstance(members, list) else 0


def _markdown(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

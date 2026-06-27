from __future__ import annotations

import os
from typing import Any

import requests

from src.config import CollegeScorecardConfig
from src.models import PipelineError, SourceItem
from src.processing.cleaner import clean_text

SCORECARD_ENDPOINT = "https://api.data.gov/ed/collegescorecard/v1/schools"
DEFAULT_FIELDS = [
    "id",
    "school.name",
    "school.city",
    "school.state",
    "latest.admissions.admission_rate.overall",
    "latest.cost.attendance.academic_year",
    "latest.aid.median_debt.completers.overall",
    "latest.completion.completion_rate_4yr_150nt",
]


class CollegeScorecardCollector:
    def __init__(self, config: CollegeScorecardConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "collegebase-content-pipeline/0.1.0"})

    def collect(self, collected_at: str) -> tuple[list[SourceItem], list[PipelineError]]:
        if not self.config.enabled:
            return [], []

        api_key = os.getenv(self.config.api_key_env, "").strip()
        if not api_key:
            return [], [
                PipelineError(
                    source_type="official",
                    source="College Scorecard",
                    message=(
                        f"{self.config.api_key_env} is not set; "
                        "skipped College Scorecard collection."
                    ),
                )
            ]

        items: list[SourceItem] = []
        errors: list[PipelineError] = []

        for school in self.config.schools:
            item, error = self._collect_school(school, api_key, collected_at)
            if item:
                items.append(item)
            if error:
                errors.append(error)

        return items, errors

    def _collect_school(
        self, school: str, api_key: str, collected_at: str
    ) -> tuple[SourceItem | None, PipelineError | None]:
        params = {
            "api_key": api_key,
            "school.name": school,
            "fields": ",".join(DEFAULT_FIELDS),
            "per_page": 1,
        }
        try:
            response = self.session.get(
                SCORECARD_ENDPOINT,
                params=params,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            return None, PipelineError(
                "official",
                school,
                f"College Scorecard request failed: {type(exc).__name__}",
            )

        if response.status_code >= 400:
            return None, PipelineError(
                "official",
                school,
                f"College Scorecard HTTP {response.status_code}: {response.reason}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            return None, PipelineError("official", school, f"Invalid JSON response: {exc}")

        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return None, PipelineError("official", school, "No College Scorecard match found.")

        result = results[0]
        title = str(result.get("school.name") or school)
        raw_text = _scorecard_summary(result)
        school_id = str(result.get("id") or "")

        return (
            SourceItem(
                source_type="official",
                source_name="College Scorecard",
                title=title,
                url=_scorecard_school_url(school_id, title),
                author_or_channel="U.S. Department of Education",
                published_date="",
                collected_at=collected_at,
                raw_text=raw_text,
                metadata={"scorecard_fields": result, "school_id": school_id},
            ),
            None,
        )


def _scorecard_summary(result: dict[str, Any]) -> str:
    labels = {
        "school.name": "School",
        "school.city": "City",
        "school.state": "State",
        "latest.admissions.admission_rate.overall": "Admission rate",
        "latest.cost.attendance.academic_year": "Annual cost of attendance",
        "latest.aid.median_debt.completers.overall": "Median debt for completers",
        "latest.completion.completion_rate_4yr_150nt": "Four-year completion rate",
    }
    parts = [
        f"{label}: {result[key]}"
        for key, label in labels.items()
        if key in result and result[key] is not None
    ]
    return clean_text(". ".join(parts))


def _scorecard_school_url(school_id: str, school_name: str) -> str:
    if not school_id:
        return "https://collegescorecard.ed.gov/"
    slug = "-".join(part for part in school_name.lower().split() if part.isalnum())
    return f"https://collegescorecard.ed.gov/school/?{school_id}-{slug}"

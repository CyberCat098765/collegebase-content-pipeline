from src.models import Chunk, PipelineOutput, SourceItem
from src.output.content_briefs import build_content_briefs


def test_content_briefs_group_useful_chunks() -> None:
    item = SourceItem(
        source_type="blog",
        source_name="example.com",
        title="College Essay Guide",
        url="https://example.com/essay",
        author_or_channel="",
        published_date="",
        collected_at="2026-06-26T00:00:00Z",
        raw_text="College essays need evidence.",
        chunks=[
            Chunk(
                chunk_id="chunk_1",
                text="College essays need specific examples and reflection.",
                source_url="https://example.com/essay",
                citation_label="example.com: College Essay Guide",
                topic_tags=["college_essays"],
                audience=["senior"],
                content_use="both",
                usefulness_score=4,
                admissions_relevance_score=4,
                admissions_topics=["college_essays"],
            )
        ],
        admissions_relevance_score=4,
        admissions_topics=["college_essays"],
    )

    briefs = build_content_briefs(
        PipelineOutput(
            pipeline_version="0.1.0",
            collected_at="2026-06-26T00:00:00Z",
            items=[item],
        )
    )

    assert briefs["briefs"][0]["topic"] == "college essays"
    assert briefs["briefs"][0]["source_count"] == 1
    assert briefs["briefs"][0]["supporting_chunks"][0]["chunk_id"] == "chunk_1"

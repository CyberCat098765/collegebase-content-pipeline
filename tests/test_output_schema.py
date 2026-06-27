from src.models import Chunk, PipelineError, PipelineOutput, SourceItem


def test_pipeline_output_contains_summary_items_chunks_and_errors() -> None:
    output = PipelineOutput(
        pipeline_version="0.1.0",
        collected_at="2026-06-26T00:00:00Z",
        run_summary={"sources_attempted": 1},
        items=[
            SourceItem(
                source_type="blog",
                source_name="example.com",
                title="Essay Guide",
                url="https://example.com/essay",
                author_or_channel="",
                published_date="",
                collected_at="2026-06-26T00:00:00Z",
                raw_text="College essay guidance.",
                chunks=[
                    Chunk(
                        chunk_id="chunk_1",
                        text="College essay guidance.",
                        source_url="https://example.com/essay",
                        citation_label="example.com: Essay Guide",
                        admissions_relevance_score=4,
                        admissions_topics=["college_essays"],
                    )
                ],
                admissions_relevance_score=4,
                admissions_topics=["college_essays"],
            )
        ],
        errors=[PipelineError("youtube", "video", "Transcript unavailable")],
    )

    data = output.to_dict()

    assert data["run_summary"]["sources_attempted"] == 1
    assert data["items"][0]["admissions_relevance_score"] == 4
    assert data["items"][0]["chunks"][0]["admissions_topics"] == ["college_essays"]
    assert data["items"][0]["chunks"][0]["source_url"] == "https://example.com/essay"
    assert data["errors"][0]["message"] == "Transcript unavailable"

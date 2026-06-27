from src.processing.cleaner import clean_text, clean_title


def test_clean_text_removes_duplicate_whitespace() -> None:
    text = "  College&nbsp;&nbsp; essays  \n\n\n  need   specific examples. "

    assert clean_text(text) == "College essays\nneed specific examples."


def test_clean_title_returns_single_line() -> None:
    assert clean_title(" Financial aid \n guide ") == "Financial aid guide"

"""Tests for description word-cap helpers."""

from src.text_utils import (
    MAX_DESCRIPTION_WORDS,
    exceeds_word_limit,
    truncate_words,
    word_count,
)


class TestWordCount:
    def test_counts_words(self):
        assert word_count("one two three") == 3

    def test_empty(self):
        assert word_count("") == 0
        assert word_count(None) == 0  # type: ignore[arg-type]


class TestExceedsWordLimit:
    def test_under_limit_ok(self):
        assert not exceeds_word_limit("short description")

    def test_exactly_at_limit_ok(self):
        text = " ".join(["w"] * MAX_DESCRIPTION_WORDS)
        assert not exceeds_word_limit(text)

    def test_over_limit(self):
        text = " ".join(["w"] * (MAX_DESCRIPTION_WORDS + 1))
        assert exceeds_word_limit(text)


class TestTruncateWords:
    def test_under_limit_unchanged(self):
        assert truncate_words("a b c") == "a b c"

    def test_over_limit_trimmed_with_ellipsis(self):
        text = " ".join(str(i) for i in range(MAX_DESCRIPTION_WORDS + 10))
        out = truncate_words(text)
        assert word_count(out.replace("…", "")) == MAX_DESCRIPTION_WORDS
        assert out.endswith("…")

    def test_empty(self):
        assert truncate_words("") == ""

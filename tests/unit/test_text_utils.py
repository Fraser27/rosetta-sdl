"""Tests for description word-cap helpers."""

from src.text_utils import (
    MAX_DESCRIPTION_WORDS,
    exceeds_word_limit,
    strip_fulltext_stopwords,
    truncate_words,
    word_count,
)


class TestStripFulltextStopwords:
    def test_keeps_content_words(self):
        assert strip_fulltext_stopwords("what is the total revenue") == "total revenue"

    def test_stopword_only_question_is_empty(self):
        # The regression: "of the in a" scored 1.408 against return_rate purely
        # because "of" appears in its definition.
        assert strip_fulltext_stopwords("of the in a") == ""

    def test_drops_of_from_customer_count_question(self):
        assert strip_fulltext_stopwords("What are the number of customers ?") == "number customers"

    def test_strips_lucene_operators(self):
        # Quotes/operators are removed so a question is always a bag of content
        # words — "AND" is itself a stopword, so no operator survives to Lucene.
        assert strip_fulltext_stopwords('revenue AND "orders"') == "revenue orders"

    def test_lone_operators_do_not_survive(self):
        assert strip_fulltext_stopwords("+-!(){}[]^~*?:\\/") == ""

    def test_empty_and_none(self):
        assert strip_fulltext_stopwords("") == ""
        assert strip_fulltext_stopwords(None) == ""  # type: ignore[arg-type]

    def test_case_insensitive(self):
        assert strip_fulltext_stopwords("THE Revenue OF orders") == "Revenue orders"


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

"""Shared text helpers for metadata descriptions.

Descriptions are capped to keep them terse (they feed the LLM schema context
and search indexes, where long free text adds noise and token cost).

Also hosts robust LLM-output parsing (code-fence / JSON extraction) and a
Bedrock retry-with-backoff helper, shared by the query generator, metadata
enrichment, and embedding modules. Kept dependency-free beyond botocore
(which is already required transitively via boto3).
"""

from __future__ import annotations

import json
import logging
import random
import re
import time

logger = logging.getLogger(__name__)

# Maximum words allowed in a table/column/document description.
MAX_DESCRIPTION_WORDS = 50

# ── Full-text query sanitisation ─────────────────────────────
#
# The Neo4j full-text indexes are created with the default
# `standard-no-stop-words` analyzer, so stopwords are indexed as real terms.
# On a small metric corpus that is actively harmful: a question phrased in
# natural English ("what is the number OF customers?") can clear the Lucene
# confidence threshold purely because "of" also appears in an unrelated
# metric's definition, and IDF makes that term look discriminating. The
# governed metric then wins before the vector path is ever consulted.
#
# Stripping stopwords before searching keeps scores tied to content words.
FULLTEXT_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
        "did", "do", "does", "for", "from", "give", "had", "has", "have", "how",
        "i", "in", "into", "is", "it", "its", "many", "me", "much", "my", "no",
        "not", "of", "on", "or", "our", "s", "show", "so", "some", "such",
        "t", "tell", "than", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "to", "us", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with", "would", "you",
        "your",
    }
)

# Lucene syntax characters that would otherwise be parsed as operators.
_LUCENE_SPECIAL_RE = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def strip_fulltext_stopwords(text: str) -> str:
    """Reduce a natural-language question to its content words for full-text search.

    Returns an empty string when nothing but stopwords remain — callers MUST treat
    that as "no match" rather than passing it to Lucene, since an empty or
    operator-only query is both meaningless and a syntax error.
    """
    cleaned = _LUCENE_SPECIAL_RE.sub(" ", text or "")
    kept = [w for w in cleaned.split() if w.lower() not in FULLTEXT_STOPWORDS]
    return " ".join(kept)


def word_count(text: str) -> int:
    return len((text or "").split())


def exceeds_word_limit(text: str, limit: int = MAX_DESCRIPTION_WORDS) -> bool:
    return word_count(text) > limit


def truncate_words(text: str, limit: int = MAX_DESCRIPTION_WORDS) -> str:
    """Trim to `limit` words, appending an ellipsis when trimming occurred.

    Used for machine-generated (LLM) descriptions, which we cap silently rather
    than reject so a chatty model never blocks an enrichment job.
    """
    words = (text or "").split()
    if len(words) <= limit:
        return text or ""
    return " ".join(words[:limit]) + "…"


# ── LLM output parsing ───────────────────────────────────────
#
# LLM responses vary: sometimes a fenced ```sql / ```json block, sometimes a
# generic ``` block, sometimes bare text with no fences at all, and often with
# explanatory prose wrapped around the payload. The extractors below tolerate
# all of these so a format drift never breaks the pipeline.

# Matches a fenced code block, optionally tagged (```sql, ```json, ``` ...).
# Capture group 1 is the optional language tag; group 2 is the block body.
_FENCE_RE = re.compile(
    r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```",
    re.DOTALL,
)


def _extract_fenced_block(text: str, preferred_tag: str | None = None) -> str | None:
    """Return the body of a fenced code block, or None if there are no fences.

    If ``preferred_tag`` is given (e.g. "sql", "json"), a block tagged with it
    wins; otherwise the first fenced block is used.
    """
    matches = _FENCE_RE.findall(text or "")
    if not matches:
        return None
    if preferred_tag:
        for tag, body in matches:
            if tag.lower() == preferred_tag.lower():
                return body.strip()
    # Fall back to the first block regardless of tag.
    return matches[0][1].strip()


def extract_sql(text: str) -> str:
    """Extract a SQL statement from an LLM response.

    Handles ```sql fences, generic ``` fences, and bare (unfenced) SQL. Always
    returns a stripped string (never None); empty input yields "".
    """
    if not text:
        return ""
    block = _extract_fenced_block(text, preferred_tag="sql")
    if block is not None:
        return block
    return text.strip()


def _find_balanced_json(text: str) -> str | None:
    """Return the first balanced JSON object/array substring in ``text``.

    Scans for the first ``{`` or ``[`` and tracks matching brackets, ignoring
    braces that appear inside JSON string literals (with escape handling). This
    lets us pull a JSON payload out of surrounding prose.
    """
    if not text:
        return None
    open_to_close = {"{": "}", "[": "]"}
    start = None
    opener = None
    for i, ch in enumerate(text):
        if ch in open_to_close:
            start = i
            opener = ch
            break
    if start is None:
        return None

    closer = open_to_close[opener]
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str):
    """Extract and parse a JSON object/array from an LLM response.

    Handles ```json fences, generic ``` fences, and bare JSON, tolerating
    leading/trailing prose around the payload. Raises ValueError if no valid
    JSON can be recovered.
    """
    if not text:
        raise ValueError("Cannot extract JSON from empty text")

    candidates: list[str] = []
    fenced = _extract_fenced_block(text, preferred_tag="json")
    if fenced is not None:
        candidates.append(fenced)
    candidates.append(text.strip())

    # Try direct parses first (fenced body, then whole text).
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass

    # Fall back to pulling the first balanced JSON object/array out of prose.
    for candidate in candidates:
        balanced = _find_balanced_json(candidate)
        if balanced is not None:
            try:
                return json.loads(balanced)
            except (ValueError, TypeError):
                continue

    raise ValueError("No valid JSON found in LLM response")


# ── Bedrock retry ────────────────────────────────────────────

# Error codes that indicate a transient Bedrock condition worth retrying.
RETRYABLE_BEDROCK_ERROR_CODES = frozenset({
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "InternalServerException",
})


def _is_retryable_bedrock_error(exc: Exception) -> bool:
    """True if ``exc`` is a transient Bedrock error we should retry on."""
    # Read timeouts (botocore.exceptions.ReadTimeoutError / ConnectTimeoutError)
    # surface as their own exception types; match by class name to avoid a hard
    # import dependency and to catch subclasses.
    if any(
        name in type(exc).__name__
        for name in ("ReadTimeoutError", "ConnectTimeoutError", "ConnectionError")
    ):
        return True
    try:
        from botocore.exceptions import ClientError
    except ImportError:  # pragma: no cover - botocore always present with boto3
        return False
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in RETRYABLE_BEDROCK_ERROR_CODES
    return False


def retry_bedrock(
    func,
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 20.0,
):
    """Call ``func()`` with exponential backoff on transient Bedrock errors.

    Retries on botocore ``ClientError`` whose code is in
    :data:`RETRYABLE_BEDROCK_ERROR_CODES` and on read/connect timeouts. Uses
    exponential backoff (``base_delay * 2**attempt``) with full jitter, capped
    at ``max_delay``. Non-retryable errors propagate immediately, and the last
    error is re-raised after ``max_attempts``.
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:
            attempt += 1
            if attempt >= max_attempts or not _is_retryable_bedrock_error(exc):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay = random.uniform(0, delay)
            logger.warning(
                "Bedrock call failed (attempt %d/%d): %s — retrying in %.2fs",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)

"""
Pure text-splitting helper for the chat pipeline (Fix round 2, item 7).
Kept separate from main.py so it can be unit tested directly, with no
FastAPI app, database, or AI provider involved.
"""
import re

# An explicit numbered or lettered list marker -- "1) ", "(1) ", "a) " --
# at the start of the question or right after whitespace, so it matches
# both a list written on separate lines and one written inline on a
# single line ("1) ... 2) ... 3) ..."). Deliberately requires a closing
# ")" rather than also accepting "1. " / "a. " -- a bare "N." shows up
# constantly in ordinary prose ("see section 2.", "version 3.") and would
# wrongly split those sentences into "parts"; a list marker written with a
# dot on its own line is still caught below by the per-line split instead.
_NUMBERED_PART_PATTERN = re.compile(r'(?:^|(?<=\s))\(?(?:\d{1,2}|[a-hA-H])\)\s+')

_LEADING_CONJUNCTION_PATTERN = re.compile(r'^(and|or)\s+', re.IGNORECASE)

# Bounds how many parts a single question is split into, so one very long
# pasted brief can't balloon into dozens of separate retrieval calls.
MAX_QUESTION_PARTS = 5


def split_question_parts(question: str) -> list[str]:
    """
    Splits a long, multi-part question into its individual parts, so the
    chat pipeline can retrieve sources for each part separately instead of
    treating the whole thing as one query (which tends to fail the
    relevance check even when every individual part has an answer in the
    notes -- see Fix round 2, item 7).

    Returns [question] unchanged whenever no clear multi-part structure is
    found, so an ordinary single-topic question -- including a plain "X
    and Y" phrasing -- is never split and behaves exactly as before.
    """
    if not question or not question.strip():
        return [question]

    # An explicit numbered/lettered list: "1) ... 2) ... 3) ...".
    numbered_parts = [p.strip() for p in _NUMBERED_PART_PATTERN.split(question) if p.strip()]
    if len(numbered_parts) >= 2:
        return numbered_parts[:MAX_QUESTION_PARTS]

    # Several non-empty lines -- a pasted assignment brief -- rather than
    # one long wrapped paragraph.
    lines = [line.strip() for line in question.split("\n") if line.strip()]
    if len(lines) >= 2:
        return lines[:MAX_QUESTION_PARTS]

    # A single sentence listing several distinct sub-topics separated by
    # semicolons.
    if question.count(";") >= 2:
        return [p.strip() for p in question.split(";") if p.strip()][:MAX_QUESTION_PARTS]

    # A single long sentence listing several comma-separated sub-topics,
    # e.g. "Explain firewalls, encryption, subnetting, and hardening." --
    # only commas are split on (never a bare "and", which is too common in
    # ordinary prose); a trailing Oxford-comma "and <last item>" just has
    # its leading conjunction stripped. Only kicks in once there are
    # several items AND the question is long enough that it isn't just an
    # ordinary two-item "X and Y" question.
    comma_parts = [
        _LEADING_CONJUNCTION_PATTERN.sub('', p.strip())
        for p in question.split(",") if p.strip()
    ]
    if len(comma_parts) >= 4 and len(question.split()) >= 15:
        return comma_parts[:MAX_QUESTION_PARTS]

    return [question]

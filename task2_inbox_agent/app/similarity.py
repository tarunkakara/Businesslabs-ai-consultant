"""Transparent Edit Distance and Similarity Measurement Rules.

Calculates Levenshtein distance, normalized similarity ratio, and word overlap
without relying on opaque heuristics.
"""

import re
from typing import Any, Dict


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute standard dynamic programming Levenshtein distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    len_s1, len_s2 = len(s1), len(s2)
    prev_row = list(range(len_s2 + 1))

    for i, c1 in enumerate(s1):
        curr_row = [i + 1] * (len_s2 + 1)
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row[j + 1] = min(insertions, deletions, substitutions)
        prev_row = curr_row

    return prev_row[len_s2]


def calculate_draft_similarity(
    original_draft: str,
    human_edited_draft: str,
    unchanged_threshold: float = 0.95,
) -> Dict[str, Any]:
    """Measure transparent similarity between original AI draft and human-edited version.

    Transparent Rules:
    1. Exact string match (ignoring leading/trailing whitespace) -> Score 1.0 (Unchanged).
    2. Normalized Levenshtein ratio: 1.0 - (edit_distance / max_len).
    3. If similarity ratio >= unchanged_threshold (default 0.95), the draft is classified as unchanged.
    4. Otherwise, it is classified as edited/changed, impacting autonomous approval streaks.
    """
    clean_orig = original_draft.strip()
    clean_edit = human_edited_draft.strip()

    if clean_orig == clean_edit:
        return {
            "similarity_score": 1.0,
            "is_unchanged": True,
            "edit_distance": 0,
            "added_words": 0,
            "removed_words": 0,
            "diff_summary": "No edits made (100% match).",
        }

    max_len = max(len(clean_orig), len(clean_edit))
    if max_len == 0:
        return {
            "similarity_score": 1.0,
            "is_unchanged": True,
            "edit_distance": 0,
            "added_words": 0,
            "removed_words": 0,
            "diff_summary": "Empty drafts matched.",
        }

    dist = levenshtein_distance(clean_orig, clean_edit)
    ratio = max(0.0, min(1.0, 1.0 - (dist / max_len)))

    orig_words = set(re.findall(r"\w+", clean_orig.lower()))
    edit_words = set(re.findall(r"\w+", clean_edit.lower()))

    added_words = len(edit_words - orig_words)
    removed_words = len(orig_words - edit_words)

    is_unchanged = ratio >= unchanged_threshold

    diff_summary = (
        f"Similarity: {ratio * 100:.1f}%. "
        f"Edit distance: {dist} chars. Added words: {added_words}, Removed: {removed_words}."
    )

    return {
        "similarity_score": round(ratio, 4),
        "is_unchanged": is_unchanged,
        "edit_distance": dist,
        "added_words": added_words,
        "removed_words": removed_words,
        "diff_summary": diff_summary,
    }

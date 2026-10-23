"""Continuous Learning Loop Engine for Task 2: Inbox Agent.

Captures diffs when human reviewers edit AI drafts, derives style rules,
persists them into SQLite, and injects learned patterns into subsequent drafts
within approximately five minutes.
"""

import difflib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from task2_inbox_agent.app.config import settings

logger = logging.getLogger("inbox_agent.learning")


class ContinuousLearningEngine:
    """Derives and injects email drafting guidelines based on human reviewer feedback."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create table for learned rules."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inbox_learned_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT,
                    original_draft TEXT,
                    user_edited_draft TEXT,
                    learned_instruction TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()

    def learn_from_edit(
        self,
        category: str,
        original_draft: str,
        user_edited_draft: str,
        reviewer_name: str = "Senior Consultant",
    ) -> Optional[Dict[str, Any]]:
        """Analyze differences between original AI draft and human revision, producing a learned rule."""
        if not original_draft or not user_edited_draft or original_draft.strip() == user_edited_draft.strip():
            return None

        orig_lines = [l.strip() for l in original_draft.splitlines() if l.strip()]
        edit_lines = [l.strip() for l in user_edited_draft.splitlines() if l.strip()]

        # Compute differences
        matcher = difflib.SequenceMatcher(None, original_draft, user_edited_draft)
        similarity = matcher.ratio()

        learned_points: List[str] = []

        # 1. Sign-off analysis
        if edit_lines and orig_lines:
            last_orig = orig_lines[-1]
            last_edit = edit_lines[-1]
            if last_orig.lower() != last_edit.lower():
                learned_points.append(f"Sign off with: '{last_edit}'")

        # 2. Greeting analysis
        if edit_lines and orig_lines:
            first_orig = orig_lines[0]
            first_edit = edit_lines[0]
            if first_orig.lower() != first_edit.lower():
                learned_points.append(f"Start email with: '{first_edit}'")

        # 3. Conciseness or expansion
        orig_words = len(original_draft.split())
        edit_words = len(user_edited_draft.split())
        if edit_words < orig_words * 0.7:
            learned_points.append("Keep response concise and avoid unnecessary fluff")
        elif edit_words > orig_words * 1.3:
            learned_points.append("Provide detailed explanation with clear next steps")

        # 4. Bullet points preference
        orig_bullets = original_draft.count("- ") + original_draft.count("* ")
        edit_bullets = user_edited_draft.count("- ") + user_edited_draft.count("* ")
        if edit_bullets > orig_bullets:
            learned_points.append("Use bullet points for instructions and multi-step solutions")

        if not learned_points:
            learned_points.append(f"Adopt preferred consultant tone as revised in recent {category} response")

        instruction = " • " + "; ".join(learned_points)
        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO inbox_learned_rules (category, original_draft, user_edited_draft, learned_instruction, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (category, original_draft, user_edited_draft, instruction, now))
            rule_id = cursor.lastrowid
            conn.commit()

        logger.info("Learned new drafting rule for category '%s': %s", category, instruction)
        return {
            "id": rule_id,
            "category": category,
            "learned_instruction": instruction,
            "similarity": round(similarity, 3),
            "created_at": now,
        }

    def get_learned_rules(self, category: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch active learned rules from reviewer edits."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if category:
                cursor.execute("""
                    SELECT id, category, learned_instruction, created_at
                    FROM inbox_learned_rules
                    WHERE category = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (category, limit))
            else:
                cursor.execute("""
                    SELECT id, category, learned_instruction, created_at
                    FROM inbox_learned_rules
                    ORDER BY id DESC
                    LIMIT ?
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def format_rules_for_prompt(self, category: str) -> str:
        """Format learned guidelines into prompt injection for real-time drafting improvement."""
        rules = self.get_learned_rules(category=category, limit=3)
        if not rules:
            return ""

        formatted_rules = "\n".join(f"- {r['learned_instruction']}" for r in rules)
        return (
            f"\n\n[HUMAN REVIEWER PREFERENCES LEARNED FROM PREVIOUS EDITS IN '{category}']:\n"
            f"{formatted_rules}\n"
            "Apply the above user preferences to this draft."
        )

"""Human Expert Feedback Store for Unanswered Questions & Approved QA."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExpertFeedbackStore:
    """Manages the human-in-the-loop workflow for reviewing unanswered questions and storing expert answers."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables for unanswered query queue and approved expert knowledge."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS unanswered_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT UNIQUE,
                    attempts_count INTEGER DEFAULT 1,
                    last_asked_at TEXT,
                    status TEXT DEFAULT 'pending'
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS expert_approved_qa (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT UNIQUE,
                    answer TEXT,
                    expert_name TEXT,
                    sources_json TEXT,
                    approved_at TEXT
                )
            """)
            conn.commit()

    def log_unanswered_question(self, question: str) -> None:
        """Record or increment occurrence of an unanswered user question."""
        now = datetime.now(timezone.utc).isoformat()
        clean_q = question.strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO unanswered_questions (question, attempts_count, last_asked_at, status)
                VALUES (?, 1, ?, 'pending')
                ON CONFLICT(question) DO UPDATE SET
                    attempts_count = attempts_count + 1,
                    last_asked_at = excluded.last_asked_at
            """, (clean_q, now))
            conn.commit()

    def get_pending_questions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List unanswered questions awaiting human review."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, question, attempts_count, last_asked_at, status
                FROM unanswered_questions
                WHERE status = 'pending'
                ORDER BY attempts_count DESC, last_asked_at DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def approve_answer(
        self,
        question: str,
        answer: str,
        expert_name: str = "Human Expert",
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Approve and store a human expert answer, marking the question resolved."""
        now = datetime.now(timezone.utc).isoformat()
        clean_q = question.strip()
        sources_json = json.dumps(sources or [])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO expert_approved_qa (question, answer, expert_name, sources_json, approved_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(question) DO UPDATE SET
                    answer = excluded.answer,
                    expert_name = excluded.expert_name,
                    sources_json = excluded.sources_json,
                    approved_at = excluded.approved_at
            """, (clean_q, answer.strip(), expert_name, sources_json, now))

            cursor.execute("""
                UPDATE unanswered_questions
                SET status = 'resolved'
                WHERE question = ?
            """, (clean_q,))
            conn.commit()

        return {
            "question": clean_q,
            "answer": answer.strip(),
            "expert_name": expert_name,
            "approved_at": now,
            "sources": sources or [],
        }

    def find_approved_answer(self, question: str) -> Optional[Dict[str, Any]]:
        """Look up if a human expert has verified an answer to this exact or close question."""
        clean_q = question.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT question, answer, expert_name, sources_json, approved_at
                FROM expert_approved_qa
                WHERE LOWER(TRIM(question)) = ?
            """, (clean_q,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["sources"] = json.loads(res["sources_json"]) if res.get("sources_json") else []
            return res

    def list_approved_answers(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all verified expert answers."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT question, answer, expert_name, sources_json, approved_at
                FROM expert_approved_qa
                ORDER BY approved_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["sources"] = json.loads(item["sources_json"]) if item.get("sources_json") else []
                results.append(item)
            return results

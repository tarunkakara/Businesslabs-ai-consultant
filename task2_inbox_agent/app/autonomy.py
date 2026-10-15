"""Autonomous Sending Engine with Strict 20-Streak and Quality Revocation Rules."""

import logging
from typing import Dict, Tuple
from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.models import CategoryAutonomyStatus, EmailCategory

logger = logging.getLogger("inbox_agent.autonomy")


class AutonomyEngine:
    """Enforces the strict 20-consecutive-unchanged approval rule and quality revocation."""

    def __init__(self, streak_threshold: int = 20):
        self.streak_threshold = streak_threshold
        self._category_states: Dict[EmailCategory, CategoryAutonomyStatus] = {
            cat: CategoryAutonomyStatus(category=cat) for cat in EmailCategory
        }

    def record_approval(
        self,
        category: EmailCategory,
        is_unchanged: bool,
    ) -> CategoryAutonomyStatus:
        """Update consecutive streak or trigger immediate revocation upon human edit."""
        state = self._category_states[category]

        if is_unchanged:
            state.consecutive_unchanged_count += 1
            state.total_approved += 1

            # Check if 20-streak milestone is achieved
            if state.consecutive_unchanged_count >= self.streak_threshold:
                if settings.autonomous_sending_enabled:
                    state.autonomous_enabled = True
                    state.revocation_reason = None
                    logger.info("Category %s granted autonomous sending (streak: %d)", category.value, state.consecutive_unchanged_count)
                else:
                    state.autonomous_enabled = False
                    state.revocation_reason = (
                        f"Milestone of {self.streak_threshold} consecutive approvals reached, "
                        "but AUTONOMOUS_SENDING_ENABLED is false in configuration. Human approval enforced."
                    )
        else:
            # Quality dropped: Human had to modify the AI draft
            was_active = state.autonomous_enabled
            state.total_edited += 1
            state.consecutive_unchanged_count = 0  # Reset streak immediately

            if was_active:
                state.autonomous_enabled = False
                state.revocation_reason = (
                    f"REVOKED: Human edit required on draft in category '{category.value}'. "
                    f"Autonomous sending permission automatically revoked to protect customer quality."
                )
                logger.warning("Revoked autonomous mode for category: %s", category.value)
            else:
                state.revocation_reason = (
                    f"Human edit applied in category '{category.value}'. Streak reset to 0."
                )

        # Calculate quality score: ratio of unchanged approvals
        total = state.total_approved + state.total_edited
        state.quality_score = round(state.total_approved / max(1, total), 4)

        return state

    def can_send_autonomously(self, category: EmailCategory) -> Tuple[bool, str]:
        """Check whether autonomous dispatch is authorized for a category."""
        # Rule 1: Master switch must be enabled
        if not settings.autonomous_sending_enabled:
            return (
                False,
                "Autonomous sending is globally disabled by policy (default to human approval).",
            )

        state = self._category_states[category]

        # Rule 2: Category must have achieved and maintained 20 consecutive unchanged approvals
        if state.consecutive_unchanged_count < self.streak_threshold:
            return (
                False,
                f"Category '{category.value}' requires {self.streak_threshold} consecutive unchanged approvals "
                f"(current streak: {state.consecutive_unchanged_count}).",
            )

        if not state.autonomous_enabled:
            return (
                False,
                f"Autonomous sending is currently revoked or pending authorization for '{category.value}'.",
            )

        return True, f"Autonomous sending authorized (streak: {state.consecutive_unchanged_count} unchanged)."

    def get_status(self, category: EmailCategory) -> CategoryAutonomyStatus:
        """Get autonomy metrics for a category."""
        return self._category_states[category]

    def get_all_statuses(self) -> Dict[EmailCategory, CategoryAutonomyStatus]:
        """Get autonomy metrics across all 6 categories."""
        return self._category_states

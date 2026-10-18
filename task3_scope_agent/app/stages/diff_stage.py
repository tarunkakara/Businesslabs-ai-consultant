"""Stage 5: Meeting Revision Diff and Scope Creep Detection."""

from typing import List
from task3_scope_agent.app.models import (
    ExtractedMeetingAnalysis,
    ScopeOfWork,
    TranscriptRevisionDiff,
)


class TranscriptDiffStage:
    """Compares revised meeting transcript against previous version to identify additions, removals, and scope creep."""

    def compare(
        self,
        prev_analysis: ExtractedMeetingAnalysis,
        prev_sow: ScopeOfWork,
        new_analysis: ExtractedMeetingAnalysis,
        new_sow: ScopeOfWork,
    ) -> TranscriptRevisionDiff:
        prev_keys = {r.canonical_key or r.title.lower(): r.title for r in prev_analysis.requirements}
        new_keys = {r.canonical_key or r.title.lower(): r.title for r in new_analysis.requirements}

        added = [new_keys[k] for k in new_keys if k not in prev_keys]
        removed = [prev_keys[k] for k in prev_keys if k not in new_keys]

        hours_delta = round(new_sow.total_developer_hours - prev_sow.total_developer_hours, 1)
        cost_delta = round(new_sow.total_cost_usd - prev_sow.total_cost_usd, 2)

        scope_creep: List[str] = []

        # Scope creep rule 1: New requirements added in revision
        if len(added) >= 1:
            scope_creep.append(
                f"Scope expansion alert: {len(added)} new feature package(s) added in revision ({', '.join(added)})."
            )

        # Scope creep rule 2: Budget increased by more than 20% or > $1,500
        if prev_sow.total_cost_usd > 0 and ((cost_delta / prev_sow.total_cost_usd) > 0.20 or cost_delta >= 1500.0):
            pct = round((cost_delta / prev_sow.total_cost_usd) * 100, 1)
            scope_creep.append(
                f"Budget surge warning: Total project cost increased by {pct}% (+${cost_delta:,.2f}). Requires formal change order."
            )

        # Build clean markdown diff summary
        added_md = "\n".join(f"- **[ADDED]** {a}" for a in added) if added else "- None"
        removed_md = "\n".join(f"- **[REMOVED]** {r}" for r in removed) if removed else "- None"
        creep_md = "\n".join(f"- ⚠️ {w}" for w in scope_creep) if scope_creep else "- ✅ No critical scope creep detected."

        summary_md = (
            f"### Meeting Revision Comparison (v{prev_analysis.version} → v{new_analysis.version})\n\n"
            f"**Estimate Impact**: Δ Hours: `{hours_delta:+} hrs` | Δ Budget: `${cost_delta:+,.2f}`\n\n"
            f"#### Added Requirements ({len(added)})\n{added_md}\n\n"
            f"#### Removed Requirements ({len(removed)})\n{removed_md}\n\n"
            f"#### Scope Creep Analysis\n{creep_md}\n"
        )

        return TranscriptRevisionDiff(
            previous_version=prev_analysis.version,
            new_version=new_analysis.version,
            added_requirements=added,
            removed_requirements=removed,
            hours_delta=hours_delta,
            cost_delta=cost_delta,
            scope_creep_warnings=scope_creep,
            diff_summary_markdown=summary_md,
        )

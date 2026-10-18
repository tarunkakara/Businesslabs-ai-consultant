"""Stage 3: Scope of Work and Hours Estimator."""

import uuid
from typing import List
from task3_scope_agent.app.config import settings
from task3_scope_agent.app.models import (
    ExtractedMeetingAnalysis,
    ScopeOfWork,
    TaskEstimate,
)


class ScopeEstimatorStage:
    """Translates verified requirements into structured task breakdowns, developer hours, and budget."""

    def __init__(self, hourly_rate: float = None):
        self.hourly_rate = hourly_rate or settings.default_hourly_rate

    def estimate(self, analysis: ExtractedMeetingAnalysis) -> ScopeOfWork:
        sow_id = f"SOW-{uuid.uuid4().hex[:6].upper()}"
        tasks: List[TaskEstimate] = []

        native_features = []
        custom_packages = []
        accounted_req_ids = set()
        seen_task_keys = set()

        for req in analysis.requirements:
            # Prevent duplicate tasks by canonical key or title
            dedup_key = req.canonical_key or req.title.lower()
            if dedup_key in seen_task_keys:
                accounted_req_ids.add(req.id)
                continue
            seen_task_keys.add(dedup_key)

            if req.is_native_feature:
                hours = round(req.estimated_hours or 3.0, 1)
                cost = round(hours * self.hourly_rate, 2)
                tasks.append(
                    TaskEstimate(
                        task_id=f"TSK-{len(tasks)+1:02d}",
                        title=f"Platform Config: {req.title}",
                        work_type="platform_configuration",
                        developer_hours=hours,
                        hourly_rate=self.hourly_rate,
                        estimated_cost=cost,
                        rationale=f"Configuring native Brilliant Directories feature. {req.native_feature_evidence or ''}",
                        source_citations=req.source_citations,
                    )
                )
                native_features.append(f"{req.title} (Native Configuration ~{hours}h)")
            else:
                hours = round(req.estimated_hours or 24.0, 1)
                cost = round(hours * self.hourly_rate, 2)
                tasks.append(
                    TaskEstimate(
                        task_id=f"TSK-{len(tasks)+1:02d}",
                        title=f"Custom Engineering: {req.title}",
                        work_type="custom_development",
                        developer_hours=hours,
                        hourly_rate=self.hourly_rate,
                        estimated_cost=cost,
                        rationale="Custom full-stack module architecture, database schema, and frontend UI.",
                        source_citations=req.source_citations,
                    )
                )
                custom_packages.append(f"{req.title} (Custom Engineering ~{hours}h)")

            accounted_req_ids.add(req.id)

        # Missing requirement verification: Ensure all extracted requirements are accounted for
        unaccounted = [
            req.title for req in analysis.requirements
            if req.id not in accounted_req_ids
        ]

        # Base development hours
        dev_hours_total = sum(t.developer_hours for t in tasks)

        # Dedicated Quality Assurance allowance: Exactly 15% of development hours, rounded to 1 decimal place
        qa_hours = round(dev_hours_total * 0.15, 1)
        qa_cost = round(qa_hours * self.hourly_rate, 2)
        tasks.append(
            TaskEstimate(
                task_id=f"TSK-{len(tasks)+1:02d}",
                title="Cross-Device Quality Assurance & UAT",
                work_type="qa_testing",
                developer_hours=qa_hours,
                hourly_rate=self.hourly_rate,
                estimated_cost=qa_cost,
                rationale=(
                    f"Quality Assurance & User Acceptance Testing calculated at exactly 15% of development hours "
                    f"({dev_hours_total}h × 15% = {qa_hours}h, rounded to 1 decimal place at ${self.hourly_rate:,.2f}/hr). "
                    "Covers end-to-end user workflows, responsive mobile testing, and cross-browser validation."
                ),
                source_citations=[],
            )
        )

        # Summary calculations: Summing task items to ensure table column sums exactly match summary metrics
        total_hours = round(sum(t.developer_hours for t in tasks), 1)
        total_cost = round(sum(t.estimated_cost for t in tasks), 2)

        return ScopeOfWork(
            sow_id=sow_id,
            meeting_id=analysis.meeting_id,
            version=analysis.version,
            tasks=tasks,
            total_developer_hours=total_hours,
            total_cost_usd=total_cost,
            native_features_utilized=native_features,
            custom_development_packages=custom_packages,
            unaccounted_requirements=unaccounted,
            qa_allowance_percentage=15.0,
            qa_rounding_rule="15% of total development hours, rounded to 1 decimal place",
        )

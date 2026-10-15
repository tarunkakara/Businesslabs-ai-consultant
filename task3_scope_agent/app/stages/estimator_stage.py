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

        total_hours = 0.0
        native_features = []
        custom_packages = []

        for req in analysis.requirements:
            if req.is_native_feature:
                hours = req.estimated_hours or 3.0
                cost = hours * self.hourly_rate
                tasks.append(
                    TaskEstimate(
                        task_id=f"TSK-{len(tasks)+1:02d}",
                        title=f"Platform Config: {req.title}",
                        work_type="platform_configuration",
                        developer_hours=hours,
                        hourly_rate=self.hourly_rate,
                        estimated_cost=cost,
                        rationale=f"Configuring native Brilliant Directories feature. {req.native_feature_evidence or ''}",
                    )
                )
                native_features.append(f"{req.title} (Native Configuration ~{hours}h)")
                total_hours += hours
            else:
                hours = req.estimated_hours or 24.0
                cost = hours * self.hourly_rate
                tasks.append(
                    TaskEstimate(
                        task_id=f"TSK-{len(tasks)+1:02d}",
                        title=f"Custom Engineering: {req.title}",
                        work_type="custom_development",
                        developer_hours=hours,
                        hourly_rate=self.hourly_rate,
                        estimated_cost=cost,
                        rationale="Custom full-stack module architecture, database schema, and frontend UI.",
                    )
                )
                custom_packages.append(f"{req.title} (Custom Engineering ~{hours}h)")
                total_hours += hours

        # Add Quality Assurance and UAT Task (15% of dev hours, min 4 hours)
        qa_hours = round(max(4.0, total_hours * 0.15), 1)
        qa_cost = qa_hours * self.hourly_rate
        tasks.append(
            TaskEstimate(
                task_id=f"TSK-{len(tasks)+1:02d}",
                title="Cross-Device Quality Assurance & UAT",
                work_type="qa_testing",
                developer_hours=qa_hours,
                hourly_rate=self.hourly_rate,
                estimated_cost=qa_cost,
                rationale="Comprehensive integration testing, cross-browser responsiveness, and user acceptance testing.",
            )
        )
        total_hours += qa_hours

        total_cost = total_hours * self.hourly_rate

        return ScopeOfWork(
            sow_id=sow_id,
            meeting_id=analysis.meeting_id,
            version=analysis.version,
            tasks=tasks,
            total_developer_hours=round(total_hours, 1),
            total_cost_usd=round(total_cost, 2),
            native_features_utilized=native_features,
            custom_development_packages=custom_packages,
        )

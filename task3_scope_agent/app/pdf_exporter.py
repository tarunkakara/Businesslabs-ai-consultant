"""Stage 6: Professional PDF Exporter for Scope of Work (SOW).

Generates formal, publication-grade client deliverables using ReportLab.
Includes executive summary, work-package tables, repeating headers,
clickable documentation citations, QA calculation, ambiguity questions,
dynamic revision diffs, and human sign-off status banners.
"""

import io
import xml.sax.saxutils
from datetime import datetime
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from task3_scope_agent.app.models import (
    ProposalDocument,
    ScopeOfWork,
    TranscriptRevisionDiff,
)


def _esc(text: Optional[str]) -> str:
    """Safely escape text for ReportLab XML paragraph parsing."""
    if text is None:
        return ""
    return xml.sax.saxutils.escape(str(text))


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas that computes total page count and renders professional running headers and footers."""

    def __init__(self, *args, **kwargs):
        kwargs["pageCompression"] = 0
        super().__init__(*args, **kwargs)
        self._saved_page_states: List[Dict[str, Any]] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_decorations(self, page_count: int):
        self.saveState()
        w, h = letter

        # Running Footer (on all pages)
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawString(40, 25, "Confidential & Proprietary — BusinessLabs AI Tech Consulting")
        self.drawRightString(w - 40, 25, f"Page {self._pageNumber} of {page_count}")

        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(40, 36, w - 40, 36)

        # Running Header (pages > 1)
        if self._pageNumber > 1:
            header_title = getattr(self, "doc_header_title", "Technical Implementation Scope of Work")
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#1e293b"))
            self.drawString(40, h - 28, header_title)

            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748b"))
            self.drawRightString(w - 40, h - 28, "BusinessLabs AI Tech Consultant")

            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.5)
            self.line(40, h - 34, w - 40, h - 34)

        self.restoreState()


class ProposalPdfExporter:
    """Compiles meeting analysis, SOW, and client proposal into a consulting-grade PDF document."""

    @classmethod
    def generate_pdf(cls, meeting_record: Dict[str, Any]) -> bytes:
        proposal: ProposalDocument = meeting_record["proposal"]
        sow: ScopeOfWork = proposal.scope_of_work
        analysis = meeting_record.get("analysis")
        diff: Optional[TranscriptRevisionDiff] = meeting_record.get("diff")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=40,
            rightMargin=40,
            topMargin=44,
            bottomMargin=50,
        )

        # Document header title for NumberedCanvas running header
        header_text = f"{proposal.client_name} — Implementation Scope of Work (v{proposal.version})"

        class CustomCanvas(NumberedCanvas):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.doc_header_title = header_text

        styles = cls._build_styles()
        story = []

        # 1. Company Brand & Document Header
        story.append(Paragraph("BUSINESSLABS AI TECH CONSULTING", styles["BrandHeader"]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(_esc(proposal.title), styles["DocTitle"]))
        story.append(Spacer(1, 4))

        meta_line = (
            f"<b>Client:</b> {_esc(proposal.client_name)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Meeting ID:</b> {_esc(proposal.meeting_id)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Revision:</b> v{proposal.version} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Date:</b> {datetime.now().strftime('%B %d, %Y')}"
        )
        story.append(Paragraph(meta_line, styles["MetaLine"]))
        story.append(Spacer(1, 10))

        # 2. Human Approval Status Banner
        story.append(cls._build_status_banner(proposal, styles))
        story.append(Spacer(1, 12))

        # 3. Key Metrics Cards (Table)
        story.append(cls._build_metrics_table(proposal, sow, styles))
        story.append(Spacer(1, 14))

        # 4. Executive Summary
        story.append(Paragraph("1. Executive Summary", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceAfter=8))
        story.append(Paragraph(_esc(proposal.executive_summary), styles["BodyTextDark"]))
        story.append(Spacer(1, 14))

        # 5. Work Package Breakdown Table
        story.append(Paragraph("2. Detailed Work Package Breakdown", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceAfter=8))
        story.append(cls._build_work_package_table(sow, styles))
        story.append(Spacer(1, 12))

        # 6. Quality Assurance Policy & Arithmetic
        story.append(cls._build_qa_callout(sow, styles))
        story.append(Spacer(1, 14))

        # 7. Ambiguities & Client Clarification Questions (if any)
        open_questions = []
        if analysis and analysis.open_questions:
            open_questions = analysis.open_questions
        elif sow.open_questions:
            open_questions = sow.open_questions

        if open_questions:
            story.append(KeepTogether(cls._build_questions_block(open_questions, styles)))
            story.append(Spacer(1, 14))

        # 8. Revision Comparison & Scope Creep (if revision diff exists)
        if diff:
            story.append(KeepTogether(cls._build_diff_block(diff, styles)))
            story.append(Spacer(1, 14))

        # 9. Sign-off / Signature Block
        story.append(KeepTogether(cls._build_signature_block(proposal, styles)))

        # Build document
        doc.build(story, canvasmaker=CustomCanvas)
        return buffer.getvalue()

    @classmethod
    def _build_styles(cls) -> Dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        custom = {}

        custom["BrandHeader"] = ParagraphStyle(
            "BrandHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#4f46e5"),  # Indigo accent
            spaceAfter=2,
        )

        custom["DocTitle"] = ParagraphStyle(
            "DocTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=0,  # Left
            textColor=colors.HexColor("#0f172a"),  # Deep slate
            spaceAfter=2,
        )

        custom["MetaLine"] = ParagraphStyle(
            "MetaLine",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#475569"),
        )

        custom["SectionHeading"] = ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=6,
            spaceAfter=4,
            keepWithNext=True,
        )

        custom["BodyTextDark"] = ParagraphStyle(
            "BodyTextDark",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13.5,
            textColor=colors.HexColor("#334155"),
        )

        custom["StatusDraftTitle"] = ParagraphStyle(
            "StatusDraftTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#92400e"),  # Amber 800
        )

        custom["StatusDraftBody"] = ParagraphStyle(
            "StatusDraftBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#b45309"),  # Amber 700
        )

        custom["StatusApprovedTitle"] = ParagraphStyle(
            "StatusApprovedTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#065f46"),  # Emerald 800
        )

        custom["StatusApprovedBody"] = ParagraphStyle(
            "StatusApprovedBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#047857"),  # Emerald 700
        )

        custom["TableHeader"] = ParagraphStyle(
            "TableHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
        )

        custom["TableHeaderRight"] = ParagraphStyle(
            "TableHeaderRight",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            alignment=2,  # Right
            textColor=colors.white,
        )

        custom["TableCellBold"] = ParagraphStyle(
            "TableCellBold",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#0f172a"),
        )

        custom["TableCell"] = ParagraphStyle(
            "TableCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#334155"),
        )

        custom["TableCellRight"] = ParagraphStyle(
            "TableCellRight",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            alignment=2,  # Right
            textColor=colors.HexColor("#0f172a"),
        )

        custom["CitationText"] = ParagraphStyle(
            "CitationText",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#475569"),
        )

        custom["MetricLabel"] = ParagraphStyle(
            "MetricLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748b"),
        )

        custom["MetricValue"] = ParagraphStyle(
            "MetricValue",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=17,
            textColor=colors.HexColor("#1e293b"),
        )

        custom["MetricSub"] = ParagraphStyle(
            "MetricSub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#64748b"),
        )

        return custom

    @classmethod
    def _build_status_banner(cls, proposal: ProposalDocument, styles: Dict[str, ParagraphStyle]) -> Table:
        if proposal.is_approved_by_human:
            content = [
                Paragraph("<b>OFFICIALLY APPROVED &amp; SIGNED OFF</b>", styles["StatusApprovedTitle"]),
                Paragraph(
                    f"Signed off by <b>{_esc(proposal.approved_by)}</b> on {_esc(proposal.approved_at)}. "
                    "This Scope of Work is authorized for engineering commencement.",
                    styles["StatusApprovedBody"],
                ),
            ]
            t = Table([[content]], colWidths=[532])
            t.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#dcfce7")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#10b981")),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ])
            )
            return t
        else:
            content = [
                Paragraph("<b>DRAFT — AWAITING HUMAN SIGN-OFF</b>", styles["StatusDraftTitle"]),
                Paragraph(
                    "This technical proposal is currently pending expert review by a Lead Solutions Architect. "
                    "All hours, architecture classifications, and cost estimates remain preliminary until formal approval.",
                    styles["StatusDraftBody"],
                ),
            ]
            t = Table([[content]], colWidths=[532])
            t.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#f59e0b")),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ])
            )
            return t

    @classmethod
    def _build_metrics_table(
        cls, proposal: ProposalDocument, sow: ScopeOfWork, styles: Dict[str, ParagraphStyle]
    ) -> Table:
        dev_hours = sum(t.developer_hours for t in sow.tasks if t.work_type != "qa_testing")
        qa_task = next((t for t in sow.tasks if t.work_type == "qa_testing"), None)
        qa_hours = qa_task.developer_hours if qa_task else round(dev_hours * 0.15, 1)

        c1 = [
            Paragraph("TOTAL DEV HOURS", styles["MetricLabel"]),
            Spacer(1, 2),
            Paragraph(f"{dev_hours:.1f} hrs", styles["MetricValue"]),
            Spacer(1, 2),
            Paragraph("Engineering Effort", styles["MetricSub"]),
        ]
        c2 = [
            Paragraph("QA ALLOWANCE (15%)", styles["MetricLabel"]),
            Spacer(1, 2),
            Paragraph(f"{qa_hours:.1f} hrs", styles["MetricValue"]),
            Spacer(1, 2),
            Paragraph(f"{sow.qa_allowance_percentage}% of dev hours", styles["MetricSub"]),
        ]
        c3 = [
            Paragraph("TOTAL ESTIMATED COST", styles["MetricLabel"]),
            Spacer(1, 2),
            Paragraph(f"${sow.total_cost_usd:,.2f}", styles["MetricValue"]),
            Spacer(1, 2),
            Paragraph("Blended Rate: $120.00/h", styles["MetricSub"]),
        ]
        c4 = [
            Paragraph("TARGET TIMELINE", styles["MetricLabel"]),
            Spacer(1, 2),
            Paragraph(f"{proposal.estimated_timeline_weeks:.0f} Weeks", styles["MetricValue"]),
            Spacer(1, 2),
            Paragraph("Deployment Target", styles["MetricSub"]),
        ]

        t = Table([[c1, c2, c3, c4]], colWidths=[133, 133, 133, 133])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        return t

    @classmethod
    def _build_work_package_table(cls, sow: ScopeOfWork, styles: Dict[str, ParagraphStyle]) -> Table:
        # Col widths total 532: [45, 252, 95, 55, 85]
        headers = [
            Paragraph("ID", styles["TableHeader"]),
            Paragraph("Work Package & Verified Documentation", styles["TableHeader"]),
            Paragraph("Classification", styles["TableHeader"]),
            Paragraph("Hours", styles["TableHeaderRight"]),
            Paragraph("Cost (USD)", styles["TableHeaderRight"]),
        ]
        data = [headers]

        for t in sow.tasks:
            # Classification badge text
            badge_text = "Custom Dev"
            if t.work_type == "platform_configuration":
                badge_text = "Native Config"
            elif t.work_type == "qa_testing":
                badge_text = "Dedicated QA"

            # Package description and citations
            desc_flowables = [
                Paragraph(f"<b>{_esc(t.title)}</b>", styles["TableCellBold"]),
            ]
            if t.rationale:
                desc_flowables.append(Spacer(1, 2))
                desc_flowables.append(Paragraph(_esc(t.rationale), styles["TableCell"]))

            if t.source_citations:
                desc_flowables.append(Spacer(1, 3))
                citation_links = []
                for c in t.source_citations:
                    c_title = _esc(c.get("title", "Solution Article"))
                    c_url = _esc(c.get("url", ""))
                    citation_links.append(f'<link href="{c_url}" color="#2563eb"><u>{c_title}</u></link>')
                citations_html = f"<b>Verified Source:</b> {', '.join(citation_links)}"
                desc_flowables.append(Paragraph(citations_html, styles["CitationText"]))

            data.append([
                Paragraph(f"<b>{_esc(t.task_id)}</b>", styles["TableCellBold"]),
                desc_flowables,
                Paragraph(badge_text, styles["TableCell"]),
                Paragraph(f"{t.developer_hours:.1f}h", styles["TableCellRight"]),
                Paragraph(f"${t.estimated_cost:,.2f}", styles["TableCellRight"]),
            ])

        # Summary Total Row
        total_desc = [
            Paragraph("<b>TOTAL (Base Development &amp; Dedicated QA)</b>", styles["TableCellBold"]),
            Paragraph("Exact sum of all work packages at $120.00/hr blended rate", styles["TableCell"]),
        ]
        data.append([
            Paragraph("<b>TOTAL</b>", styles["TableCellBold"]),
            total_desc,
            Paragraph("<b>All Tasks</b>", styles["TableCellBold"]),
            Paragraph(f"<b>{sow.total_developer_hours:.1f}h</b>", styles["TableCellRight"]),
            Paragraph(f"<b>${sow.total_cost_usd:,.2f}</b>", styles["TableCellRight"]),
        ])

        table = Table(data, colWidths=[45, 252, 95, 55, 85], repeatRows=1)
        table_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#cbd5e1")),
            # Total row styling
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
            ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.HexColor("#94a3b8")),
        ]

        # Alternating row colors (skip header and total row)
        for i in range(1, len(data) - 1):
            if i % 2 == 0:
                table_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc")))

        table.setStyle(TableStyle(table_style))
        return table

    @classmethod
    def _build_qa_callout(cls, sow: ScopeOfWork, styles: Dict[str, ParagraphStyle]) -> Table:
        dev_hours = sum(t.developer_hours for t in sow.tasks if t.work_type != "qa_testing")
        qa_task = next((t for t in sow.tasks if t.work_type == "qa_testing"), None)
        qa_hours = qa_task.developer_hours if qa_task else round(dev_hours * 0.15, 1)
        qa_cost = qa_task.estimated_cost if qa_task else round(qa_hours * 120.0, 2)

        content = [
            Paragraph("<b>Quality Assurance Policy &amp; Rigorous Testing Formula</b>", styles["TableCellBold"]),
            Spacer(1, 3),
            Paragraph(
                f"A mandatory Quality Assurance allowance of exactly <b>{sow.qa_allowance_percentage}%</b> "
                f"({_esc(sow.qa_rounding_rule)}) is included in the project scope. "
                f"Formula: <code>{dev_hours:.1f} dev hours × 15% = {qa_hours:.1f} QA hours</code> "
                f"at $120.00/hr = <b>${qa_cost:,.2f}</b>. "
                "Covers end-to-end user journeys, responsive mobile/tablet layout verification, "
                "Stripe test mode transactions, and webhook delivery verification.",
                styles["TableCell"],
            ),
        ]
        t = Table([[content]], colWidths=[532])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ])
        )
        return t

    @classmethod
    def _build_questions_block(
        cls, open_questions: List[str], styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        items: List[Any] = [
            Paragraph("3. Ambiguities &amp; Client Clarification Questions", styles["SectionHeading"]),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceAfter=6),
            Paragraph(
                "The following items were identified as underspecified or subject to interpretation. "
                "Client feedback is requested to finalize technical specifications:",
                styles["BodyTextDark"],
            ),
            Spacer(1, 4),
        ]

        q_rows = []
        for i, q in enumerate(open_questions, 1):
            q_rows.append([
                Paragraph(f"<b>Q{i}</b>", styles["TableCellBold"]),
                Paragraph(_esc(q), styles["TableCell"]),
            ])

        t = Table(q_rows, colWidths=[30, 502])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),  # Amber 50
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#fcd34d")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#fef3c7")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        items.append(t)
        return items

    @classmethod
    def _build_diff_block(
        cls, diff: TranscriptRevisionDiff, styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        items: List[Any] = [
            Paragraph("4. Revision Comparison &amp; Scope Creep Analysis", styles["SectionHeading"]),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceAfter=6),
        ]

        summary_p = Paragraph(
            f"<b>Revision Transition:</b> Version {diff.previous_version} &rarr; Version {diff.new_version} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Hours Impact:</b> <code>{diff.hours_delta:+.1f} hrs</code> &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Budget Impact:</b> <code>${diff.cost_delta:+,.2f}</code>",
            styles["TableCellBold"],
        )

        rows = [[summary_p]]

        if diff.added_requirements:
            added_text = "<b>Added Work Packages:</b><br/>" + "<br/>".join(
                f"&bull; {_esc(a)}" for a in diff.added_requirements
            )
            rows.append([Paragraph(added_text, styles["TableCell"])])

        if diff.removed_requirements:
            removed_text = "<b>Removed Work Packages:</b><br/>" + "<br/>".join(
                f"&bull; {_esc(r)}" for r in diff.removed_requirements
            )
            rows.append([Paragraph(removed_text, styles["TableCell"])])

        if diff.scope_creep_warnings:
            warnings_text = "<b>⚠️ Scope Creep Warnings:</b><br/>" + "<br/>".join(
                f"&bull; {_esc(w)}" for w in diff.scope_creep_warnings
            )
            rows.append([Paragraph(warnings_text, styles["StatusDraftBody"])])

        t = Table(rows, colWidths=[532])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ])
        )
        items.append(t)
        return items

    @classmethod
    def _build_signature_block(
        cls, proposal: ProposalDocument, styles: Dict[str, ParagraphStyle]
    ) -> Table:
        status_text = (
            f"Approved by <b>{_esc(proposal.approved_by)}</b> on {_esc(proposal.approved_at)}"
            if proposal.is_approved_by_human
            else "<i>Awaiting Human Sign-Off (Draft)</i>"
        )

        consultant_sig = [
            Paragraph("<b>Solutions Architect Sign-Off</b>", styles["TableCellBold"]),
            Spacer(1, 8),
            Paragraph(status_text, styles["TableCell"]),
            Spacer(1, 14),
            Paragraph("________________________________________", styles["TableCell"]),
            Paragraph("Authorized Signature &amp; Date", styles["MetricSub"]),
        ]

        client_sig = [
            Paragraph(f"<b>Client Authorization ({_esc(proposal.client_name)})</b>", styles["TableCellBold"]),
            Spacer(1, 8),
            Paragraph("Accepted and approved to proceed with stated scope and rates.", styles["TableCell"]),
            Spacer(1, 14),
            Paragraph("________________________________________", styles["TableCell"]),
            Paragraph("Client Representative Signature &amp; Date", styles["MetricSub"]),
        ]

        t = Table([[consultant_sig, client_sig]], colWidths=[266, 266])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ])
        )
        return t

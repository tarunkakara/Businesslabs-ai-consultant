# BusinessLabs AI Tech Consultant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/pytest-34%20passed-brightgreen.svg)]()
[![Evals](https://img.shields.io/badge/evals-60%2F60%20passed-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, interconnected AI consulting suite built for **BusinessLabs** to streamline technical advisory for the **Brilliant Directories** platform. Developed for the AI Engineering Internship Selection Capstone.

---

## Architecture Overview

The system consists of three modular, type-safe Python microservices connected through REST contracts:

```
                    +-----------------------------------------------+
                    |      Task 1: Python Knowledge Brain           |
                    |   - Crawls Brilliant Directories Solution Ctr |
                    |   - Multimodal RAG + Grounded Citations       |
                    |   - Truthful Refusal & Expert Approval Loop   |
                    |   Port: 8000 | UI: / | Docs: /docs            |
                    +-----------------------+-----------------------+
                                            |
                 +--------------------------+--------------------------+
                 |                                                     |
                 v                                                     v
+-------------------------------------+               +-------------------------------------+
|       Task 2: Inbox Agent           |               |   Task 3: Meeting-to-Scope Agent    |
| - 50+ Synthetic Email Generator     |               | - 5-Stage Transcript Pipeline       |
| - Intent Classification & Drafting  |               | - Native Feature Check vs Task 1    |
| - Levenshtein Edit-Distance Tracker |               | - SOW Breakdown & Rates ($120/hr)   |
| - Strict 20-Streak Autonomy Revoke  |               | - Client Proposal Human Boundary    |
| Port: 8001 | UI: / | Docs: /docs    |               | - Revision Diff & Scope Creep Alerts|
+-------------------------------------+               | Port: 8002 | UI: / | Docs: /docs    |
                                                      +-------------------------------------+
```

---

## Key Capabilities

1. **Dual-Provider Architecture**: Works 100% offline out-of-the-box using deterministic `MockClaudeProvider` with zero hallucinated credentials. Adding an `ANTHROPIC_API_KEY` to `.env` seamlessly activates live `claude-3-5-sonnet` calls.
2. **Grounded RAG with Truthful Refusal**: Answers include verifiable markdown citations `[Title](URL)`. If cosine similarity falls below `0.35`, the system refuses to speculate and routes the question to human experts.
3. **Transparent Edit-Distance & Autonomy Gate**: Autonomous email sending requires 20 consecutive human approvals without modifications. Any human edit immediately resets the streak and revokes autonomy.
4. **Scope Extraction & Budget Guard**: Extracts requirements, flags ambiguities, checks Brilliant Directories native capabilities to save client budget, and alerts on scope creep (>20% hours or >$2,000 surge).

---

## Quick Start (Under 2 Minutes)

### 1. Prerequisites
- Python 3.11, 3.12, or 3.13 64-bit
- Git

### 2. Setup Virtual Environment
```bash
# Clone repository
git clone https://github.com/likhith/businesslabs-ai-consultant.git
cd businesslabs-ai-consultant

# Create & activate venv
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

### 3. Launch All Services
```bash
python run_all.py
```
Open your browser to:
- **Task 1 Knowledge Brain UI**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Task 2 Inbox Agent UI**: [http://127.0.0.1:8001](http://127.0.0.1:8001)
- **Task 3 Meeting-to-Scope UI**: [http://127.0.0.1:8002](http://127.0.0.1:8002)

---

## Verification & Automated Evals

Run the complete test suite (34 unit & integration tests) and 60-case benchmark:
```bash
# Run 34 unit & integration tests
pytest -v

# Run 60 quantitative evals (20 per task)
python evals/task1_evals.py
python evals/task2_evals.py
python evals/task3_evals.py
```

---

## Repository Documentation
- [DESIGN.md](file:///c:/Users/likhi/OneDrive/Desktop/project/DESIGN.md): Comprehensive 4-page system architecture, trade-offs, and design rationale.
- [EVALS.md](file:///c:/Users/likhi/OneDrive/Desktop/project/EVALS.md): Full evaluation methodology, quantitative metrics, and latency/token analysis.
- [AI_LOG.md](file:///c:/Users/likhi/OneDrive/Desktop/project/AI_LOG.md): 5 high-impact AI prompts and 1 critically rejected AI suggestion.
- [DEMO_PLAN.md](file:///c:/Users/likhi/OneDrive/Desktop/project/DEMO_PLAN.md): Step-by-step 10-minute live demonstration script with deliberate edge cases.

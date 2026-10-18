# BusinessLabs AI Tech Consultant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Pytest](https://img.shields.io/badge/pytest-70%20passed%20(100%25)-brightgreen.svg)]()
[![Evals](https://img.shields.io/badge/evals-60%2F60%20passed-brightgreen.svg)]()
[![Deployment](https://img.shields.io/badge/Render-Live%20Deployment-46e3b7.svg)](https://businesslabs-ai-consultant.onrender.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, interconnected multi-agent consulting suite built for **BusinessLabs** to streamline technical advisory, support automation, and statement-of-work (SOW) generation for the **Brilliant Directories** platform.

---

> 🚀 **LIVE PRODUCTION CLOUD DEPLOYMENT**  
> **Master URL**: [https://businesslabs-ai-consultant.onrender.com](https://businesslabs-ai-consultant.onrender.com)  
> *Hosted on Render via a unified application gateway — all 3 specialized AI agent microservices, interactive web dashboards, and REST APIs are accessible through this single public link.*

### Quick Link Registry

| Subsystem / Tool | Route | Direct Link | Primary Function |
|---|---|---|---|
| **Suite Portal Hub** | `/` | [Open Hub](https://businesslabs-ai-consultant.onrender.com/) | Master landing dashboard with live telemetry & embedded workspaces |
| **Task 1: Knowledge Brain** | `/brain` | [Open Brain](https://businesslabs-ai-consultant.onrender.com/brain) | Solution Center RAG, grounded Q&A, citations & expert approval |
| **Task 2: Client Inbox Agent** | `/inbox` | [Open Inbox](https://businesslabs-ai-consultant.onrender.com/inbox) | Email classification, voice drafting & 20-streak autonomy gate |
| **Task 3: Meeting-to-Scope Agent** | `/scope` | [Open Scope](https://businesslabs-ai-consultant.onrender.com/scope) | 5-stage scoping, native BD check, diffs & professional PDF export |
| **Unified Swagger API Docs** | `/docs` | [Open Docs](https://businesslabs-ai-consultant.onrender.com/docs) | Interactive OpenAPI documentation for all endpoints |
| **Aggregated Health Telemetry** | `/health` | [View Health](https://businesslabs-ai-consultant.onrender.com/health) | Real-time JSON telemetry with live RSS memory tracking and 512MB headroom monitoring |

---

## Architecture Overview

The system runs as a **Unified High-Availability Gateway (`server.py`)** on a single port for cloud deployments, while maintaining 100% backward compatibility for isolated standalone execution (`run_all.py` across ports 8000, 8001, and 8002):

```
                                +---------------------------------------------------------+
                                |      Unified Monorepo Gateway (server.py / wsgi.py)     |
                                |         Listens on 0.0.0.0:$PORT (Single Public Link)   |
                                +----------------------------+----------------------------+
                                                             |
            +------------------------------------------------+------------------------------------------------+
            |                                                |                                                |
            v                                                v                                                v
+------------------------------------+   +------------------------------------+   +------------------------------------+
|     Task 1: Knowledge Brain        |   |     Task 2: Inbox Agent            |   |   Task 3: Meeting-to-Scope Agent   |
| - Brilliant Directories RAG Engine |   | - 6-Category Support Email Triage  |   | - 5-Stage Transcript Pipeline      |
| - Verified Citation Attribution    |   | - Voice-Calibrated AI Drafting     |   | - Native BD Feature Verification   |
| - Truthful Refusal & Expert Store  |   | - 20-Approval Streak Autonomy Gate |   | - Scope Creep Diff & QA Budgeting  |
| Direct UI: /brain                  |   | Direct UI: /inbox                  |   | Direct UI: /scope                  |
| API: /api/v1/query, /api/v1/crawl  |   | API: /api/v1/emails, /api/v1/drafts|   | API: /api/v1/scope/*, Export PDF   |
+-----------------+------------------+   +-----------------+------------------+   +-----------------+------------------+
                  |                                        |                                        |
                  +----------------------------------------+----------------------------------------+
                                                           |
                                  +------------------------+------------------------+
                                  |                                                 |
                                  v                                                 v
                   +------------------------------+                  +------------------------------+
                   |  ChromaDB Vector Store       |                  |  SQLite Relational DBs       |
                   |  - 768-Dim Text Embeddings   |                  |  - crawl_state.db            |
                   |  - Cosine Distance Metric    |                  |  - knowledge_brain.db        |
                   |  - Persistent at data/chroma |                  |  - inbox_audit.db            |
                   +------------------------------+                  +------------------------------+
```

---

## Detailed Pin-to-Pin System Guide

### Task 1: Knowledge Brain (Grounded RAG & Retrieval Engine)
* **Objective**: Eliminates AI hallucination by grounding technical answers strictly in official Brilliant Directories Solution Center documentation.
* **How It Works**:
  1. **Crawler & Sitemap Parser**: Crawls the official sitemap (`https://support.brilliantdirectories.com/support/sitemap.xml`), filters for solution articles, and deduplicates URLs.
  2. **Extraction & Chunking**: Cleans raw HTML via BeautifulSoup, extracting titles, categories, and substantive paragraphs into 800-character chunks with 150-character semantic overlap.
  3. **Vector Persistence**: Embeds chunks into a local persistent ChromaDB collection (`bd_knowledge_base`) paired with SQLite state tracking.
  4. **Truthful Refusal**: For any question where maximum cosine similarity falls below `0.35`, the engine refuses to speculate, returning `"Insufficient Evidence"` and logging the question to SQLite for human review.
  5. **Verified Citations**: Generates deduplicated markdown citations (`[Title](URL)`) linking directly to official documentation articles.
  6. **Human-in-the-Loop Feedback**: Consultants can review pending unanswered queries, input verified answers, and approve them. Approved answers are automatically embedded into the vector store and prioritized in future retrievals.
  7. **Dual-Provider Architecture**: Operates 100% offline via `MockClaudeProvider` with zero external API calls or credentials needed. Set `ANTHROPIC_API_KEY` in `.env` to seamlessly engage live `claude-3-5-sonnet` inferences.
* **How to Use**:
  - Open `/brain`.
  - Click any sample query (e.g., *"What payment gateways are supported?"* or *"How to set up webhooks for real-time notifications?"*).
  - Observe the grounded response, confidence score, end-to-end latency, and official citation cards.
  - Test an unsupported query (e.g., *"How to configure quantum database clusters?"*) to observe truthful refusal and review logging.

---

### Task 2: Client Inbox Routing Agent (Support Automation & Autonomy Gate)
* **Objective**: Automates repetitive client support triage while guaranteeing brand voice consistency and enforcing an ironclad human-in-the-loop safety boundary.
* **How It Works**:
  1. **Synthetic Data Generator**: Ships with 55 realistic customer support emails spanning 6 distinct business categories: `Account Billing`, `Technical Support`, `Feature Inquiry`, `Integration`, `Custom Dev`, and `General Question`.
  2. **Multi-Category Intent Classifier**: Analyzes sender intent, subject, and body text to assign categories and urgency priority (`high`, `medium`, `low`).
  3. **Knowledge-Grounded Drafting**: Connects directly to the Task 1 Knowledge Brain to retrieve official platform facts before composing email replies.
  4. **Tone Calibration (Company Voice)**: Calibrates drafts across 3 distinct communication personas:
     - `Professional` (authoritative, clear, courteous)
     - `Urgent` (concise, action-oriented, direct troubleshooting)
     - `Friendly` (warm, empathetic, welcoming)
  5. **Strict 20-Approval Streak Autonomy Gate**: To unlock automated email dispatch for any category, the AI drafter must achieve **20 consecutive approvals without human modification**.
  6. **Automatic Revocation**: Uses character-level Levenshtein edit distance and difflib similarity. If a human reviewer modifies a draft (similarity $< 0.95$), the category's streak **immediately resets to 0** and autonomous dispatch is revoked.
  7. **Transparent Audit Logging**: Every original draft, human modification, similarity percentage, feedback note, and reviewer timestamp is persisted in `inbox_audit.db`.
* **How to Use**:
  - Open `/inbox`.
  - Select an incoming customer email from the inbox list.
  - Choose a company voice and click **Generate Grounded Draft**.
  - Review the AI-generated response and citations.
  - Approve unchanged to increment the category's autonomy streak, or edit the response to test the automatic safety revocation mechanism.

---

### Task 3: Meeting-to-Scope Agent (Discovery to SOW & Capstone PDF Export)
* **Objective**: Transforms raw, unstructured discovery call transcripts into structured, actionable statements of work (SOW) while guarding client budget against unnecessary custom development.
* **How It Works**:
  1. **Stage 1 — Information Extraction**: Parses conversational dialogue, identifies functional requirements, converts full sentences into concise work-package titles, and flags ambiguous statements (e.g., *"some sort of modern look"*) with client clarification questions.
  2. **Stage 2 — Native Feature Checking**: Queries the Task 1 Knowledge Brain before recommending custom development. Verifies that features like Stripe payments, outbound webhooks, directory structure, and lead distribution are native Brilliant Directories features ($3.0\text{h}$ configuration only), isolating true custom development (e.g., bidirectional patient scheduling database sync at $24.0\text{h}$).
  3. **Stage 3 — Scope & Budget Estimation**: Calculates total developer hours, applies the standard blended rate of **$120.00/hour**, calculates a dedicated **QA allowance (15–20%)**, and estimates project timeline in weeks.
  4. **Stage 4 — Human Approval Boundary**: Enforces that proposals remain in `DRAFT` status until explicitly signed off by a human consultant.
  5. **Stage 5 — Scope Creep & Diff Tracking**: When a client updates requirements across meetings (e.g., Meeting 1 to Meeting 2), the engine performs deep diff analysis, flags added/modified/dropped items, and alerts on scope creep if hours surge $>20\%$ or budget increases $>\$2,000$.
  6. **Publication-Grade PDF Export**: Generates a professional, downloadable PDF containing client info, executive summary, work packages, QA calculations, clickable documentation URLs, and sign-off verification.
* **How to Use**:
  - Open `/scope`.
  - Load **HealthHub Directories (Sample 1)** or paste a meeting transcript.
  - Click **Run 5-Stage Scope Pipeline**.
  - Inspect the work package breakdown, budget calculations ($120/hr), and clarification questions.
  - Click **Approve & Finalize Proposal** to apply human sign-off.
  - Click **Export SOW (PDF)** to download the formatted proposal document.
  - Load **Sample 2 (Revision)** with Meeting 1 ID to test dynamic scope creep diffing.

---

## Local Development & Quick Start

### 1. Prerequisites
- Python 3.11, 3.12, or 3.13 (64-bit)
- Git

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/tarunkakara/Businesslabs-ai-consultant.git
cd Businesslabs-ai-consultant

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate       # On Windows
source venv/bin/activate    # On macOS/Linux

# Install all dependencies
pip install -r requirements.txt
```

### 3. Launch Unified Server (Single Link Port 8000)
```bash
python server.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

*(Optional: To run standalone services on 3 independent ports 8000, 8001, 8002, run `python run_all.py`)*

---

## Automated Verification & Test Suite

The project includes a comprehensive, hermetic automated test suite with **70 tests passing (100% pass rate)**:

```bash
# Run all 70 unit, integration, and gateway tests
pytest -v
```

### Test Suite Coverage Breakdown
* **`tests/test_server.py`** (5 tests): Unified gateway health, route dispatch, UI templates, and JSON service registry.
* **`task1_knowledge_brain/tests/`** (28 tests): Sitemap crawler, article extraction, ChromaDB vector indexing, grounded RAG Q&A, actionable webhooks, payment gateways, lead distribution, and expert approval loop.
* **`task2_inbox_agent/tests/`** (11 tests): Synthetic email generation, 6-category classification, Levenshtein edit distance, tone drafting, 20-streak autonomy rule, and automatic revocation.
* **`task3_scope_agent/tests/`** (15 tests): Transcript extraction, native BD feature verification, QA math, scope creep diffing, human sign-off enforcement, and publication PDF generation.
* **`_legacy_recruitment/`** (11 tests): Legacy database and session isolation tests.

### Quantitative Evaluations
Run the 60-case quantitative benchmark (20 evaluation cases per task):
```bash
python evals/task1_evals.py
python evals/task2_evals.py
python evals/task3_evals.py
```

---

## Deploy to Render (Single Public Web Service)

Deploy the entire suite under a single `*.onrender.com` URL:

1. In Render, select **New Web Service** and connect repository `https://github.com/tarunkakara/Businesslabs-ai-consultant`.
2. Configure settings:
   - **Language**: `Python 3`
   - **Branch**: `main`
   - **Region**: `Ohio (US East)` (or your preferred region)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`
3. Environment Variables (Optional):
   - `MOCK_MODE`: `true` *(default; works 100% offline with zero external API dependencies)*
   - `ANTHROPIC_API_KEY`: *(optional; enter your key to activate live Claude 3.5 Sonnet)*
   - `PYTHON_VERSION`: `3.13.0`
4. Click **Deploy Web Service**. Render binds to `$PORT` and generates your live single-link URL!

---

## Repository Engineering Documentation
- [DESIGN.md](DESIGN.md): Comprehensive 4-page system architecture, trade-offs, and design rationale.
- [EVALS.md](EVALS.md): Full evaluation methodology, quantitative metrics, and latency/token analysis.
- [AI_LOG.md](AI_LOG.md): 5 high-impact AI prompts and 1 critically rejected AI suggestion.
- [DEMO_PLAN.md](DEMO_PLAN.md): Step-by-step 10-minute live demonstration script with deliberate edge cases.

---

## License
MIT License. Developed for the BusinessLabs AI Engineering Internship Selection Capstone.

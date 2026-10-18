# BusinessLabs AI Tech Consultant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/pytest-70%20passed-brightgreen.svg)]()
[![Evals](https://img.shields.io/badge/evals-60%2F60%20passed-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, interconnected AI consulting suite built for **BusinessLabs** to streamline technical advisory for the **Brilliant Directories** platform. Features a unified gateway hosting all three specialized AI agent services under a **single public link** for cloud deployment.

---

## Unified Monorepo Architecture

The entire monorepo can run as a **Unified Gateway (`server.py`)** on a single port for cloud environments like Render, or as 3 isolated standalone microservices locally (`run_all.py`):

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
|   Task 1: Knowledge Brain          |   |   Task 2: Inbox Agent              |   |   Task 3: Meeting-to-Scope Agent   |
| - Brilliant Directories RAG Engine |   | - 6-Category Support Email Triage  |   | - 5-Stage Transcript Pipeline      |
| - Verified Citation Attribution    |   | - Voice-Calibrated AI Drafting     |   | - Native BD Feature Verification   |
| - Truthful Refusal & Expert Store  |   | - 20-Approval Streak Autonomy Gate |   | - Scope Creep Diff & QA Budgeting  |
| Direct UI: /brain                  |   | Direct UI: /inbox                  |   | Direct UI: /scope                  |
| API: /api/v1/query, /api/v1/crawl  |   | API: /api/v1/emails, /api/v1/drafts|   | API: /api/v1/scope/*, Export PDF   |
+------------------------------------+   +------------------------------------+   +------------------------------------+
```

### Single Public Link Routing
- **`/`**: Unified Suite Portal with telemetry dashboard & live embedded microservice workspace.
- **`/brain`**: Task 1 Knowledge Brain RAG Dashboard.
- **`/inbox`**: Task 2 Client Support Inbox & Autonomy Review Dashboard.
- **`/scope`**: Task 3 Meeting-to-Scope Proposal Generator with Capstone PDF Export.
- **`/health`**: Aggregated health check returning status of all 3 microservices.
- **`/docs`**: Unified OpenAPI / Swagger UI documenting all endpoints.

---

## Quick Start (Local Run)

### 1. Prerequisites
- Python 3.11, 3.12, or 3.13 64-bit
- Git

### 2. Setup Virtual Environment
```bash
# Clone repository
git clone https://github.com/tarunkakara/Businesslabs-ai-consultant.git
cd Businesslabs-ai-consultant

# Create & activate venv
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

### 3. Launch Unified Server (Single Port 8000)
```bash
python server.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser to access the complete suite.

*(Alternative: Run standalone services on 3 ports via `python run_all.py`)*

---

## Deploy to Render (Single Public Web Service)

Deploy the entire suite under a single `*.onrender.com` URL:

1. **Create New Web Service** on Render:
   - **Repository**: `https://github.com/tarunkakara/Businesslabs-ai-consultant`
   - **Environment / Language**: `Python 3`
   - **Region**: `Ohio (US East)` (or your preferred region)
   - **Branch**: `main`
   - **Root Directory**: *(leave blank)*
2. **Build & Start Commands**:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`
3. **Environment Variables** (Optional):
   - `MOCK_MODE`: `true` *(default; works 100% offline with zero external API dependencies)*
   - `ANTHROPIC_API_KEY`: *(optional; enter your key to activate live Claude 3.5 Sonnet)*
   - `PYTHON_VERSION`: `3.13.0`
4. Click **Deploy Web Service**. Once deployed, your single public URL grants access to all 3 tasks!

---

## Verification & Automated Tests

Run the complete test suite (70 unit & integration tests across all microservices and gateway):
```bash
pytest -v
```

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

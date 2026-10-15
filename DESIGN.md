# System Design Document: BusinessLabs AI Tech Consultant

**Author**: AI Engineering Candidate  
**Project**: BusinessLabs AI Tech Consultant Capstone  
**Target Platform**: Brilliant Directories Ecosystem  
**Architecture Style**: Asynchronous Microservices Monorepo  
**Version**: 1.0.0 (Production Candidate)  
**Date**: October 2026  

---

## 1. Executive Summary & Problem Statement

### 1.1 The BusinessLabs Consulting Bottleneck
BusinessLabs provides technical advisory, custom engineering, and integration services to organizations running on **Brilliant Directories**, a specialized directory Content Management System (CMS). Operating as high-touch consultants presents three compounding operational challenges:

1. **Knowledge Fragmentation & Grounding Risk**: Brilliant Directories contains thousands of features, webhook events, and configuration workflows documented across hundreds of articles in its Solution Center. Human consultants spend significant time manually searching docs, while generic generative AI tools hallucinate non-existent features or APIs, eroding client trust.
2. **Support Inundation & Quality Inconsistency**: Prospective and existing clients flood the consultancy inbox with inquiries ranging from basic billing setups to complex bidirectional CRM integrations. Manual triage causes response delays, while fully unconstrained AI drafting risks sending unvetted advice or committing to unfeasible timelines.
3. **Scoping Ambiguity & Scope Creep**: Client discovery calls frequently produce vague requirements (e.g., *"make it look modern"*, *"some sort of simple search"*). Without formal ambiguity detection and automated platform capability checks, consultants underbid custom features that already exist natively, or fail to capture 300% scope expansions across transcript revisions.

### 1.2 The Solution
The **BusinessLabs AI Tech Consultant** is an enterprise-grade suite of three interconnected, type-safe autonomous agents designed to eliminate these bottlenecks while enforcing **strict human-in-the-loop oversight**:

```
                                  +---------------------------------------+
                                  |     Brilliant Directories Platform    |
                                  |        Solution Center Sitemap        |
                                  +-------------------+-------------------+
                                                      |
                                                      v
+---------------------------------------------------------------------------------------------------------+
| TASK 1: PYTHON KNOWLEDGE BRAIN (Port 8000)                                                              |
|   - Resumable Async Crawler (1,695 URLs)       - Incremental Change Detection (SQLite)                   |
|   - Multimodal Chunking (Text + Screenshots)   - Persistent Vector Store (ChromaDB)                     |
|   - Grounded RAG with URL Citations            - Strict Truthful Refusal (Threshold: 0.35)              |
|   - Human-in-the-Loop Expert QA Store          - REST API (/api/v1/query) & Accessible Web UI           |
+-----------------------------------------------------+---------------------------------------------------+
                                                      |
                         +----------------------------+----------------------------+
                         | HTTP Query                                              | HTTP Query
                         v                                                         v
+---------------------------------------------------+     +-----------------------------------------------+
| TASK 2: INBOX AGENT (Port 8001)                   |     | TASK 3: MEETING-TO-SCOPE AGENT (Port 8002)    |
|   - 55 Realistic Synthetic Emails (6 Categories)  |     |   - 5-Stage Transcript Analysis Pipeline      |
|   - Intent & Priority Classifier (1-10 Scale)     |     |   - NLP Requirement & Ambiguity Extractor     |
|   - KB-Grounded Drafter (4 Persona Voices)        |     |   - Native Feature Checker (Task 1 KB Client) |
|   - Levenshtein Edit Distance & Similarity Ratio  |     |   - SOW & QA Breakdown ($120/hr Rate Math)    |
|   - Strict 20-Streak Autonomy Gate                |     |   - Client Proposal Human Approval Gate       |
|   - Instant Revocation on Human Edit              |     |   - Transcript Revision Diff & Scope Alerts   |
|   - SQLite Audit Trail & Dashboard UI             |     |   - Web Interface with Version Comparisons    |
+---------------------------------------------------+     +-----------------------------------------------+
```

---

## 2. High-Level Architecture & Core Principles

### 2.1 Core Architectural Principles
1. **Defensive Grounding & Truthful Refusal**: If retrieved context similarity is below `0.35`, the system refuses to speculate, admits lack of evidence, and queues the question for human expert review.
2. **Deterministic Human Boundaries**: Autonomous sending and proposal finalization require explicit human authorization. The AI proposes; the human consultant approves.
3. **Earned Autonomy via Empirical Proof**: Autonomy is never a toggle; it is an earned operational state requiring 20 consecutive unmodified human approvals, automatically revoked upon any manual correction.
4. **Zero-Hallucination Dual-Provider Pattern**: Operates with 100% offline determinism via `MockClaudeProvider` without fake API keys, and dynamically switches to `AnthropicClaudeProvider` when an actual key is supplied.
5. **Strict Type-Safety & Interoperability**: Built with Python 3.11+, Pydantic v2 schemas, FastAPI REST contracts, and asynchronous I/O.

---

## 3. Deep-Dive: Task 1 — Python Knowledge Brain

### 3.1 Sitemap Crawling & Incremental Change Sync
- **Live Sitemap Discovery**: The crawler pulls `https://support.brilliantdirectories.com/support/sitemap.xml`, extracting 1,695 article URLs and metadata (`lastmod`, `changefreq`).
- **SQLite Change Tracking**: The `data/knowledge_brain.db` database maintains an `article_sync` table tracking `url`, `content_hash`, `last_crawled_at`, and `status`. On subsequent runs, it categorizes URLs into:
  - `ADDED`: New articles not previously in the index.
  - `UPDATED`: Existing articles whose hash changed.
  - `REMOVED`: Articles present in SQLite but missing from the latest sitemap.
- **Stale Article Purging**: Removed or modified articles have their previous chunks evicted from ChromaDB (`collection.delete(where={"url": url})`) before re-indexing, preventing index pollution.

### 3.2 Multimodal Extraction & Document Chunking
- **Clean HTML Stripping**: BeautifulSoup strips navigation, headers, footers, scripts, and sidebar clutter, isolating the actual solution text and hierarchical breadcrumbs.
- **Inline Screenshot Preservation**: Directory tutorials rely on visual UI guides. The extractor detects `<img>` tags, records image source URLs, alt text, and captions, and embeds them into the chunk metadata (`images_json`).
- **Semantic Chunking**: Long articles are chunked into 600-word segments with 100-word overlaps. Each chunk preserves the article title, breadcrumb category, and source URL.

### 3.3 Vector Store, Grounding & Human Expert Loop
- **ChromaDB Vector Store**: Persisted under `data/chroma/`, using default cosine similarity spaces.
- **Grounded Answering Engine**: Queries perform similarity searches against the vector store.
  - **Refusal Condition**: If `top_score < 0.35`, the engine returns `is_grounded = False`, `is_sufficient = False`, notes the refusal reason, and inserts the query into the `unanswered_questions` queue in SQLite.
  - **Citation Formatting**: Grounded responses must cite sources in markdown link format: `[Article Title](https://support.brilliantdirectories.com/support/solutions/articles/...)`.
- **Expert Review Loop**: Consultants review the unanswered queue via `/api/v1/feedback/unanswered`, submit verified answers via `/api/v1/feedback/expert-approval`, which are then indexed into ChromaDB under `Community Solutions > Verified Expert Answers`.

---

## 4. Deep-Dive: Task 2 — Inbox Agent

### 4.1 Synthetic Customer Dataset Generation
To evaluate customer handling under realistic conditions, `generator.py` produces 55 varied emails spanning 6 real-world categories:
1. `Billing`: Invoices, card updates, subscription changes.
2. `Technical Support`: SSL errors, DNS mapping, layout issues.
3. `Feature Inquiries`: Webhooks, Zapier, member badges, export limits.
4. `Platform Scoping`: Custom directory builds, multi-tier pricing portals.
5. `Urgent Escalations`: Site outages, payment gateway rejections.
6. `Consulting Inquiries`: Architecture reviews, bespoke API development.

### 4.2 Classification & Persona Drafting
- **Classification Engine**: Parses email subject and body to assign an intent category and priority score (1 = Low to 10 = Critical Outage).
- **Knowledge Brain Client**: Queries Task 1 at `http://127.0.0.1:8000/api/v1/query` with an in-process fallback if the HTTP server is offline.
- **4 Distinct Brand Personas**:
  - `Professional`: Structured, thorough, consultative.
  - `Empathetic`: Reassuring, patient, prioritized for billing disputes and frustrated users.
  - `Concise`: Fast, direct, bullet-pointed.
  - `Executive`: High-level strategic summary with clear timelines and outcomes.

### 4.3 Transparent Edit-Distance & The 20-Streak Autonomy Gate
To prevent uncontrolled autonomous sending, the agent implements a mathematical oversight gate:
- **Levenshtein Metric**: Uses normalized Levenshtein distance:
  $$\text{Similarity Ratio} = \frac{|A| + |B| - \text{Levenshtein}(A, B)}{|A| + |B|}$$
  Similarity is reported as a percentage (0% to 100%) alongside the raw character delta.
- **Strict 20-Streak Rule**:
  - Initial State: `autonomous_mode_enabled = False`.
  - Condition: Consultant must approve **20 consecutive drafts without modifying a single character** (`similarity == 1.0`).
  - Progress: Streak counter increments ($1, 2, \dots, 20$). Once it reaches 20, autonomous mode is granted.
- **Instant Revocation**: If a human consultant modifies even a single character (`similarity < 1.0`) while autonomy is enabled, the system **immediately revokes autonomous mode**, resets the streak to 0, logs a security audit event, and returns to mandatory manual review.

---

## 5. Deep-Dive: Task 3 — Meeting-to-Scope Agent

### 5.1 The 5-Stage Transcript Pipeline
```
[Raw Transcript]
       │
       ▼
[Stage 1: Transcript Extractor]
  - NLP Entity & Intent Extraction
  - Functional / Integration / Non-Functional Classification
  - Ambiguity Detection (Flags vague phrases & formulates clarification questions)
       │
       ▼
[Stage 2: Native Feature Checker]
  - Queries Task 1 Knowledge Brain API
  - Distinguishes native BD capabilities (Stripe, webhooks) from custom full-stack code
       │
       ▼
[Stage 3: Scope Estimator]
  - Work package breakdown
  - Developer hours calculation
  - Mandatory 15% dedicated QA testing package allocation
  - Rate arithmetic ($120.00 / hour standard rate)
       │
       ▼
[Stage 4: Proposal Generator]
  - Formulates structured client proposal
  - Enforces Human Approval Boundary (is_approved_by_human = False)
       │
       ▼
[Stage 5: Transcript Revision Diffing]
  - Compares Transcript V1 vs V2
  - Identifies added & removed requirements
  - Computes Delta Developer Hours & Delta Budget
  - Triggers Scope Creep Alerts (Surge > 20% hours or > $2,000)
```

### 5.2 Native Platform Verification vs. Custom Development
Brilliant Directories provides numerous native features that non-expert developers often rebuild from scratch at immense cost:
- If a client requests **Stripe payments**, the Native Feature Checker identifies this as a native capability: **3 hours** configuration instead of 24 hours custom development, saving the client thousands of dollars.
- If a client requests a **Custom Native Mobile App for the Apple App Store**, the checker queries the Knowledge Brain, detects a lack of native mobile wrappers, and scopes **24 hours** of custom React Native / API development.

### 5.3 Revision Diffing & Scope Creep Protection
Clients frequently introduce significant changes across multiple meetings. The Diff Stage compares revision trees:
- Computes added requirements, removed requirements, delta hours, and delta budget.
- **Surge Detection**: If total project cost increases by >20% or >$2,000, it automatically generates a high-priority **Scope Creep Warning** advising the consultant to issue a formal change order.

---

## 6. Architectural Decisions & Alternatives Considered

| Dimension | Chosen Architecture | Rejected Alternative | Rationale |
|---|---|---|---|
| **Language Stack** | Pure Python 3.11+ / FastAPI / Pydantic v2 | Node.js / TypeScript microservices | Eliminates multi-runtime overhead; delivers unified typing and native ML/NLP libraries. |
| **AI Integration** | Dual-Provider Pattern (Mock + Claude) | Hardcoded Claude API calls | Allows 100% offline development, testing, and continuous integration without fake keys. |
| **Vector Database** | Embedded ChromaDB with SQLite metadata | Cloud Pinecone / Weaviate | Zero cloud infrastructure dependency; enables instant local reproducible evaluation. |
| **Autonomy Control** | 20-streak Levenshtein edit distance gate | Static confidence threshold | Model self-confidence is uncalibrated and prone to hallucinations; human edits provide ground truth. |
| **Scoping QA** | Explicit 15% QA testing work package | Implicit testing inside dev tasks | QA is essential in directory launches; explicit line items guarantee dedicated testing budget. |

---

## 7. Limitations & Production Hardening

1. **Embedding Model Scale**: In offline mode, similarity matching relies on deterministic token-overlap embeddings. In production, upgrading to `text-embedding-3-large` or `Voyage-3` will enhance semantic nuance.
2. **Multi-Tenant Partitioning**: The current SQLite database assumes a single consultancy organization. Production deployment requires tenant-scoped foreign keys (`org_id`).
3. **Real-Time Streaming**: Long crawl jobs and multi-stage proposals execute via background jobs; integrating WebSockets will allow real-time UI progress bars.

---

## 8. Conclusion
The BusinessLabs AI Tech Consultant demonstrates that enterprise AI systems must be designed around **truthfulness, transparency, and human authority**. By anchoring generative agents to verified knowledge stores and enforcing empirical autonomy gates, BusinessLabs can scale consulting capacity while guaranteeing client satisfaction.

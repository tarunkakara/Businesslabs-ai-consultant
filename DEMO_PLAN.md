# 10-Minute Live Demonstration Plan: BusinessLabs AI Tech Consultant

**Target Audience**: Senior AI Engineers & Internship Review Committee  
**Total Duration**: Exactly 10 minutes (plus 5 minutes for technical Q&A)  
**Primary Objective**: Demonstrate an interconnected, robust, explainable multi-agent system with defensive human boundaries.  

---

## 1. Preparation Checklist (Before the Review)

- [ ] Repository cloned in VS Code or terminal.
- [ ] Virtual environment activated (`venv\Scripts\activate`).
- [ ] Dependencies verified (`pip install -r requirements.txt`).
- [ ] Browser tabs prepared:
  - Tab 1: Knowledge Brain UI (`http://127.0.0.1:8000`)
  - Tab 2: Inbox Agent UI (`http://127.0.0.1:8001`)
  - Tab 3: Meeting-to-Scope Agent UI (`http://127.0.0.1:8002`)
- [ ] Terminal window open to workspace root.

---

## 2. Minute-by-Minute Live Review Script

```
+-----------+-----------------------------------------------+-----------------------------------------+
| Minute    | Agenda Item                                   | Key Engineering Value Demonstrated      |
+-----------+-----------------------------------------------+-----------------------------------------+
| 00:00     | Introduction & Monorepo Startup               | Single-command launch, clean contracts  |
| 02:00     | Task 1: Knowledge Brain & Grounded RAG        | Multimodal citations, truthful refusal  |
| 04:00     | Task 2: Inbox Agent & Autonomy Revocation     | 4 personas, Levenshtein edit gate       |
| 06:00     | Task 3: Meeting-to-Scope & Scope Creep Alerts | Native BD check, $120/hr math, diffs    |
| 08:00     | Deliberate Failure Case Demonstration         | Truthful refusal & graceful degradation |
| 09:00     | Architecture Defense & Q&A                    | Why human boundary beats AI confidence  |
+-----------+-----------------------------------------------+-----------------------------------------+
```

### [Minute 00:00 - 02:00] Startup & Architecture Overview
1. **Action**: Open terminal and run:
   ```bash
   python run_all.py
   ```
2. **What to Say**:
   > *"Good afternoon. Today I am presenting the BusinessLabs AI Tech Consultant, an enterprise monorepo built to eliminate knowledge fragmentation, customer inbox delays, and scoping ambiguities for the Brilliant Directories platform. All three microservices are now running locally on ports 8000, 8001, and 8002 via unified Pydantic schemas with zero external cloud dependencies."*
3. **Show**: Point out the terminal banner showing all three services running concurrently.

---

### [Minute 02:00 - 04:00] Task 1: Python Knowledge Brain (Port 8000)
1. **Action**: Switch to browser at `http://127.0.0.1:8000`.
2. **Query 1 (Grounded Retrieval)**: Type *"How do I connect Stripe for recurring member payments?"* and click **Ask Knowledge Brain**.
   - **Show**: Point out the generated answer citing the exact documentation link `[Title](https://support.brilliantdirectories.com/...)` and showing screenshots where applicable.
   - **What to Say**: *"Notice that every factual statement is anchored to indexed Solution Center documentation. We do not generate ungrounded advice."*
3. **Query 2 (Truthful Refusal & Expert Loop)**: Type *"How do I configure quantum blockchain staking on Brilliant Directories?"*
   - **Show**: The system refuses to answer, explicitly notes confidence is below the `0.35` threshold, and queues the question into the Unanswered Questions list.
   - **Action**: In the Expert Review Panel, type a verified answer: *"Brilliant Directories does not support blockchain staking; it uses standard payment gateways like Stripe and PayPal."* Click **Approve & Index Answer**.
   - **Show**: Re-ask the question to prove immediate vector store grounding.

---

### [Minute 04:00 - 06:00] Task 2: Inbox Agent (Port 8001)
1. **Action**: Switch to browser at `http://127.0.0.1:8001`.
2. **Show**: Click **Generate 50+ Inquiries** to populate the inbox across all 6 categories.
3. **Select Email**: Select an urgent outage email.
   - **Show**: Intent classified as `urgent_escalation` with a Priority Score of `10/10`.
4. **Draft Personas**: Switch between `empathetic` and `concise` tones to show how drafting adapts dynamically.
5. **Autonomy & Revocation Demo**:
   - **Show**: Streak counter at `0/20`, `Autonomous Sending: Disabled`.
   - **Action**: Click **Approve & Send** without editing. Notice the streak increments to `1/20`.
   - **Action**: In the next draft, modify one word in the text box and click **Approve & Send**.
   - **Show**: Notice the Levenshtein similarity displays `98.2%` with character deltas, and the streak resets to `0/20`.
   - **What to Say**: *"Autonomy is an earned operational state requiring 20 consecutive unmodified human approvals. Any human correction immediately resets the streak and revokes autonomous sending."*

---

### [Minute 06:00 - 08:00] Task 3: Meeting-to-Scope Agent (Port 8002)
1. **Action**: Switch to browser at `http://127.0.0.1:8002`.
2. **Action**: Paste `SAMPLE_TRANSCRIPT_V1` and click **Analyze & Generate Scope**:
   ```
   Sarah (Consultant): Welcome John. Let's discuss your directory requirements.
   John (Client): We need to accept member payments via Stripe.
   John: We also want webhooks to notify our CRM on signup.
   John: We need a custom bidirectional integration with our patient booking database.
   John: We must launch within 4 weeks. Our budget is around $8,000.
   John: Make sure the search has some sort of modern look.
   ```
3. **Show**:
   - **Ambiguity Warning**: Highlighted badge on *"some sort of modern look"* with an automated follow-up question.
   - **Platform Savings**: Stripe and Webhooks identified as native platform features (scoped at only 3.0 hours config), saving the client thousands of dollars.
   - **Work Breakdown & QA**: Patient booking database scoped at 24.0 hours custom development, plus an explicit 15% dedicated QA testing package (4.05 hours) at the standard $120.00/hr rate.
   - **Human Sign-off Boundary**: Proposal status shows `Status: DRAFT (Pending Human Sign-off)`.
4. **Revision Diff & Scope Creep**:
   - **Action**: Paste `SAMPLE_TRANSCRIPT_V2` (where the client adds a native iOS/Android mobile app and multi-currency billing) and click **Compare Revisions**.
   - **Show**: The **Scope Creep Warning** banner alerts that project costs surged by >20% (+$9,888.00), advising the consultant to issue a formal change order before proceeding.

---

### [Minute 08:00 - 09:00] Deliberate Failure Case: Graceful Degradation
1. **The Scenario**: An interviewer asks: *"What happens if the Task 1 Knowledge Brain API server is down or unreachable when the Inbox Agent or Scope Agent needs it?"*
2. **Action / Explanation**:
   - In `task2_inbox_agent/app/kb_client.py`, the HTTP client contains an automated **in-process RAG fallback**.
   - If the remote server times out, the client automatically instantiates local SQLite and ChromaDB instances to fulfill the query with zero user disruption.
   - If the query falls outside any indexed topic, the system **truthfully refuses** rather than hallucinating plausible-sounding answers.
3. **What to Say**: *"Defensive AI engineering means systems must be designed for partial network failures and out-of-distribution prompts. We degrade gracefully to local offline search and truthful refusal."*

---

### [Minute 09:00 - 10:00] Defense & Engineering Q&A

#### Question 1: *"Why did you use Levenshtein distance instead of having Claude rate its own confidence?"*
* **Defense**: *"LLM self-confidence is notoriously uncalibrated; models frequently assign high confidence to hallucinations. Measuring the exact edit distance between what the AI generated and what the human consultant accepted provides an empirical, auditable ground truth."*

#### Question 2: *"Why are all three tasks in Python instead of TypeScript/Node.js?"*
* **Defense**: *"Task 1 required advanced BeautifulSoup HTML scraping, chunking, and ChromaDB embeddings. By standardizing on Python with FastAPI and Pydantic v2 across all three tasks, we achieved end-to-end type safety, zero multi-runtime operational friction, and 100% test isolation across all 60 evaluations."*

#### Question 3: *"How does this transition from offline mock mode to production Claude?"*
* **Defense**: *"Via the `AIProviderInterface`. By default, it runs with `MockClaudeProvider` without fake keys. The moment an engineer sets `ANTHROPIC_API_KEY=sk-ant-...` in `.env`, the factory automatically routes requests to Anthropic's Claude 3.5 Sonnet API with identical schema validation."*

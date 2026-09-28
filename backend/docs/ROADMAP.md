# Roadmap

Timeline: Aug 24 - early October. Pivoted from the original confused-deputy
/ provenance design to a Saga (compensation-based rollback) engine for
MCP tool chains - see project notes for why. Backend is now complete;
frontend (Stage 6) is the only remaining stage.

---

## Stage 0 - Environment & Repo Skeleton [COMPLETE]
- [x] Folder structure, requirements.txt, git-ready layout
- [x] pytest configured and passing (4/4 smoke tests)
- [ ] Ollama installed and verified locally - do this on your own machine,
      not verifiable in a sandbox without a GPU

---

## Stage 1-4 - Saga Engine + Real MCP Wiring [COMPLETE]
Built the full compensation-based rollback engine:
- `src/mcp_servers/git_server.py`, `db_server.py` - real MCP servers,
  genuine subprocess/SQLite operations and genuine failures, nothing mocked
- `src/orchestration/mcp_client.py` - `MultiServerToolInvoker`, real MCP
  client wiring, routes tool names to the right server
- `src/saga/` - `models.py`, `registry.py`, `tracker.py`,
  `failure_detection.py`, `executor.py`: the full saga engine, covering
  all four reviewed blind spots:
  1. Compensation can fail -> `DEAD_LETTER` state + bounded retries
  2. Non-reversible actions -> `PIVOT` category halts rollback at the boundary
  3. MCP-boundary placement -> zero framework imports, works with any agent framework
  4. Protocol vs semantic failures -> dual-layer `FailureDetector`

**Verified:** `scripts/run_saga_demo.py` runs a real saga against a real
git repo + SQLite DB, fails genuinely, rolls back genuinely (confirmed:
identical branch/commit state before and after). 16 tests passing
(`test_stage4_saga.py`, `test_stage4b_real_mcp_integration.py`).

---

## Stage 5 - LLM Planning + Evaluation Harness [COMPLETE - backend side]
- `src/llm/ollama_client.py` - thin wrapper over a local Ollama server
- `src/agents/planning_agent.py` - LLM turns a task description into a
  validated saga plan; hallucinated/unregistered tools are rejected
  before they ever reach `SagaExecutor`
- Audit logging wired directly into `SagaExecutor` (every forward call
  and every compensation attempt is persisted to SQLite)
- `src/evaluation/metrics.py` - rollback success rate, dead-letter rate,
  false positive rate, avg latency
- `scripts/run_saga_demo_llm.py` - full LLM-driven demo (task description
  in, LLM plan out, real saga execution) - **requires a real Ollama
  install to run, not verified in this sandbox**
- `scripts/run_evaluation.py` - runs repeated real sagas and prints the
  results table for your report

**Verified in sandbox:** `scripts/run_evaluation.py` executed 20 real
sagas (10 legitimate, 10 induced-failure) against real git/SQLite:
100% rollback success rate, 0% dead-letter rate, 0% false positive rate.
26/26 tests passing across the whole repo.

**Not yet verified - do this next, on your machine:**
- [ ] Run `scripts/run_saga_demo_llm.py` with a real Ollama model and
      confirm the LLM-generated plan executes and rolls back correctly
- [ ] Re-run `scripts/run_evaluation.py` with `RUNS_PER_CONDITION` raised
      to 15+ for your final report numbers

---

## Stage 6 - Dashboard / Demo Frontend [ONLY REMAINING STAGE]
**Goal:** A visual layer over your existing audit logs, built last on purpose.

Tasks:
- [ ] Simple live view: request list, saga status per run, allow/block/
      compensated status, filter by agent
- [ ] "Trigger induced failure" button for live demo purposes
- [ ] Read directly from the audit trail SQLite DB already produced by
      Stage 5 (no new backend logic needed here)

**Acceptance criteria:** You can run a live saga from the UI and watch
a genuine failure roll back on screen, in real time, in under 5 minutes.

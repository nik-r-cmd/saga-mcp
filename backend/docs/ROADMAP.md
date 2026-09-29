# Roadmap

Timeline: Aug 24 - early October. Pivoted from the original confused-deputy
/ provenance design to a Saga (compensation-based rollback) engine for
MCP tool chains - see project notes for why. The engine, local agent,
Ollama path, control plane, and React Flow dashboard are implemented.
The paper-sized 20-trial benchmark rerun remains an author-run step.

---

## Stage 0 - Environment & Repo Skeleton [COMPLETE]
- [x] Folder structure, requirements.txt, git-ready layout
- [x] pytest configured and passing (4/4 smoke tests)
- [x] Ollama installed locally; `llama3.2:3b` completed a live generation
  and an LLM-planned Git + SQLite success/rollback smoke run

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

**Verified:** the LLM-generated plan executed through independent Git and
SQLite MCP servers; a success plan completed and an empty-seed plan rolled
back the real branch. The agent task path now builds prompts from stored MCP
descriptions/schemas and routes approved tools to their configured servers.

---

## Stage 6 - Dashboard / Demo Frontend [IMPLEMENTED]
**Goal:** A live policy and execution view over the local agent and Saga engine.

Implemented:
- [x] Tool discovery, inverse suggestions, confidence, approval, and saved
  server/schema/argument-mapping configuration
- [x] Authenticated local-agent status, LLM task dispatch, and live events
- [x] Interactive React Flow event canvas and saga status counters
- [x] Deterministic real-MCP rollback demo for reviewer figures

**Verified acceptance:** the backend integration test runs a real Git branch
creation, real SQLite empty-seed failure, and real LIFO branch compensation;
the normal local-agent path was also exercised with live Ollama inference.

## Final Paper Benchmark [AUTHOR RUN]
- [x] Harness defaults to 20 trials and exact 0/5/10 fault allocations
  per condition; uses a paired schedule and monotonic timing
- [x] State oracle checks both Git branches and SQLite records
- [x] Read-only latency is excluded from executable-workflow comparison
- [x] Raw schedules and per-trial results are saved separately under
  `benchmark_output/paper_final/` without overwriting earlier artifacts
- [x] Four-trial smoke run passed all conditions and verified metrics/output
- [ ] Run the full `python -m scripts.run_benchmark` and replace the paper's
  TBD result cells with values from `paper_final/benchmark_results.json`

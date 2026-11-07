# Roadmap

Timeline: Aug 24 - early October. Pivoted from the original confused-deputy
/ provenance design to a Saga (compensation-based rollback) engine for
MCP tool chains - see project notes for why. The engine, local agent, Ollama path, control plane, and React Flow
dashboard are implemented. The checked-in `paper_final` benchmark artifact
contains 100 trials per condition and confidence intervals calculated
from its saved trial-level results.

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
The backend tests cover the saga engine, real MCP integration, route
behavior, task results, and per-user approval isolation.

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
- [x] User-scoped approval registry and API route isolation tests

**Verified acceptance:** the backend integration test runs a real Git branch
creation, real SQLite empty-seed failure, and real LIFO branch compensation;
the normal local-agent path was also exercised with live Ollama inference.

## Paper Benchmark

The saved `benchmark_output/paper_final/benchmark_results.json` records
100 trials per condition (900 total evaluations: 600 real MCP workflow
executions and 300 read-only policy evaluations). Fault schedules are
paired across systems and have exact 0%, 25%, and 50% allocations. The
state oracle checks both Git branches and SQLite records; read-only
policy evaluations are excluded from execution-latency comparisons.

Recorded point estimates:

| Fault rate | System | State consistency | Task completion | Mean latency |
|---|---|---:|---:|---:|
| 0% | Unprotected | 100% | 100% | 3556 ms |
| 0% | Read-Only Gatekeeper | 100% | 0% | n/a |
| 0% | Saga-MCP | 100% | 100% | 3506 ms |
| 25% | Unprotected | 75% | 75% | 4098 ms |
| 25% | Read-Only Gatekeeper | 100% | 0% | n/a |
| 25% | Saga-MCP | 100% | 75% | 3617 ms |
| 50% | Unprotected | 50% | 50% | 4211 ms |
| 50% | Read-Only Gatekeeper | 100% | 0% | n/a |
| 50% | Saga-MCP | 100% | 50% | 3844 ms |

`scripts/run_benchmark.py` reports 95% Wilson intervals for success rates
and normal-approximation intervals for mean latency. The summary and
figures were regenerated from the saved trial-level results without
rerunning the workflows; the original point estimates are unchanged. To
reproduce the experiment and refresh the data and figures, run from `backend`:

The benchmark disables LLM inference, so it evaluates transactional
execution and recovery rather than plan quality. The read-only gatekeeper
is a policy-level reference that rejects the predefined mutating workflow;
it is not a separately implemented MCP product and its latency is not
comparable. Confidence intervals are descriptive per-condition intervals,
not paired-difference hypothesis tests.

```powershell
$env:BENCH_TRIALS = "100"
..\.venv\Scripts\python.exe -m scripts.run_benchmark
```

The harness defaults to 20 trials per condition for a shorter local run.
The paper PDF itself was not edited; use this artifact when writing the
results and interpretation.

## Real-world architecture direction

Keep the Saga engine as the deterministic policy and recovery layer.
The user-local agent should remain the MCP broker: it owns the approved
server configurations, validates every planned tool against the registry,
and invokes MCP tools through one controlled boundary. A chain of
specialized agents can improve planning, risk review, and operator
summaries, but it cannot enforce recovery if clients can invoke MCP tools
around that chain. Do not treat agent-to-agent prompts as a security or
transaction boundary.

For production, prioritize a durable saga journal (including step inputs,
outputs, idempotency keys, and uncertain outcomes), resumable recovery,
explicit human handling for ambiguous side effects, and shared storage /
WebSocket delivery before horizontal scaling. A managed daemon or MCP
proxy becomes valuable when the product must mediate unmodified clients;
for the current user-local workflow, a single local broker is the simpler
and more enforceable next step.

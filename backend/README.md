# Compensation-Based Rollback Orchestration for Multi-Step Tool Execution in MCP-Based Agentic Pipelines

## What this project does

AI agents now perform real multi-step actions through tools using the
Model Context Protocol (MCP) - creating git branches, committing files,
seeding databases, etc. If a step partway through a chain genuinely
fails, everything before it already happened, and nothing undoes it
automatically. This project builds a Saga-pattern rollback engine that
sits at the MCP boundary: every mutating tool call is paired with a
registered compensating (undo) action, and the moment a real failure is
detected, the engine walks backward and undoes everything that already
succeeded - handling the hard edge cases (compensation itself failing,
irreversible actions, silent semantic failures) rather than assuming a
rollback is always clean.

## Architecture

```
Task description (plain English)
        |
        v
PlanningAgent (Ollama LLM decides the step sequence)
        |
        v
SagaExecutor
  |-- CompensationRegistry   (which tool undoes which)
  |-- FailureDetector        (protocol AND semantic failure checks)
  |-- SagaTracker            (execution graph, rollback sequence)
        |
        v
MultiServerToolInvoker  (real MCP client, routes tool name -> server)
        |
        v
Real MCP servers: git_server.py, db_server.py
        |
        v
AuditLog (SQLite)  -->  evaluation/metrics.py  -->  results table
```

## Status: backend complete, frontend remaining

Every stage through Stage 5 (LLM planning + evaluation harness) is built
and tested. Only Stage 6 (the dashboard UI) is left - see
`docs/ROADMAP.md` for full detail on what was built and why.

---

## Setup (do this once)

```bash
# 1. Confirm git is installed
git --version

# 2. Confirm Python 3.10+
python3 --version

# 3. Create and activate a virtual environment
cd mcp-privilege-verification
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Install Ollama (only needed for the LLM-driven demo, not the
#    core saga tests) - download from https://ollama.com, then:
ollama pull llama3.1:8b            # or a smaller model if VRAM is tight
```

---

## Step-by-step: what to run, in order

### 1. Run the full test suite (should take under 15 seconds)
```bash
pytest -v
```
Expect: **26 passed**. If anything fails, stop here and fix it before
moving on - nothing downstream should be trusted if this doesn't pass.

### 2. Run the deterministic saga demo (no LLM needed)
```bash
python -m scripts.run_saga_demo
```
This creates a throwaway git repo + SQLite DB, runs a hardcoded 3-step
saga where the last step genuinely fails, and shows the real rollback.
Confirm the "before" and "after" repo state printed at the end are
identical.

### 3. Run the evaluation harness (produces your results table)
```bash
python -m scripts.run_evaluation
```
Runs 10 legitimate + 10 induced-failure sagas against real git/SQLite
and prints a metrics table: rollback success rate, dead-letter rate,
false positive rate, average latency. Open
`scripts/run_evaluation.py` and raise `RUNS_PER_CONDITION` to 15+ before
generating the final numbers for your report.

### 4. Run the LLM-driven demo (requires Ollama running)
```bash
python -m scripts.run_saga_demo_llm
```
Make sure Ollama is running in the background first (it usually starts
automatically after install; if not, run `ollama serve` in a separate
terminal). This asks the local model to turn a plain-English task
description into a saga plan, validates the plan, then executes it for
real. If the model produces a bad plan, the script will tell you exactly
why and refuse to execute it rather than guessing.

If this is the first time you're running it and it fails, check:
- Is `ollama serve` running? (`ollama list` should show your pulled model)
- Does `src/config.py`'s `OllamaConfig.model` match the model name you
  pulled? Edit that one line if not.

### 5. Inspect the audit trail directly (optional, useful for your report)
After running step 3 or 4, a `logs/audit_trail.sqlite3` file exists with
every tool call and decision logged. You can inspect it with any SQLite
browser, or:
```bash
python -c "
from src.logging_utils.audit_log import AuditLog
from src.config import AUDIT_DB_PATH
log = AuditLog(db_path=AUDIT_DB_PATH)
for row in log.all_entries():
    print(dict(row))
"
```

---

## Repo structure

```
src/
  config.py              Central config: Ollama model, paths
  agents/
    base_agent.py         Shared agent interface
    planning_agent.py      LLM turns a task into a validated saga plan
  llm/
    ollama_client.py        Thin wrapper over the local Ollama server
  mcp_servers/
    git_server.py            Real MCP server: git operations
    db_server.py             Real MCP server: SQLite operations
  orchestration/
    mcp_client.py             Real MCP client, routes tools to servers
  saga/
    models.py                  Core data types (ActionCategory, SagaStatus, etc.)
    registry.py                  CompensationRegistry - tool -> undo mapping
    tracker.py                    Execution graph + rollback sequencing
    failure_detection.py           Dual-layer (protocol + semantic) failure checks
    executor.py                     Orchestrates forward execution + rollback
  evaluation/
    metrics.py                       Computes the results table
  logging_utils/
    audit_log.py                       Structured SQLite audit trail
tests/                    One test file per stage, mirrors src/ layout
scripts/
  run_saga_demo.py          Deterministic demo, no LLM
  run_saga_demo_llm.py        Real LLM-driven demo
  run_evaluation.py             Evaluation harness, produces results table
docs/
  ROADMAP.md                Full stage-by-stage build history and status
```

# Formalization and Threat Model

This document formalizes what is actually implemented in this repository
(`src/saga/`, `src/discovery/`, `src/api/`). It intentionally does NOT
describe unbuilt future work (a transparent stdio/SSE interception
daemon, live WebSocket telemetry) as though it were part of the current
system - see docs/ROADMAP.md for what is explicitly deferred and why.

## 1. Saga Formalization

A saga execution is a sequence of steps:

    S = (T_1, T_2, ..., T_n)

where each step T_i is a tuple:

    T_i = (tool_i, args_i, category_i, C_i)

- tool_i: the forward MCP tool invoked
- args_i: the JSON-serializable argument payload for tool_i
- category_i in {COMPENSABLE, PIVOT}, declared at registration time,
  never inferred at execution time
- C_i: the compensating action for T_i, defined only when
  category_i = COMPENSABLE, as a pair:

    C_i = (compensating_tool_i, map_i)

  where map_i: args_i -> comp_args_i is an explicit argument mapping
  function (compensation_arg_mapper in ActionSpec). This is a
  deliberate correction over a naive design that reuses args_i directly
  for the compensating call - that naive approach only appears correct
  because MCP tolerates unexpected extra arguments rather than
  rejecting them, which is accidental leniency, not a guarantee.

A step's outcome is one of:

    StepStatus in {SUCCEEDED, FAILED, COMPENSATED, COMPENSATION_FAILED}

A saga's terminal state is one of:

    SagaStatus in {COMPLETED, ROLLED_BACK, PARTIALLY_COMMITTED, DEAD_LETTER}

## 2. Algorithm 1: Saga Execution with Bounded LIFO Compensation

```
Input: sequence of (tool, args) pairs S = (T_1, ..., T_n)
Input: CompensationRegistry R (maps tool -> ActionSpec)
Output: SagaStatus, per-step StepStatus

executed := []                      # LIFO stack of succeeded steps

for T_i in S:
    spec := R.get(tool_i)           # raises if tool_i unregistered -
                                     # a step this saga does not know
                                     # how to undo must never execute
    response, error := invoke(tool_i, args_i)
    is_failure, reason := detect_failure(response, error)
                                     # checks BOTH protocol-level errors
                                     # (exceptions/JSON-RPC errors) AND
                                     # semantic-level errors (a payload
                                     # that returns 200/success but
                                     # encodes {"status": "error"})

    if is_failure:
        mark T_i as FAILED
        goto ROLLBACK
    else:
        mark T_i as SUCCEEDED
        executed.push(T_i)

return COMPLETED

ROLLBACK:
    any_compensation_failed := false
    hit_pivot := false

    for T_j in reversed(executed):          # LIFO order
        spec_j := R.get(tool_j)
        if spec_j.category = PIVOT:
            hit_pivot := true
            break                            # rollback CANNOT proceed
                                              # past an irreversible step

        comp_args := spec_j.map_j(args_j)    # explicit mapping, not
                                              # naive reuse of args_j
        succeeded := false
        for attempt in 1..spec_j.max_retries:
            response, error := invoke(spec_j.compensating_tool, comp_args)
            is_failure, _ := detect_failure(response, error)
            if not is_failure:
                succeeded := true
                break

        if succeeded:
            mark T_j as COMPENSATED
        else:
            mark T_j as COMPENSATION_FAILED
            any_compensation_failed := true
            write_dead_letter(T_j)           # persisted, not silently
                                              # dropped - requires human

    if any_compensation_failed:
        return DEAD_LETTER
    elif hit_pivot:
        return PARTIALLY_COMMITTED
    else:
        return ROLLED_BACK
```

Complexity: O(n) forward passes, O(n) worst-case rollback passes, each
compensation bounded by max_retries (a constant) - overall O(n) in the
number of steps, independent of failure location.

## 3. Threat Model

### 3.1 System boundary

The saga engine (`SagaExecutor`) mediates every tool invocation an
agent makes, provided the agent's tool calls are routed through a
`ToolInvoker` this project controls (see `MultiServerToolInvoker`). It
does not currently intercept traffic between arbitrary third-party MCP
clients (e.g. Claude Desktop, Cursor) and their configured servers
transparently - that would require a host-side proxy daemon, which is
explicitly out of scope for the current implementation (see
docs/ROADMAP.md, "explicitly not building"). The threat model below is
scoped accordingly: it describes what the system guarantees for tool
calls it mediates, not a claim of universal interception.

### 3.2 Attacker / failure model

Two related but distinct classes are in scope:

1. **Genuine environmental failure** - a tool call fails for real
   operational reasons (disk full, network partition, invalid input,
   concurrent modification). This is the primary class this project's
   evaluation (`scripts/run_benchmark.py`) tests against, via induced
   real failures (e.g. `seed_database` given empty input, which the
   tool genuinely rejects).
2. **Partially adversarial tool behavior** - a registered tool's
   compensating action itself fails or behaves inconsistently. This is
   handled by the DEAD_LETTER + retry mechanism (Section 2), which
   assumes compensations *can* be adversarial or unreliable and does
   not assume rollback always succeeds.

Out of scope for the current implementation (explicitly, not silently):
prompt injection into tool descriptions, malicious MCP server
metadata poisoning, and cross-server tool shadowing. These are real,
documented MCP threat classes (see literature review), but this
project's contribution is state-consistency under failure via
compensation, not adversarial-input detection - conflating the two
would overstate what is tested in Section VI's evaluation.

### 3.3 Trust assumptions

- The `CompensationRegistry` is populated only through explicit human
  approval (`src/discovery/registry_store.py` - `RegistryStore.approve`),
  never automatically from `AutoPairSuggester`'s output directly. This
  is a deliberate trust boundary: an incorrect automatic reversibility
  guess must not become an enforced policy without a human confirming
  it.
- An unregistered tool cannot execute within a saga at all
  (`UnregisteredToolError`) - the system fails closed, not open, when
  it does not know how to undo an action.
- A tool defaulting to `PIVOT` classification (the `AutoPairSuggester`'s
  behavior when confidence is low) is the safe default: an
  unrecognized action is assumed irreversible until a human says
  otherwise, never the reverse.

### 3.4 What this project's evaluation actually measures

`scripts/run_benchmark.py` measures **state consistency** (did the
system end in a state consistent with either full success or full
rollback, with no orphaned partial state) and **task completion rate**
under real, induced failures, compared against two baselines
(Unprotected execution, and a Read-Only Gatekeeper mirroring
MCP-Secure's L2 enforcement). It does not measure resistance to
adversarial tool metadata or prompt injection - see 3.2 for why that is
explicitly out of scope, not an oversight.

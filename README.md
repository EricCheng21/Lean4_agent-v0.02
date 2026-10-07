# Formal Reasoning Agent
## From Primitive Tactic Search to Abstract Reasoning Space

> **Project status:** Persistent Lean state driver / v0.2-alpha  
> **Primary goal:** Learn reusable reasoning operators from many Lean proofs and search experiences in one mathematical subdomain, then test whether those operators compose to solve unseen theorems.

The repository contains a minimal Lean-owned vertical slice. It searches a
finite propositional-logic domain, asks Lean to accept or reject every attempted
transition, records valid and invalid attempts, reconstructs a proof trace, and
asks a fresh Lean process to verify the final generated theorem. It intentionally
does not depend on Mathlib yet.

## Run the scaffold

Requirements: Python 3.9+ and Lean 4 on `PATH`. No third-party Python package is
required.

```bash
PYTHONPATH=src python3 -m formal_agent demo --environment lean --output runs/demo
PYTHONPATH=src python3 -m unittest discover -s tests -v
cd lean
lake build FormalAgent formal_agent_driver
```

`--environment lean` is the default. Use `--environment toy` to run the fast
Python reference environment instead.

Each problem writes:

- `search_graph.json`: all states and attempted actions within the budget;
- `proof.lean`: the reconstructed proof replayed by Lean;
- `summary.json`: search metrics and verification status.

In the default environment, one long-lived Lean JSONL driver owns process-local
tactic-state handles. For each candidate it restores the chosen parent state,
applies one typed action with tactic recovery disabled, saves the child state,
and returns every remaining goal. The complete action trace is retained as the
proof certificate and as a conservative state identity; it is also the fallback
if a driver restart invalidates its in-memory handles. The Python transition
system remains available only as a fast reference domain. A complete solution
is independently replayed again in a fresh Lean process without `sorry`; only
that clean run counts as a solved theorem.

---

## 1. Motivation

Most current automated theorem proving systems that use neural models follow a pattern like:

\[
\text{formal proof state}
\rightarrow
\text{LLM / policy predicts tactic}
\rightarrow
\text{search}
\rightarrow
\text{formal verifier}
\]

This works, but it leaves two major inefficiencies:

1. The search space is often the **primitive tactic space**, which can be extremely large.
2. The model often learns **which tactic to try next**, rather than learning reusable high-level reasoning structures.

Our project starts from a different question:

> Can a formal reasoning agent reduce the effective reasoning/search space by learning reusable abstract reasoning structures from previous formal proofs?

The long-term objective is not to remove formal verification. Lean remains the authority on correctness.

The objective is to move the source of intelligence from:

\[
P(\text{next tactic}\mid \text{proof state})
\]

toward:

\[
\text{formal structure}
\rightarrow
\text{abstract reasoning operator}
\rightarrow
\text{subgoal transformation}
\rightarrow
\text{formal refinement}
\]

---

# 2. Evolution of the Idea

## Stage 1 — MCTS-centered thinking

The original idea was to improve theorem-proving efficiency using MCTS.

However, formal proof search differs from games in several ways:

- transitions are deterministic;
- rewards are sparse;
- one action can generate multiple subgoals;
- all generated subgoals may need to be solved;
- many paths can reach the same formal state.

Therefore ordinary tree-based MCTS is not necessarily the natural representation.

This led to a shift from:

\[
\text{MCTS optimization}
\]

to:

\[
\text{formal search optimization}
\]

---

## Stage 2 — AND-OR proof structure

A formal proof naturally has AND-OR structure.

For a goal \(G\), several proof methods may be possible:

\[
a_1 \lor a_2 \lor a_3
\]

so the goal behaves like an **OR node**.

But one tactic may produce several subgoals:

\[
G
\xrightarrow{a}
\{G_1,G_2,G_3\}
\]

and all must be solved:

\[
G_1\land G_2\land G_3
\]

so the tactic expansion behaves like an **AND node**.

At the implementation boundary, v0.0/v0.1 keeps the full list of open goals in
one `SearchState`; the AND requirement is therefore encoded inside the state.
An explicit bipartite AND-OR representation can be derived later, but goals that
share metavariables must not be treated as independent tasks.

This suggests that formal reasoning is better represented as:

\[
\boxed{\text{AND-OR proof graph}}
\]

rather than a conventional Monte Carlo tree.

---

## Stage 3 — Tree to state graph

Different reasoning paths can lead to the same proof state.

Therefore repeated states should be merged:

\[
s_1 \rightarrow s_3
\]

and

\[
s_2 \rightarrow s_3
\]

should share the same node \(s_3\).

The semantic search structure is therefore a directed **state graph**:

\[
\boxed{\text{AND-OR state graph}}
\]

Reversible operations can create cycles, so it is not generally a DAG. A
separate first-discovery tree is retained for proof reconstruction. This
motivates:

- canonicalization of formal states;
- state hashing;
- transposition tables;
- reuse of previously explored subproblems.

---

## Stage 4 — Search-space reduction is more important than search algorithm choice

The main bottleneck may not be whether we use:

- MCTS,
- BFS,
- best-first search,
- AO*,
- proof-number search,

but the size of the action space:

\[
|\mathcal A(s)|
\]

If a state has tens of thousands of possible theorem/tactic actions, changing the search algorithm alone cannot solve the problem.

So the research focus shifted toward:

\[
\boxed{
\text{reduce the effective reasoning space before search}
}
\]

---

## Stage 5 — From "solution space" to "abstract reasoning space"

A naive approach would store solved proofs and retrieve similar proofs.

That risks becoming a large solution database:

\[
\{\text{proof}_1,\text{proof}_2,\dots\}
\]

which may fail on genuinely novel problems.

Instead, the goal is to extract reusable structure:

\[
\{\text{proof/search graphs}\}
\rightarrow
\{\text{abstract reasoning operators}\}
\]

The agent should learn methods rather than answers.

---

# 3. Core Concept: Search State, Graph, and Proof Trace

## State semantics

The atomic search node is the **complete proof state**, not one isolated goal:

\[
s=(M, [\Gamma_1\vdash G_1,\ldots,\Gamma_n\vdash G_n], E)
\]

where \(M\) is the metavariable/constraint context and \(E\) identifies the
Lean environment. If an action splits one goal into two, it still produces one
successor state containing both open goals. Both must eventually disappear
before the state is solved. This keeps shared metavariables and dependencies
between subgoals intact.

The Lean adapter implements the same whole-state contract and obtains its
goal observations from Lean. Its current state key conservatively includes the
root theorem, environment fingerprint, and complete action trace. Pretty-printed
goal text and expression reprs are stored for inspection, but are not treated as
a sound semantic state key.

## Search graph

The complete experience is a directed graph and may contain cycles. A node is a
complete state. An attempt edge stores:

```python
class AttemptEdge:
    source_id
    action              # typed operation and parameters
    valid
    target_id           # absent for invalid attempts
    error_code
    cost
    elapsed_ms
    generated_goal_count
```

The graph stores successful transitions, invalid actions, duplicates, dead ends,
and expensive branches. The search engine separately stores one chosen parent
edge per discovered state; that acyclic witness is used to reconstruct a proof.

State identity must conservatively include all correctness-relevant data:

- every open goal and local context;
- metavariable assignments and constraints;
- universe and instance information where relevant;
- imports plus Lean/Mathlib version fingerprint.

When equivalence is uncertain, v0.1 must keep two states separate. Missing a
merge costs performance; merging non-equivalent states can make search and proof
reconstruction unsound.

## Verification boundary

A cached state transition is not itself a proof certificate. The output of a
successful run is a replayable action trace or proof term. It must be replayed
in a fresh, version-pinned Lean environment. Only that final kernel-accepted
artifact is counted as a solved theorem.

This state graph is the agent's **concrete experience**. Later stages may extract
acyclic successful subgraphs or other parameterized schemas from it; the raw
search structure itself is not required to be a DAG.

---

# 4. Definition Space

We introduce an intermediate representation called the **Definition Space**.

Let a formal Lean state contain all open goals and their shared constraints:

\[
s=(M,[\Gamma_1\vdash G_1,\ldots,\Gamma_n\vdash G_n],E)
\]

We define an abstraction map:

\[
\phi_D : X \rightarrow D
\]

where:

- \(X\) is the concrete Lean state space;
- \(D\) is a structural / definition space.

The definition space removes irrelevant concrete details and preserves properties relevant to reasoning.

Possible levels:

### \(D_0\): syntax
- multiplication;
- inverse;
- identity;
- equality;
- composition.

### \(D_1\): mathematical object
- group element;
- integer;
- polynomial;
- sequence;
- set.

### \(D_2\): structural property
- associative;
- invertible;
- factorizable;
- symmetric;
- recursive.

### \(D_3\): reasoning affordance
- rewrite by identity;
- cancel inverse;
- reassociate;
- split cases;
- introduce auxiliary lemma;
- apply induction.

The long-term objective is:

\[
\text{Lean state}
\rightarrow
\text{Definition Space}
\rightarrow
\text{Abstract Reasoning Space}
\]

---

# 5. Abstract Reasoning Operator

A reasoning operator should **not** simply be a fixed sequence of Lean tactics.

Instead, it should be a parameterized reasoning-graph schema. A learned operator
may be acyclic, but acyclicity is not assumed for the raw experience graph.

A conceptual definition is:

\[
O=(P,V,E,C)
\]

where:

- \(P\): applicability / precondition pattern;
- \(V\): abstract reasoning states;
- \(E\): reasoning transformations;
- \(C\): structural constraints.

An operator is learned from multiple concrete experience graphs:

\[
D_1,D_2,\dots,D_n
\rightarrow
O
\]

At inference time:

\[
s
\xrightarrow{\phi_D}
z
\xrightarrow{\text{match}}
O
\xrightarrow{\text{instantiate}}
O[s]
\xrightarrow{\text{formal refinement}}
s'
\]

The final result is always verified by Lean.

---

# 6. Why this should not become "solution accumulation"

The project must distinguish:

## Memorization

\[
\text{new problem}
\rightarrow
\text{retrieve old proof}
\]

from:

## Compositional reasoning

\[
O_A,\ O_B,\ O_C
\rightarrow
O_A\circ O_C\circ O_B
\]

where the composition may never have appeared in the training corpus.

Therefore an important future evaluation is:

> Can the agent solve problems whose required **operator composition** was not observed during training?

This is the main defense against merely building a larger proof database.

---

# 7. Search-Space Reduction

There are two kinds of pruning.

## Hard pruning

Actions that are formally impossible can be removed:

\[
\operatorname{valid}(a,s)=0
\Rightarrow
a\notin \mathcal A(s)
\]

Examples:

- type mismatch;
- unification failure;
- invalid theorem application.

## Soft pruning

Actions that appear unlikely should **not** necessarily be permanently removed.

Instead, search can expand gradually:

\[
A_0(s)\subset A_1(s)\subset \cdots \subset A_{\text{valid}}(s)
\]

This keeps efficiency without permanently destroying completeness.

---

# 8. Relationship to Search-Aligned ATP Work

Recent work on Compute-Aligned Training optimizes an LLM policy for downstream theorem search under a compute budget.

That line of work still uses the basic structure:

\[
\text{proof state}
\rightarrow
\text{policy predicts tactic}
\rightarrow
\text{search}
\]

Our project differs by moving one level upward:

\[
\text{formal state}
\rightarrow
\text{structural abstraction}
\rightarrow
\text{reasoning operator}
\rightarrow
\text{subgoal transformation}
\rightarrow
\text{formal refinement}
\]

We therefore treat search as an executor over a compressed reasoning space rather than the primary source of reasoning capability.

---

# 9. v0.0: Reference Scaffold

This reference version does **not** attempt automatic abstraction learning or
Mathlib ingestion. Its purpose is to make the infrastructure executable before
connecting it to a much larger formal environment.

## Reference domain

The scaffold uses a tiny propositional language containing atoms, implication,
and conjunction. Its manually implemented actions are:

```text
intro
split_and
exact
and_left
and_right
```

The optional Python environment owns transitions in this reference domain. It exercises
multi-goal state handling and records rejected actions, but it is not presented
as a replacement for Lean elaboration. After search succeeds, the selected trace
is rendered as a `.lean` theorem and checked in a fresh Lean process.

This is an infrastructure test, not evidence for the long-term research claim.

---

# 10. Environment and Action Contract

Search depends on a replaceable environment with three responsibilities:

```python
class Environment:
    fingerprint
    initial_state(target)
    candidate_actions(state)
    apply(state, action) -> TransitionResult
```

An `Action` is typed and parameterized. The future Mathlib adapter should use
stable theorem identifiers, direction, goal index, occurrence path, and explicit
instantiation data rather than arbitrary tactic strings.

A `TransitionResult` distinguishes:

- valid transition with one complete successor state;
- invalid attempt with no target and a structured error code;
- generated subgoal count, cost, timing, and a replay fragment.

Candidate generation may cheaply reject impossible shapes, but Lean remains the
authority for applicability in the Lean-backed environment.

---

# 11. Search and Termination

The scaffold uses best-first search with:

\[
f(s)=\operatorname{depth}(s)+\operatorname{structuralComplexity}(s)
\]

Every run has explicit limits:

- maximum expanded states;
- maximum attempted actions;
- maximum depth;
- wall-clock timeout.

`visited` prevents redundant expansion but does not erase duplicate or cyclic
attempt edges from the experience graph. No run is called a "complete search"
unless the action space is finite and the frontier was actually exhausted.

Later versions can replace the heuristic with a learned cost-to-prove estimate,
but budget semantics and accounting must remain unchanged.

---

# 12. Core Loop

```python
frontier.push(initial_state)
graph.add_state(initial_state)

while frontier and within_budget():
    state = frontier.pop()

    if state.solved:
        trace = reconstruct_from_parent_edges(state)
        return replay_and_verify_in_fresh_lean(trace)

    for action in environment.candidate_actions(state):
        result = environment.apply(state, action)
        graph.record_attempt(state, action, result)

        if result.valid:
            graph.add_state(result.state)
            if improves_known_path(result.state):
                parent[result.state] = (state, action)
                frontier.push(result.state)
```

The outputs are both a kernel-checked proof artifact and the budget-bounded
search graph that produced it.

---

# 13. Repository Structure

```text
.
├── formal_reasoning_agent_README.md
├── pyproject.toml
├── src/formal_agent/
│   ├── formula.py        # reference-domain AST
│   ├── state.py          # complete multi-goal state
│   ├── action.py         # action and transition result
│   ├── environment.py    # replaceable reference environment
│   ├── lean_bridge.py    # persistent state-handle client and replay fallback
│   ├── lean_environment.py # Lean-owned transition environment
│   ├── graph.py          # cyclic graph and attempt log
│   ├── search.py         # budgeted best-first search
│   ├── lean_env.py       # final replay verifier
│   ├── agent.py
│   └── cli.py
├── lean/
│   ├── lakefile.lean
│   ├── lean-toolchain
│   ├── FormalAgent/Basic.lean
│   ├── FormalAgent/Driver.lean
│   ├── FormalAgent/Protocol.lean
│   └── FormalAgent/Probe.lean
├── benchmarks/prop_v0.json
└── tests/
```

Generated run artifacts live under `runs/` and are intentionally not committed.

---

# 14. Acceptance Criteria for the Scaffold

The scaffold is complete when it can:

1. solve all checked-in propositional examples under a declared budget;
2. retain multiple open goals in one state after conjunction splitting;
3. record valid, invalid, and duplicate attempts without assuming a DAG;
4. reconstruct one witness trace from parent edges;
5. have Lean accept the complete trace and reject an incomplete one;
6. save a versioned search graph and metrics for every problem;
7. ask Lean to accept or reject every transition in the default environment;
8. reuse one versioned JSONL driver process and process-local tactic states;
9. run without network access or third-party Python dependencies.

These criteria are implemented by the current demo and test suite.

---

# 15. v0.2: Persistent Lean State Driver and Mathlib Boundary

The current alpha adapter keeps opaque tactic-state handles inside one
long-lived Lean executable. The driver uses a versioned JSONL request/response
protocol. A transition restores the selected parent state and elaboration
contexts, applies one action, stores an independent child, and emits every
remaining goal as versioned JSON. Tactic recovery and error-to-`sorry`
conversion are disabled at this boundary, so an elaboration error cannot become
a successful child state.

Handles are deliberately process-local and are not proof certificates. Python
therefore retains the complete typed action trace. If a driver restart loses a
handle, the environment falls back to trace replay; final success is always
checked by a fresh Lean process without `sorry`.

Implemented:

- versioned Lean goal JSON;
- typed, allow-listed actions rather than arbitrary tactic strings;
- Lean-owned valid/invalid transition decisions;
- complete multi-goal observations;
- environment fingerprints including the exact Lean version;
- conservative trace-based state keys;
- persistent driver startup, request correlation, timeouts, and process reset;
- process-local `create/apply/release` state handles with parent-state isolation;
- batched cleanup of all ephemeral handles when a search finishes;
- strict action evaluation that rejects recovered tactic errors;
- trace-replay fallback when a restarted driver no longer knows a handle;
- final proof replay without `sorry`.

Before large-scale collection it still needs:

- richer structural expression serialization, metavariable constraints, universe
  information, and stable declaration identifiers;
- validation on Mathlib tactics, typeclass synthesis, and states with shared
  metavariables beyond the current propositional action set;
- finer structured diagnostic categories;
- Mathlib imports and an explicitly pinned Mathlib commit.

There are two different meanings of "use Mathlib as experience," and they must
not be conflated:

1. **Corpus replay:** instrument Mathlib source proofs to collect the intermediate
   states and successful actions that occur during elaboration.
2. **Search generation:** expose a curated Mathlib theorem inventory to this
   agent and record its own successful and failed search attempts.

Compiled proof terms can provide dependencies and final proof structure, but do
not recover the original tactic states, failed attempts, or search decisions.
Therefore raw `.olean` inspection alone is not a search-experience dataset.

Every dataset shard must record the Lean version, Mathlib commit, imports,
collection method, action vocabulary, budgets, and schema version.

---

# 16. v0.2: Abstraction

After enough versioned experience graphs have been collected:

\[
\text{Concrete Experience Graphs}
\rightarrow
\text{Definition-Space Graphs}
\]

Then investigate:

- canonicalization;
- anti-unification;
- graph-pattern mining;
- frequent subgraph discovery;
- structural clustering.

The objective is to discover:

\[
\boxed{\text{parameterized reasoning-graph schemas}}
\]

---

# 17. v0.3: Reuse and Compositional Evaluation

For a new theorem:

\[
s
\rightarrow
\phi_D(s)
\rightarrow
\text{match abstract operator}
\rightarrow
\text{instantiate}
\rightarrow
\text{reduced search}
\]

Compare under the same theorem inventory and compute budget:

\[
\text{search without experience}
\]

versus:

\[
\text{search guided by abstract experience}
\]

The strongest experiment will deliberately split train/test data by reasoning
composition and by normalized theorem structure.

For example:

Training sees:

\[
O_A\circ O_B
\]

and

\[
O_B\circ O_C
\]

but testing requires:

\[
O_A\circ O_C
\]

or:

\[
O_A\circ O_C\circ O_B
\]

This tests whether the system learned reusable operators rather than memorized complete proofs.

---

# 18. Long-Term Research Question

The larger research question is:

> Can a theorem-proving system compress a large corpus of formal proofs into a small, compositional basis of abstract reasoning operators, and use that basis to reduce search while retaining the ability to solve structurally novel problems?

A possible formal objective is:

\[
\min_D
\left[
\operatorname{Complexity}(D)
+
\lambda\operatorname{SearchCost}(D)
\right]
\]

subject to:

\[
\operatorname{ProofCoverage}(D)\ge \tau
\]

This can be interpreted as:

> Find the smallest useful reasoning language that explains and generates a large fraction of formal proofs.

---

# 19. Current Non-Goals

Do **not** attempt yet:

- training a large language model;
- automatic discovery of the full definition space;
- MCTS optimization;
- solving all of Mathlib;
- olympiad-level theorem proving;
- automatic lemma invention;
- fully learned operator generation.

The current purpose is to build a clean experimental platform and a trustworthy
data boundary before scaling collection.

---

# 20. Immediate Roadmap

### Milestone 0 — complete
Run a finite reference domain end to end, store the search graph, reconstruct a
proof, and have Lean verify it.

### Milestone 1 — complete
Implement a replay-based Lean adapter with typed actions, versioned goal output,
Lean-owned transition decisions, and clean final proof verification.

### Milestone 2 — complete
Replace per-action process startup with a persistent, versioned JSONL Lean
driver. Keep an immutable base environment, correlate requests and responses,
recover cleanly after timeouts, and retain a one-shot replay backend for
comparison.

### Milestone 3 — in progress
The `save/apply/restore` state-handle path and search-scoped handle cleanup are
implemented for the current typed action set. Next, enrich the state protocol
with metavariables, universes, declaration IDs, and structured diagnostics.

### Milestone 4
Select a small Mathlib import slice and implement 5–10 manually defined operators.
Keep rewrite directions and instantiation counts finite.

### Milestone 5
Replay final proofs in a clean pinned environment and add integration tests for
state identity, multiple goals, metavariables, typeclasses, and process recovery.

### Milestone 6
Collect a small versioned pilot dataset and inspect it before large-scale runs.

### Milestone 7
Define fair baselines, fixed compute budgets, train/test leakage rules, and
measurement semantics.

### Milestone 8
Begin definition-space abstraction experiments.

---

## Project Principle

> **Lean decides what is true.  
> The agent decides what structure to explore.  
> Experience is stored as a graph.  
> Intelligence emerges from compressing and composing that graph.**

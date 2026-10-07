from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from .action import Action
from .graph import SearchGraph
from .state import ProofState


@dataclass(frozen=True)
class SearchBudget:
    max_expansions: int = 1_000
    max_attempts: int = 10_000
    max_depth: int = 50
    timeout_seconds: float = 10.0


@dataclass
class SearchMetrics:
    expanded_states: int = 0
    attempted_actions: int = 0
    valid_transitions: int = 0
    invalid_transitions: int = 0
    duplicate_transitions: int = 0
    unique_states: int = 0
    max_frontier: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class SearchResult:
    status: str
    final_state_id: Optional[str]
    actions: List[Action]
    tactics: List[str]
    graph: SearchGraph
    metrics: SearchMetrics
    message: str

    @property
    def solved(self) -> bool:
        return self.status == "solved"

    def summary(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "final_state_id": self.final_state_id,
            "actions": [action.to_dict() for action in self.actions],
            "tactics": self.tactics,
            "metrics": asdict(self.metrics),
            "message": self.message,
        }


def best_first_search(environment, initial_state: ProofState, budget: SearchBudget) -> SearchResult:
    retained_states = [initial_state]
    try:
        return _best_first_search(environment, initial_state, budget, retained_states)
    finally:
        release_states = getattr(environment, "release_states", None)
        if release_states is not None:
            try:
                release_states(retained_states)
            except Exception:
                # State handles are an optimization, never the proof certificate.
                # Driver shutdown will reclaim them if best-effort cleanup fails.
                pass


def _best_first_search(
    environment,
    initial_state: ProofState,
    budget: SearchBudget,
    retained_states: List[ProofState],
) -> SearchResult:
    started = time.monotonic()
    graph = SearchGraph(initial_state.environment_fingerprint)
    graph.add_state(initial_state, depth=0)
    metrics = SearchMetrics(unique_states=1, max_frontier=1)

    sequence = itertools.count()
    frontier: List[Tuple[float, int, int, ProofState]] = []
    heapq.heappush(frontier, (_priority(initial_state, 0), next(sequence), 0, initial_state))

    best_depth: Dict[str, int] = {initial_state.key: 0}
    parent: Dict[str, Tuple[str, Action, str]] = {}

    while frontier:
        elapsed = time.monotonic() - started
        if elapsed >= budget.timeout_seconds:
            return _finish(
                "budget_exceeded", None, graph, metrics, started, parent,
                "timeout budget exhausted",
            )
        if metrics.expanded_states >= budget.max_expansions:
            return _finish(
                "budget_exceeded", None, graph, metrics, started, parent,
                "expansion budget exhausted",
            )

        _, _, depth, state = heapq.heappop(frontier)
        if depth != best_depth.get(state.key):
            continue
        if state.solved:
            return _finish(
                "solved", state.key, graph, metrics, started, parent, "proof found"
            )
        if depth >= budget.max_depth:
            continue

        graph.mark_expanded(state)
        metrics.expanded_states += 1

        for action in environment.candidate_actions(state):
            if metrics.attempted_actions >= budget.max_attempts:
                return _finish(
                    "budget_exceeded", None, graph, metrics, started, parent,
                    "attempt budget exhausted",
                )

            transition_started = time.monotonic()
            result = environment.apply(state, action)
            transition_ms = (time.monotonic() - transition_started) * 1_000
            graph.record_attempt(state, action, result, transition_ms)
            metrics.attempted_actions += 1

            if not result.valid:
                metrics.invalid_transitions += 1
                continue

            metrics.valid_transitions += 1
            assert result.state is not None and result.tactic is not None
            target = result.state
            retained_states.append(target)
            next_depth = depth + 1
            first_seen = graph.add_state(target, depth=next_depth)
            if first_seen:
                metrics.unique_states += 1
            else:
                metrics.duplicate_transitions += 1

            old_depth = best_depth.get(target.key)
            if old_depth is not None and old_depth <= next_depth:
                continue

            best_depth[target.key] = next_depth
            parent[target.key] = (state.key, action, result.tactic)
            heapq.heappush(
                frontier,
                (_priority(target, next_depth), next(sequence), next_depth, target),
            )
            metrics.max_frontier = max(metrics.max_frontier, len(frontier))

    return _finish(
        "exhausted", None, graph, metrics, started, parent, "frontier exhausted"
    )


def _priority(state: ProofState, depth: int) -> float:
    return float(depth + state.complexity())


def _finish(
    status: str,
    final_state_id: Optional[str],
    graph: SearchGraph,
    metrics: SearchMetrics,
    started: float,
    parent: Dict[str, Tuple[str, Action, str]],
    message: str,
) -> SearchResult:
    metrics.elapsed_seconds = time.monotonic() - started
    actions: List[Action] = []
    tactics: List[str] = []
    cursor = final_state_id
    while cursor is not None and cursor in parent:
        previous, action, tactic = parent[cursor]
        actions.append(action)
        tactics.append(tactic)
        cursor = previous
    actions.reverse()
    tactics.reverse()
    return SearchResult(
        status=status,
        final_state_id=final_state_id,
        actions=actions,
        tactics=tactics,
        graph=graph,
        metrics=metrics,
        message=message,
    )

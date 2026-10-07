from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .action import Action, TransitionResult
from .formula import Formula
from .lean_bridge import LeanBridge, LeanGoalView


@dataclass(frozen=True)
class LeanProofState:
    """A Lean-owned state with an ephemeral handle and replayable identity."""

    target: Formula
    goals: Tuple[LeanGoalView, ...]
    tactics: Tuple[str, ...]
    environment_fingerprint: str
    driver_state_id: Optional[str] = None

    @property
    def solved(self) -> bool:
        return not self.goals

    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "environment_fingerprint": self.environment_fingerprint,
            "target": self.target.to_dict(),
            "trace": list(self.tactics),
            "goals_observed": [goal.to_dict() for goal in self.goals],
        }

    @property
    def key(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def complexity(self) -> int:
        return len(self.goals) + sum(
            max(1, len(goal.target_pretty) // 20) for goal in self.goals
        )


class LeanEnvironment:
    """Environment in which Lean owns every transition decision."""

    def __init__(self, bridge: LeanBridge):
        self.bridge = bridge

    @property
    def fingerprint(self) -> str:
        return self.bridge.environment_fingerprint

    def initial_state(self, target: Formula) -> LeanProofState:
        result = self.bridge.create_state(target)
        if not result.valid:
            raise RuntimeError(f"Lean rejected initial theorem state: {result.message}")
        return LeanProofState(
            target=target,
            goals=result.goals,
            tactics=tuple(),
            environment_fingerprint=self.fingerprint,
            driver_state_id=result.state_id,
        )

    def candidate_actions(self, state: LeanProofState) -> Iterable[Action]:
        if state.solved:
            return []
        goal = state.goals[0]
        actions: List[Action] = [Action("intro"), Action("split_and")]
        for hypothesis in goal.local_context:
            if (
                _safe_identifier(hypothesis.user_name)
                and _is_actionable_hypothesis(hypothesis.type_pretty)
            ):
                actions.extend(
                    [
                        Action("exact", hypothesis=hypothesis.user_name),
                        Action("and_left", hypothesis=hypothesis.user_name),
                        Action("and_right", hypothesis=hypothesis.user_name),
                    ]
                )
        return actions

    def release_states(self, states: Iterable[LeanProofState]) -> None:
        """Release ephemeral handles after a search no longer needs them."""
        if not self.bridge.persistent_driver:
            return
        state_ids = tuple(
            state.driver_state_id
            for state in states
            if state.driver_state_id is not None
        )
        if state_ids:
            self.bridge.release_states(state_ids)

    def apply(self, state: LeanProofState, action: Action) -> TransitionResult:
        if state.solved:
            return _invalid("state_solved", "cannot act on a solved Lean state")
        if action.goal_index != 0:
            return _invalid("unsupported_goal", "the bridge currently focuses goal 0")

        try:
            tactic = _compile_action(state, action)
        except ValueError as error:
            return _invalid("invalid_action", str(error))

        tactics = state.tactics + (tactic,)
        if state.driver_state_id is not None and self.bridge.persistent_driver:
            probe = self.bridge.apply_state(state.driver_state_id, tactic)
            # State handles live inside one driver process. If that process was
            # restarted, replay the trace once so correctness is preserved.
            if probe.error_code == "state_not_found":
                probe = self.bridge.probe(state.target, tactics)
        else:
            probe = self.bridge.probe(state.target, tactics)
        if not probe.valid:
            return _invalid(probe.error_code or "lean_rejected", probe.message)

        next_state = LeanProofState(
            target=state.target,
            goals=probe.goals,
            tactics=tactics,
            environment_fingerprint=state.environment_fingerprint,
            driver_state_id=probe.state_id,
        )
        generated_goal_count = max(0, len(next_state.goals) - len(state.goals) + 1)
        return TransitionResult(
            valid=True,
            state=next_state,
            tactic=tactic,
            error_code=None,
            message="ok",
            generated_goal_count=generated_goal_count,
        )


def _compile_action(state: LeanProofState, action: Action) -> str:
    if action.kind == "intro":
        used = {
            declaration.user_name
            for goal in state.goals
            for declaration in goal.local_context
        }
        index = 0
        while f"h{index}" in used:
            index += 1
        return f"intro h{index}"
    if action.kind == "split_and":
        return "constructor"

    hypothesis = action.hypothesis
    if hypothesis is None or not _safe_identifier(hypothesis):
        raise ValueError("action requires a safe local hypothesis name")
    if action.kind == "exact":
        return f"exact {hypothesis}"
    if action.kind == "and_left":
        return f"exact {hypothesis}.1"
    if action.kind == "and_right":
        return f"exact {hypothesis}.2"
    raise ValueError(f"unsupported Lean action: {action.kind}")


def _safe_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", value))


def _is_actionable_hypothesis(type_pretty: str) -> bool:
    """Skip proposition/type parameters that cannot directly prove a goal."""
    normalized = type_pretty.strip()
    return not (
        normalized == "Prop"
        or normalized == "Type"
        or normalized.startswith("Type ")
        or normalized.startswith("Type (")
        or normalized.startswith("Sort ")
        or normalized.startswith("Sort (")
    )


def _invalid(error_code: str, message: str) -> TransitionResult:
    return TransitionResult(
        valid=False,
        state=None,
        tactic=None,
        error_code=error_code,
        message=message,
        generated_goal_count=0,
    )

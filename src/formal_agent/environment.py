from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .action import Action, TransitionResult
from .state import Goal, Hypothesis, ProofState, fresh_hypothesis_name


@dataclass(frozen=True)
class PropositionalEnvironment:
    """Finite reference environment used to exercise the framework end to end.

    This is not a Lean tactic-state emulator. Its successful traces are accepted
    only after the generated proof is replayed by Lean.
    """

    fingerprint: str = "toy-prop-v1"

    def initial_state(self, target):
        return ProofState.initial(target, self.fingerprint)

    def candidate_actions(self, state: ProofState) -> Iterable[Action]:
        if state.solved:
            return []

        goal = state.goals[0]
        actions: List[Action] = [Action("intro"), Action("split_and")]
        for hypothesis in goal.context:
            actions.extend(
                [
                    Action("exact", hypothesis=hypothesis.name),
                    Action("and_left", hypothesis=hypothesis.name),
                    Action("and_right", hypothesis=hypothesis.name),
                ]
            )
        return actions

    def apply(self, state: ProofState, action: Action) -> TransitionResult:
        if state.solved:
            return self._invalid("state_solved", "cannot act on a solved state")
        if action.goal_index != 0:
            return self._invalid("unsupported_goal", "the scaffold focuses the first goal")

        goal = state.goals[0]
        remaining_goals = state.goals[1:]

        if action.kind == "intro":
            if goal.target.kind != "imp":
                return self._invalid("shape_mismatch", "target is not an implication")
            assert goal.target.left is not None and goal.target.right is not None
            name = fresh_hypothesis_name(state.goals)
            next_goal = Goal(
                context=goal.context + (Hypothesis(name, goal.target.left),),
                target=goal.target.right,
            )
            return self._valid(
                state,
                (next_goal,) + remaining_goals,
                tactic=f"intro {name}",
                generated_goal_count=1,
            )

        if action.kind == "split_and":
            if goal.target.kind != "and":
                return self._invalid("shape_mismatch", "target is not a conjunction")
            assert goal.target.left is not None and goal.target.right is not None
            left_goal = Goal(goal.context, goal.target.left)
            right_goal = Goal(goal.context, goal.target.right)
            return self._valid(
                state,
                (left_goal, right_goal) + remaining_goals,
                tactic="constructor",
                generated_goal_count=2,
            )

        hypothesis = self._find_hypothesis(goal, action.hypothesis)
        if hypothesis is None:
            return self._invalid("unknown_hypothesis", "hypothesis does not exist")

        if action.kind == "exact":
            if hypothesis.formula != goal.target:
                return self._invalid("type_mismatch", "hypothesis does not match target")
            return self._valid(
                state,
                remaining_goals,
                tactic=f"exact {hypothesis.name}",
                generated_goal_count=0,
            )

        if action.kind in {"and_left", "and_right"}:
            if hypothesis.formula.kind != "and":
                return self._invalid("type_mismatch", "hypothesis is not a conjunction")
            projected = (
                hypothesis.formula.left
                if action.kind == "and_left"
                else hypothesis.formula.right
            )
            if projected != goal.target:
                return self._invalid("type_mismatch", "projection does not match target")
            suffix = "1" if action.kind == "and_left" else "2"
            return self._valid(
                state,
                remaining_goals,
                tactic=f"exact {hypothesis.name}.{suffix}",
                generated_goal_count=0,
            )

        return self._invalid("unknown_action", f"unsupported action: {action.kind}")

    @staticmethod
    def _find_hypothesis(goal: Goal, name: Optional[str]) -> Optional[Hypothesis]:
        for hypothesis in goal.context:
            if hypothesis.name == name:
                return hypothesis
        return None

    def _valid(
        self,
        old_state: ProofState,
        goals,
        tactic: str,
        generated_goal_count: int,
    ) -> TransitionResult:
        return TransitionResult(
            valid=True,
            state=ProofState(tuple(goals), old_state.environment_fingerprint),
            tactic=tactic,
            error_code=None,
            message="ok",
            generated_goal_count=generated_goal_count,
        )

    @staticmethod
    def _invalid(error_code: str, message: str) -> TransitionResult:
        return TransitionResult(
            valid=False,
            state=None,
            tactic=None,
            error_code=error_code,
            message=message,
            generated_goal_count=0,
        )

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple

from .formula import Formula


@dataclass(frozen=True)
class Hypothesis:
    name: str
    formula: Formula

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "formula": self.formula.to_dict()}


@dataclass(frozen=True)
class Goal:
    context: Tuple[Hypothesis, ...]
    target: Formula

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context": [hypothesis.to_dict() for hypothesis in self.context],
            "target": self.target.to_dict(),
        }


@dataclass(frozen=True)
class ProofState:
    """The whole search state, including every currently open goal."""

    goals: Tuple[Goal, ...]
    environment_fingerprint: str

    @classmethod
    def initial(cls, target: Formula, environment_fingerprint: str) -> "ProofState":
        return cls(
            goals=(Goal(context=tuple(), target=target),),
            environment_fingerprint=environment_fingerprint,
        )

    @property
    def solved(self) -> bool:
        return not self.goals

    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "environment_fingerprint": self.environment_fingerprint,
            "goals": [goal.to_dict() for goal in self.goals],
        }

    @property
    def key(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def complexity(self) -> int:
        return sum(goal.target.complexity() for goal in self.goals) + len(self.goals)


def fresh_hypothesis_name(goals: Iterable[Goal]) -> str:
    used = {
        hypothesis.name
        for goal in goals
        for hypothesis in goal.context
    }
    index = 0
    while f"h{index}" in used:
        index += 1
    return f"h{index}"


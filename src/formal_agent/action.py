from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .state import ProofState


@dataclass(frozen=True)
class Action:
    kind: str
    goal_index: int = 0
    hypothesis: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "goal_index": self.goal_index,
            "hypothesis": self.hypothesis,
        }


@dataclass(frozen=True)
class TransitionResult:
    valid: bool
    state: Optional[ProofState]
    tactic: Optional[str]
    error_code: Optional[str]
    message: str
    generated_goal_count: int
    cost: float = 1.0


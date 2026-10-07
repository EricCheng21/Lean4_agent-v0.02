from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .action import Action, TransitionResult
from .state import ProofState


@dataclass
class SearchNode:
    state_id: str
    state: Dict[str, Any]
    first_depth: int
    expanded: bool = False


@dataclass
class AttemptEdge:
    attempt_id: int
    source_id: str
    action: Dict[str, Any]
    valid: bool
    target_id: Optional[str]
    error_code: Optional[str]
    message: str
    cost: float
    elapsed_ms: float
    generated_goal_count: int


class SearchGraph:
    """A state-transition graph. Cycles and invalid attempts are preserved."""

    schema_version = 1

    def __init__(self, environment_fingerprint: str):
        self.environment_fingerprint = environment_fingerprint
        self.created_at_unix = time.time()
        self.nodes: Dict[str, SearchNode] = {}
        self.attempts: List[AttemptEdge] = []

    def add_state(self, state: ProofState, depth: int) -> bool:
        if state.key in self.nodes:
            return False
        self.nodes[state.key] = SearchNode(
            state_id=state.key,
            state=state.canonical_payload(),
            first_depth=depth,
        )
        return True

    def mark_expanded(self, state: ProofState) -> None:
        self.nodes[state.key].expanded = True

    def record_attempt(
        self,
        source: ProofState,
        action: Action,
        result: TransitionResult,
        elapsed_ms: float,
    ) -> None:
        target_id = result.state.key if result.state is not None else None
        self.attempts.append(
            AttemptEdge(
                attempt_id=len(self.attempts),
                source_id=source.key,
                action=action.to_dict(),
                valid=result.valid,
                target_id=target_id,
                error_code=result.error_code,
                message=result.message,
                cost=result.cost,
                elapsed_ms=elapsed_ms,
                generated_goal_count=result.generated_goal_count,
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "environment_fingerprint": self.environment_fingerprint,
            "created_at_unix": self.created_at_unix,
            "nodes": [asdict(node) for node in self.nodes.values()],
            "attempts": [asdict(attempt) for attempt in self.attempts],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


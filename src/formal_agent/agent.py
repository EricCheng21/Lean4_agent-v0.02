from __future__ import annotations

from dataclasses import dataclass

from .formula import Formula
from .lean_env import LeanVerifier, VerificationResult
from .search import SearchBudget, SearchResult, best_first_search


@dataclass
class RunResult:
    search: SearchResult
    verification: VerificationResult

    @property
    def succeeded(self) -> bool:
        return self.search.solved and self.verification.verified


class FormalReasoningAgent:
    def __init__(self, environment, verifier: LeanVerifier):
        self.environment = environment
        self.verifier = verifier

    def solve(self, target: Formula, budget: SearchBudget) -> RunResult:
        initial_state = self.environment.initial_state(target)
        search_result = best_first_search(self.environment, initial_state, budget)
        if search_result.solved:
            verification = self.verifier.verify(target, search_result.tactics)
        else:
            verification = VerificationResult(
                verified=False,
                return_code=None,
                stdout="",
                stderr="",
                source="",
                message="verification skipped because search did not find a proof",
            )
        return RunResult(search=search_result, verification=verification)

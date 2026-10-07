from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .agent import FormalReasoningAgent
from .environment import PropositionalEnvironment
from .formula import Formula
from .lean_bridge import LeanBridge
from .lean_environment import LeanEnvironment
from .lean_env import LeanVerifier
from .search import SearchBudget


def _load_benchmarks(path: Path) -> Iterable[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema version")
    return payload["problems"]


def run_demo(benchmark_path: Path, output_dir: Path, environment_name: str) -> int:
    if environment_name == "lean":
        environment = LeanEnvironment(LeanBridge(timeout_seconds=20.0))
        budget = SearchBudget(timeout_seconds=120.0)
    elif environment_name == "toy":
        environment = PropositionalEnvironment()
        budget = SearchBudget()
    else:
        raise ValueError(f"unsupported environment: {environment_name}")
    verifier = LeanVerifier()
    agent = FormalReasoningAgent(environment, verifier)
    summaries: List[Dict[str, Any]] = []

    output_dir.mkdir(parents=True, exist_ok=True)
    all_succeeded = True

    for problem in _load_benchmarks(benchmark_path):
        problem_id = str(problem["id"])
        target = Formula.from_dict(problem["target"])
        result = agent.solve(target, budget)
        problem_dir = output_dir / problem_id
        problem_dir.mkdir(parents=True, exist_ok=True)

        result.search.graph.save(problem_dir / "search_graph.json")
        (problem_dir / "proof.lean").write_text(
            result.verification.source, encoding="utf-8"
        )
        summary = {
            "problem_id": problem_id,
            "environment": environment_name,
            "target": target.to_dict(),
            "lean_target": target.to_lean(),
            **result.search.summary(),
            "lean_verified": result.verification.verified,
            "verification_message": result.verification.message,
            "verification_stderr": result.verification.stderr,
        }
        (problem_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summaries.append(summary)
        all_succeeded = all_succeeded and result.succeeded
        print(
            f"{problem_id}: search={result.search.status} "
            f"lean_verified={result.verification.verified} "
            f"states={result.search.metrics.unique_states} "
            f"attempts={result.search.metrics.attempted_actions}"
        )

    run_summary = {
        "schema_version": 1,
        "all_succeeded": all_succeeded,
        "problem_count": len(summaries),
        "problems": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(run_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"artifacts: {output_dir.resolve()}")
    return 0 if all_succeeded else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="formal-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run the minimal end-to-end demo")
    demo.add_argument(
        "--benchmark",
        type=Path,
        default=Path("benchmarks/prop_v0.json"),
    )
    demo.add_argument(
        "--output",
        type=Path,
        default=Path("runs/demo"),
    )
    demo.add_argument(
        "--environment",
        choices=("lean", "toy"),
        default="lean",
        help="use Lean-owned transitions (default) or the Python reference domain",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        return run_demo(args.benchmark, args.output, args.environment)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

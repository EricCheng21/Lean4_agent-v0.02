from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .formula import Formula, sorted_atoms


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    return_code: Optional[int]
    stdout: str
    stderr: str
    source: str
    message: str


class LeanVerifier:
    """Replays a complete generated proof in a fresh Lean process."""

    def __init__(
        self,
        executable: str = "lean",
        timeout_seconds: float = 10.0,
        project_dir: Optional[Path] = None,
    ):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        default_project_dir = Path(__file__).resolve().parents[2] / "lean"
        self.project_dir = (
            project_dir
            if project_dir is not None
            else default_project_dir if default_project_dir.is_dir() else None
        )

    def render_source(self, target: Formula, tactics: Iterable[str]) -> str:
        atoms = " ".join(sorted_atoms(target))
        binders = f" ({atoms} : Prop)" if atoms else ""
        tactic_lines = "\n".join(f"  {tactic}" for tactic in tactics)
        return (
            "set_option autoImplicit false\n\n"
            f"theorem generated{binders} : {target.to_lean()} := by\n"
            f"{tactic_lines}\n"
        )

    def verify(self, target: Formula, tactics: Iterable[str]) -> VerificationResult:
        source = self.render_source(target, tactics)
        executable = shutil.which(self.executable)
        if executable is None:
            return VerificationResult(
                verified=False,
                return_code=None,
                stdout="",
                stderr="",
                source=source,
                message=f"Lean executable not found: {self.executable}",
            )

        with tempfile.TemporaryDirectory(prefix="formal-agent-") as temp_dir:
            source_path = Path(temp_dir) / "Generated.lean"
            source_path.write_text(source, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [executable, str(source_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    cwd=str(self.project_dir) if self.project_dir is not None else None,
                )
            except subprocess.TimeoutExpired as error:
                return VerificationResult(
                    verified=False,
                    return_code=None,
                    stdout=error.stdout or "",
                    stderr=error.stderr or "",
                    source=source,
                    message="Lean verification timed out",
                )

        verified = completed.returncode == 0
        return VerificationResult(
            verified=verified,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            source=source,
            message="verified" if verified else "Lean rejected the generated proof",
        )

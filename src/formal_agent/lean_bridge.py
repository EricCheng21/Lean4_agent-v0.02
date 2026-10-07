from __future__ import annotations

import atexit
from collections import deque
import json
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional, Tuple

from .formula import Formula, sorted_atoms


GOAL_MARKER = "FORMAL_AGENT_GOAL:"
DONE_MARKER = "FORMAL_AGENT_PROBE_DONE"


@dataclass(frozen=True)
class LeanLocalDeclView:
    user_name: str
    type_pretty: str
    type_repr: str
    is_let: bool

    @classmethod
    def from_dict(cls, value):
        return cls(
            user_name=str(value["userName"]),
            type_pretty=str(value["typePretty"]),
            type_repr=str(value["typeRepr"]),
            is_let=bool(value["isLet"]),
        )

    def to_dict(self):
        return {
            "user_name": self.user_name,
            "type_pretty": self.type_pretty,
            "type_repr": self.type_repr,
            "is_let": self.is_let,
        }


@dataclass(frozen=True)
class LeanGoalView:
    target_pretty: str
    target_repr: str
    local_context: Tuple[LeanLocalDeclView, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(
            target_pretty=str(value["targetPretty"]),
            target_repr=str(value["targetRepr"]),
            local_context=tuple(
                LeanLocalDeclView.from_dict(item)
                for item in value["localContext"]
            ),
        )

    def to_dict(self):
        return {
            "target_pretty": self.target_pretty,
            "target_repr": self.target_repr,
            "local_context": [item.to_dict() for item in self.local_context],
        }


@dataclass(frozen=True)
class ProbeResult:
    valid: bool
    goals: Tuple[LeanGoalView, ...]
    error_code: Optional[str]
    message: str
    stdout: str
    stderr: str
    source: str
    return_code: Optional[int]
    state_id: Optional[str] = None

    @property
    def solved(self) -> bool:
        return self.valid and not self.goals


class LeanBridge:
    """Drive process-local Lean states, retaining traces for replay and proof."""

    schema_version = 1

    def __init__(
        self,
        project_dir: Optional[Path] = None,
        timeout_seconds: float = 10.0,
        lake_executable: str = "lake",
        persistent_driver: bool = True,
    ):
        default_project_dir = Path(__file__).resolve().parents[2] / "lean"
        self.project_dir = (
            project_dir if project_dir is not None else default_project_dir
        ).resolve()
        self.timeout_seconds = timeout_seconds
        self.lake_executable = lake_executable
        self.persistent_driver = persistent_driver
        self._ready = False
        self._lean_version: Optional[str] = None
        self._lake_path: Optional[str] = None
        self._driver: Optional[_LeanDriverClient] = None
        self._next_state_id = 0
        self._state_id_lock = threading.Lock()
        atexit.register(self.close)

    @property
    def environment_fingerprint(self) -> str:
        self._ensure_ready()
        if self.persistent_driver:
            self._ensure_driver()
            backend = "lean-jsonl-state"
        else:
            backend = "lean-process-replay"
        return f"{backend}-v{self.schema_version}|{self._lean_version}"

    @property
    def backend(self) -> str:
        return "persistent_driver" if self.persistent_driver else "one_shot"

    @property
    def driver_process_id(self) -> Optional[int]:
        if self._driver is None:
            return None
        return self._driver.process_id

    def render_probe_source(
        self,
        target: Formula,
        tactics: Iterable[str],
        *,
        for_driver: bool = False,
    ) -> str:
        atoms = " ".join(sorted_atoms(target))
        binders = f" ({atoms} : Prop)" if atoms else ""
        tactic_lines = "\n".join(f"  {tactic}" for tactic in tactics)
        if tactic_lines:
            tactic_lines += "\n"
        dump_tactic = (
            "all_goals agent_dump_goal"
            if for_driver
            else "all_goals run_tac FormalAgent.dumpGoal"
        )
        close_tactic = "all_goals exact sorry" if for_driver else "all_goals sorry"
        completion = "" if for_driver else f'\n#eval IO.println "{DONE_MARKER}"\n'
        import_line = "" if for_driver else "import FormalAgent.Probe\n\n"
        return (
            f"{import_line}"
            "set_option autoImplicit false\n\n"
            f"example{binders} : {target.to_lean()} := by\n"
            f"{tactic_lines}"
            f"  {dump_tactic}\n"
            f"  {close_tactic}\n"
            f"{completion}"
        )

    def probe(self, target: Formula, tactics: Iterable[str]) -> ProbeResult:
        source = self.render_probe_source(
            target, tactics, for_driver=self.persistent_driver
        )
        try:
            self._ensure_ready()
            if self.persistent_driver:
                return self._probe_with_driver(source)
        except RuntimeError as error:
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="bridge_unavailable",
                message=str(error),
                stdout="",
                stderr="",
                source=source,
                return_code=None,
            )

        return self._probe_one_shot(source)

    def create_state(self, target: Formula) -> ProbeResult:
        """Create a process-local Lean tactic-state handle for one theorem."""
        if not self.persistent_driver:
            return self.probe(target, [])
        state_id = self._new_state_id()
        source = self._render_create_state_source(target, state_id)
        try:
            self._ensure_ready()
            return self._probe_with_driver(
                source, method="createState", state_id=state_id
            )
        except RuntimeError as error:
            return _bridge_unavailable(source, error)

    def apply_state(self, parent_state_id: str, tactic: str) -> ProbeResult:
        """Apply one tactic to a saved parent without replaying its trace."""
        if not self.persistent_driver:
            raise RuntimeError("state handles require the persistent Lean driver")
        if "\n" in tactic or "\r" in tactic:
            raise ValueError("state-handle actions must be one-line Lean tactics")
        child_state_id = self._new_state_id()
        source = self._render_apply_state_source(
            parent_state_id, child_state_id, tactic
        )
        try:
            self._ensure_ready()
            return self._probe_with_driver(
                source, method="applyState", state_id=child_state_id
            )
        except RuntimeError as error:
            return _bridge_unavailable(source, error)

    def release_state(self, state_id: str) -> ProbeResult:
        """Release a process-local state handle."""
        return self.release_states([state_id])

    def release_states(self, state_ids: Iterable[str]) -> ProbeResult:
        """Release process-local state handles in one driver request."""
        if not self.persistent_driver:
            raise RuntimeError("state handles require the persistent Lean driver")
        state_ids = tuple(state_ids)
        source = self._render_release_states_source(state_ids)
        try:
            self._ensure_ready()
            return self._probe_with_driver(source, method="releaseState")
        except RuntimeError as error:
            return _bridge_unavailable(source, error)

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _probe_with_driver(
        self,
        source: str,
        *,
        method: str = "probe",
        state_id: Optional[str] = None,
    ) -> ProbeResult:
        try:
            driver = self._ensure_driver()
            response = driver.request(method, source)
        except _DriverTimeout as error:
            self.close()
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="timeout",
                message=str(error),
                stdout="",
                stderr="",
                source=source,
                return_code=None,
                state_id=None,
            )
        except _DriverTransportError as error:
            stderr = self._driver.stderr_tail if self._driver is not None else ""
            self.close()
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="driver_error",
                message=str(error),
                stdout="",
                stderr=stderr,
                source=source,
                return_code=None,
                state_id=None,
            )

        output = str(response.get("output", ""))
        message = str(response.get("message", "Lean driver returned no message"))
        if not response.get("ok", False):
            driver_error_code = str(response.get("errorCode") or "lean_rejected")
            error_code = (
                _classify_error(message)
                if driver_error_code == "lean_rejected"
                else driver_error_code
            )
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code=error_code,
                message=message,
                stdout=output,
                stderr="",
                source=source,
                return_code=1,
                state_id=None,
            )

        try:
            goals = tuple(
                LeanGoalView.from_dict(value) for value in response.get("goals", [])
            )
        except (KeyError, TypeError, ValueError) as error:
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="protocol_error",
                message=f"invalid goal payload from Lean driver: {error}",
                stdout=output,
                stderr="",
                source=source,
                return_code=0,
                state_id=None,
            )

        return ProbeResult(
            valid=True,
            goals=goals,
            error_code=None,
            message="ok",
            stdout=output,
            stderr="",
            source=source,
            return_code=0,
            state_id=state_id,
        )

    def _new_state_id(self) -> str:
        with self._state_id_lock:
            self._next_state_id += 1
            return f"state-{self._next_state_id}"

    def _render_create_state_source(
        self, target: Formula, state_id: str
    ) -> str:
        atoms = " ".join(sorted_atoms(target))
        binders = f" ({atoms} : Prop)" if atoms else ""
        return (
            "set_option autoImplicit false\n\n"
            f"example{binders} : {target.to_lean()} := by\n"
            f"  agent_save_state {json.dumps(state_id)}\n"
            "  all_goals exact sorry\n"
        )

    def _render_apply_state_source(
        self, parent_state_id: str, child_state_id: str, tactic: str
    ) -> str:
        return (
            "set_option autoImplicit false\n\n"
            "example : True := by\n"
            f"  agent_apply_state {json.dumps(parent_state_id)} "
            f"{json.dumps(child_state_id)} => {tactic}\n"
            "  exact True.intro\n"
        )

    def _render_release_states_source(self, state_ids: Iterable[str]) -> str:
        release_lines = "".join(
            f"  agent_release_state {json.dumps(state_id)}\n"
            for state_id in state_ids
        )
        return (
            "example : True := by\n"
            f"{release_lines}"
            "  exact True.intro\n"
        )

    def _probe_one_shot(self, source: str) -> ProbeResult:

        with tempfile.TemporaryDirectory(prefix="formal-agent-probe-") as temp_dir:
            source_path = Path(temp_dir) / "Probe.lean"
            source_path.write_text(source, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        self.lake_executable,
                        "env",
                        "lean",
                        str(source_path),
                    ],
                    cwd=str(self.project_dir),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                return ProbeResult(
                    valid=False,
                    goals=tuple(),
                    error_code="timeout",
                    message="Lean transition probe timed out",
                    stdout=_output_text(error.stdout),
                    stderr=_output_text(error.stderr),
                    source=source,
                    return_code=None,
                )

        combined_output = completed.stdout + "\n" + completed.stderr
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout).strip()
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code=_classify_error(diagnostic),
                message=diagnostic or "Lean rejected the transition",
                stdout=completed.stdout,
                stderr=completed.stderr,
                source=source,
                return_code=completed.returncode,
            )

        if DONE_MARKER not in combined_output:
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="protocol_error",
                message="Lean completed without the probe completion marker",
                stdout=completed.stdout,
                stderr=completed.stderr,
                source=source,
                return_code=completed.returncode,
            )
        try:
            goals = _parse_goals(combined_output)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return ProbeResult(
                valid=False,
                goals=tuple(),
                error_code="protocol_error",
                message=f"invalid goal payload from Lean: {error}",
                stdout=completed.stdout,
                stderr=completed.stderr,
                source=source,
                return_code=completed.returncode,
            )

        return ProbeResult(
            valid=True,
            goals=goals,
            error_code=None,
            message="ok",
            stdout=completed.stdout,
            stderr=completed.stderr,
            source=source,
            return_code=completed.returncode,
        )

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        lake = shutil.which(self.lake_executable)
        if lake is None:
            raise RuntimeError(f"Lake executable not found: {self.lake_executable}")
        if not self.project_dir.is_dir():
            raise RuntimeError(f"Lean project directory not found: {self.project_dir}")

        build_target = "formal_agent_driver" if self.persistent_driver else "FormalAgent"
        build = subprocess.run(
            [lake, "build", build_target],
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
            timeout=max(self.timeout_seconds, 30.0),
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(
                "failed to build Lean probe module: "
                + (build.stderr or build.stdout).strip()
            )

        version = subprocess.run(
            [lake, "env", "lean", "--version"],
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if version.returncode != 0:
            raise RuntimeError(
                "failed to query Lean version: "
                + (version.stderr or version.stdout).strip()
            )
        self._lean_version = version.stdout.strip()
        self._lake_path = lake
        self._ready = True

    def _ensure_driver(self) -> "_LeanDriverClient":
        if self._driver is not None and self._driver.running:
            return self._driver
        if not self._ready or self._lake_path is None:
            self._ensure_ready()
        assert self._lake_path is not None
        driver_path = self.project_dir / ".lake" / "build" / "bin" / "formal_agent_driver"
        if not driver_path.is_file():
            raise RuntimeError(f"Lean driver executable not found: {driver_path}")
        try:
            driver = _LeanDriverClient(
                command=[self._lake_path, "env", str(driver_path)],
                cwd=self.project_dir,
                timeout_seconds=self.timeout_seconds,
                schema_version=self.schema_version,
            )
            ping = driver.request("ping", "")
            if not ping.get("ok", False):
                raise _DriverTransportError(
                    f"Lean driver rejected ping: {ping.get('message', 'unknown error')}"
                )
        except (_DriverTimeout, _DriverTransportError) as error:
            if "driver" in locals():
                driver.close()
            raise RuntimeError(f"failed to start Lean driver: {error}") from error
        self._driver = driver
        return driver


class _DriverTimeout(TimeoutError):
    pass


class _DriverTransportError(RuntimeError):
    pass


class _LeanDriverClient:
    """Synchronous, thread-safe client for one long-lived JSONL subprocess."""

    def __init__(
        self,
        command,
        cwd: Path,
        timeout_seconds: float,
        schema_version: int,
    ):
        self.timeout_seconds = timeout_seconds
        self.schema_version = schema_version
        self._next_request_id = 0
        self._lock = threading.Lock()
        self._stderr_lines: Deque[str] = deque(maxlen=100)
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise _DriverTransportError(
                f"could not launch Lean driver: {error}"
            ) from error
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="formal-agent-lean-driver-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    @property
    def process_id(self) -> int:
        return self._process.pid

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr_lines)

    def request(self, method: str, source: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running:
                raise _DriverTransportError(
                    f"Lean driver exited with code {self._process.returncode}: "
                    f"{self.stderr_tail.strip()}"
                )
            self._next_request_id += 1
            request_id = str(self._next_request_id)
            payload = {
                "schemaVersion": self.schema_version,
                "requestId": request_id,
                "method": method,
                "source": source,
            }
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            try:
                self._process.stdin.write(
                    json.dumps(payload, separators=(",", ":")) + "\n"
                )
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise _DriverTransportError(
                    f"failed to write to Lean driver: {error}"
                ) from error

            deadline = time.monotonic() + self.timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _DriverTimeout(
                        f"Lean driver request {request_id} timed out after "
                        f"{self.timeout_seconds:g}s"
                    )
                readable, _, _ = select.select(
                    [self._process.stdout], [], [], remaining
                )
                if not readable:
                    raise _DriverTimeout(
                        f"Lean driver request {request_id} timed out after "
                        f"{self.timeout_seconds:g}s"
                    )
                line = self._process.stdout.readline()
                if not line:
                    raise _DriverTransportError(
                        f"Lean driver closed stdout with code "
                        f"{self._process.poll()}: {self.stderr_tail.strip()}"
                    )
                if line.strip():
                    break

            try:
                response = json.loads(line)
            except json.JSONDecodeError as error:
                raise _DriverTransportError(
                    f"Lean driver emitted invalid JSON: {line.strip()}"
                ) from error
            if not isinstance(response, dict):
                raise _DriverTransportError("Lean driver response is not an object")
            if response.get("schemaVersion") != self.schema_version:
                raise _DriverTransportError(
                    f"Lean driver schema mismatch: {response.get('schemaVersion')!r}"
                )
            if response.get("requestId") != request_id:
                raise _DriverTransportError(
                    "Lean driver response/request id mismatch: "
                    f"{response.get('requestId')!r} != {request_id!r}"
                )
            return response

    def close(self) -> None:
        with self._lock:
            if self._process.poll() is None:
                try:
                    if self._process.stdin is not None:
                        self._process.stdin.close()
                    self._process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=2.0)

        self._stderr_thread.join(timeout=2.0)
        for stream in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

    def _drain_stderr(self) -> None:
        if self._process.stderr is None:
            return
        for line in self._process.stderr:
            self._stderr_lines.append(line)


def _parse_goals(output: str) -> Tuple[LeanGoalView, ...]:
    goals = []
    for line in output.splitlines():
        marker_index = line.find(GOAL_MARKER)
        if marker_index < 0:
            continue
        payload = line[marker_index + len(GOAL_MARKER) :].strip()
        envelope = json.loads(payload)
        if envelope.get("schemaVersion") != LeanBridge.schema_version:
            raise ValueError(
                f"unsupported Lean goal schema: {envelope.get('schemaVersion')!r}"
            )
        goals.append(LeanGoalView.from_dict(envelope["goal"]))
    return tuple(goals)


def _bridge_unavailable(source: str, error: RuntimeError) -> ProbeResult:
    return ProbeResult(
        valid=False,
        goals=tuple(),
        error_code="bridge_unavailable",
        message=str(error),
        stdout="",
        stderr="",
        source=source,
        return_code=None,
    )


def _classify_error(diagnostic: str) -> str:
    lowered = diagnostic.lower()
    if "unknown identifier" in lowered:
        return "unknown_hypothesis"
    if "unknown proof state" in lowered:
        return "state_not_found"
    if "type mismatch" in lowered or "application type mismatch" in lowered:
        return "type_mismatch"
    if (
        "tactic 'intro' failed" in lowered
        or "tactic `intron` failed" in lowered
        or "no binders" in lowered
        or "no additional binders" in lowered
        or "target is not an inductive datatype" in lowered
    ):
        return "shape_mismatch"
    if "no goals to be solved" in lowered:
        return "state_solved"
    if re.search(r"unexpected|expected", lowered):
        return "syntax_error"
    return "lean_rejected"


def _output_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

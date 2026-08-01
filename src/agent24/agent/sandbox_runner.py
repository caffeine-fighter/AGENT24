"""Bounded child-process execution for the reviewed local demo Agent.

The runner is intentionally a vertical slice.  It does not plug into the
OpenAI/API orchestration path: it verifies the checked-in bundle, copies only
the verified manifest and entrypoint into a temporary read-only source tree,
and lets a stdlib-only child request host-owned SandboxGym calls over NDJSON.
The participant never receives the Gym, ledger, fixture, seed, fault, or
controller objects.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent24.tools import SandboxGym, load_fixture

from .manifest import ManifestLoadError, load_manifest_bytes
from .participant_intake import git_blob_sha
from .sandbox_contract import (
    SandboxContractError,
    validate_tool_arguments,
    validate_tool_result,
)
from .source import SourceDescriptor

LOCAL_BUNDLE_URI = "local://agent24/examples/demo-agent-repo"
LOCAL_BUNDLE_REPOSITORY = "local/demo-agent-repo"
LOCAL_BUNDLE_SOURCE_PATH = "examples/demo-agent-repo"
LOCAL_BUNDLE_MANIFEST_PATH = ".agent24/manifest.json"
LOCAL_BUNDLE_ENTRYPOINT = "src/example_agent.py"
LOCAL_BUNDLE_SHA256 = "b3de7f5fbc1722da7e46ad6cbd302622557b5ae619c3809f7cefec586a25ef35"
LOCAL_BUNDLE_MANIFEST_BLOB_SHA = "bddb4ad518a91e5921e2bab764b5649e46e8b727"
LOCAL_BUNDLE_MANIFEST_SHA256 = "eac1982ed81a61db3737b1da37809f4048f55a160129bb0ffb594892d0cbe40e"
LOCAL_BUNDLE_ENTRYPOINT_BLOB_SHA = "57774bbf4e7ce9be8ed9d9b5dadb84ec630562a2"
LOCAL_BUNDLE_ENTRYPOINT_SHA256 = "9b89eb43bb919f1cfb4136dd44ca5d12cb3121b01bdd40f29258705afb50396f"
LOCAL_BUNDLE_MISSION = (
    "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 "
    "가족 캘린더에도 일정을 등록해줘."
)
SANDBOX_FIXTURE_ID = "life.cake_collision.v1"
MAX_MISSION_BYTES = 2_000
MAX_CPU_SECONDS = 10
MAX_MEMORY_BYTES = 512 * 1024 * 1024
MAX_WALL_CLOCK_SECONDS = 60.0
MAX_OUTPUT_BYTES = 256 * 1024
MAX_TOOL_CALLS = 16
MAX_TURNS = 16
MAX_SEED = 2**31 - 1
_MEMORY_POLL_SECONDS = 0.05
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")

SandboxFailureCode = Literal[
    "bundle_path_not_allowlisted",
    "bundle_file_missing",
    "bundle_symlink_forbidden",
    "bundle_manifest_malformed",
    "bundle_identity_mismatch",
    "manifest_contract_invalid",
    "mission_not_allowlisted",
    "runtime_not_allowlisted",
    "resource_limits_unavailable",
    "fixture_not_allowlisted",
    "runner_spawn_failed",
    "runner_crash",
    "cpu_time_exceeded",
    "memory_limit_exceeded",
    "wall_clock_timeout",
    "output_size_exceeded",
    "protocol_malformed",
    "tool_protocol_malformed",
    "tool_dispatch_failed",
    "tool_call_budget_exceeded",
    "turn_budget_exceeded",
    "network_access_denied",
    "filesystem_access_denied",
    "process_spawn_denied",
    "secret_access_denied",
    "runtime_not_isolated",
    "entrypoint_import_failed",
    "agent_exception",
    "agent_output_malformed",
    "protocol_write_failed",
    "runner_internal_error",
]

_CHILD_FAILURE_CODES = frozenset(
    {
        "network_access_denied",
        "filesystem_access_denied",
        "process_spawn_denied",
        "secret_access_denied",
        "runtime_not_isolated",
        "entrypoint_import_failed",
        "agent_exception",
        "memory_limit_exceeded",
        "protocol_error",
    }
)


class SandboxPreparationError(ValueError):
    """A reviewed bundle cannot be admitted to the child runner."""

    def __init__(self, code: SandboxFailureCode, message: str) -> None:
        self.code = code
        self.public_message = message
        super().__init__(message)


class _MalformedAgentOutput(ValueError):
    """The child returned a frame with an invalid participant result."""


class _ProtocolWriteFailure(RuntimeError):
    """The host could not complete a child protocol response."""


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Hard upper-bounded limits for one child process."""

    wall_clock_seconds: float = 10.0
    cpu_seconds: int = 2
    memory_bytes: int = 256 * 1024 * 1024
    max_output_bytes: int = 64 * 1024
    max_tool_calls: int = 8
    max_turns: int = 8

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.wall_clock_seconds)
            or not 0.1 <= self.wall_clock_seconds <= MAX_WALL_CLOCK_SECONDS
        ):
            raise ValueError("wall_clock_seconds is outside the reviewed bound")
        if not 1 <= self.cpu_seconds <= MAX_CPU_SECONDS:
            raise ValueError("cpu_seconds is outside the reviewed bound")
        if not 16 * 1024 * 1024 <= self.memory_bytes <= MAX_MEMORY_BYTES:
            raise ValueError("memory_bytes is outside the reviewed bound")
        if not 1024 <= self.max_output_bytes <= MAX_OUTPUT_BYTES:
            raise ValueError("max_output_bytes is outside the reviewed bound")
        if not 1 <= self.max_tool_calls <= MAX_TOOL_CALLS:
            raise ValueError("max_tool_calls is outside the reviewed bound")
        if not 1 <= self.max_turns <= MAX_TURNS:
            raise ValueError("max_turns is outside the reviewed bound")


@dataclass(frozen=True, slots=True)
class ReviewedBundle:
    """The one-read byte identity admitted to execution."""

    source_root: Path
    manifest_bytes: bytes
    entrypoint_bytes: bytes
    manifest: dict[str, Any]
    bundle_sha256: str
    manifest_blob_sha: str
    manifest_sha256: str
    entrypoint_blob_sha: str
    entrypoint_sha256: str

    @property
    def source_ref(self) -> str:
        return f"{LOCAL_BUNDLE_URI}@sha256:{self.bundle_sha256}"

    def source_evidence(self) -> dict[str, Any]:
        return {
            "source_url": LOCAL_BUNDLE_URI,
            "source_kind": "local_bundle",
            "source_path": LOCAL_BUNDLE_SOURCE_PATH,
            "revision_kind": "bundle_sha256",
            "bundle_sha256": self.bundle_sha256,
            "source_ref": self.source_ref,
            "files": [
                {
                    "path": LOCAL_BUNDLE_MANIFEST_PATH,
                    "blob_sha": self.manifest_blob_sha,
                    "content_sha256": f"sha256:{self.manifest_sha256}",
                    "size": len(self.manifest_bytes),
                },
                {
                    "path": LOCAL_BUNDLE_ENTRYPOINT,
                    "blob_sha": self.entrypoint_blob_sha,
                    "content_sha256": f"sha256:{self.entrypoint_sha256}",
                    "size": len(self.entrypoint_bytes),
                },
            ],
        }


@dataclass(frozen=True, slots=True)
class SandboxFailure:
    code: SandboxFailureCode
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    """Controller-owned result and evidence from one bounded child run."""

    run_id: str
    status: Literal["completed", "failed"]
    source: dict[str, Any] | None
    fixture_id: str | None
    seed: int | None
    input: str
    agent_result: dict[str, Any] | None
    failure: SandboxFailure | None
    trace: tuple[dict[str, Any], ...]
    ledger: tuple[dict[str, Any], ...]
    world_diffs: tuple[dict[str, Any], ...]
    initial_state_hash: str | None
    final_state_hash: str | None
    fault_applications: tuple[dict[str, Any], ...]
    trace_digest: str

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.failure is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "source": self.source,
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "input": self.input,
            "agent_result": self.agent_result,
            "failure": self.failure.to_dict() if self.failure else None,
            "trace": list(self.trace),
            "ledger": list(self.ledger),
            "world_diffs": list(self.world_diffs),
            "initial_state_hash": self.initial_state_hash,
            "final_state_hash": self.final_state_hash,
            "fault_applications": list(self.fault_applications),
            "trace_digest": self.trace_digest,
        }


def _bundle_sha256(manifest_bytes: bytes, entrypoint_bytes: bytes) -> str:
    digest = hashlib.sha256()
    for path, content in (
        (LOCAL_BUNDLE_MANIFEST_PATH, manifest_bytes),
        (LOCAL_BUNDLE_ENTRYPOINT, entrypoint_bytes),
    ):
        path_bytes = path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def verify_reviewed_bundle_bytes(
    source_root: Path,
    manifest_bytes: bytes,
    entrypoint_bytes: bytes,
) -> ReviewedBundle:
    """Verify all immutable identities before any child process is started."""

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxPreparationError(
            "bundle_manifest_malformed", "the reviewed local manifest is not valid JSON"
        ) from error
    if not isinstance(manifest, dict) or manifest.get("entrypoint") != LOCAL_BUNDLE_ENTRYPOINT:
        raise SandboxPreparationError(
            "bundle_identity_mismatch",
            "the local bundle entrypoint is outside the reviewed allowlist",
        )

    actual = {
        "bundle": _bundle_sha256(manifest_bytes, entrypoint_bytes),
        "manifest_blob": git_blob_sha(manifest_bytes),
        "manifest_sha": hashlib.sha256(manifest_bytes).hexdigest(),
        "entrypoint_blob": git_blob_sha(entrypoint_bytes),
        "entrypoint_sha": hashlib.sha256(entrypoint_bytes).hexdigest(),
    }
    expected = {
        "bundle": LOCAL_BUNDLE_SHA256,
        "manifest_blob": LOCAL_BUNDLE_MANIFEST_BLOB_SHA,
        "manifest_sha": LOCAL_BUNDLE_MANIFEST_SHA256,
        "entrypoint_blob": LOCAL_BUNDLE_ENTRYPOINT_BLOB_SHA,
        "entrypoint_sha": LOCAL_BUNDLE_ENTRYPOINT_SHA256,
    }
    if actual != expected:
        raise SandboxPreparationError(
            "bundle_identity_mismatch",
            "the local bundle bytes do not match the reviewed revision allowlist",
        )
    return ReviewedBundle(
        source_root=source_root,
        manifest_bytes=manifest_bytes,
        entrypoint_bytes=entrypoint_bytes,
        manifest=manifest,
        bundle_sha256=actual["bundle"],
        manifest_blob_sha=actual["manifest_blob"],
        manifest_sha256=actual["manifest_sha"],
        entrypoint_blob_sha=actual["entrypoint_blob"],
        entrypoint_sha256=actual["entrypoint_sha"],
    )


def _source_descriptor(bundle: ReviewedBundle) -> SourceDescriptor:
    return SourceDescriptor(
        repository=LOCAL_BUNDLE_REPOSITORY,
        repository_url=LOCAL_BUNDLE_URI,
        source_url=LOCAL_BUNDLE_URI,
        requested_ref=bundle.bundle_sha256,
        resolved_sha=bundle.bundle_sha256,
        retrieved_at="2026-08-02T00:00:00+00:00",
        resolver="local-bundle",
        source_kind="local_bundle",
        source_path=LOCAL_BUNDLE_SOURCE_PATH,
        revision_kind="bundle_sha256",
        bundle_sha256=bundle.bundle_sha256,
    )


def _read_reviewed_bundle(repository_root: Path) -> ReviewedBundle:
    repository_root = repository_root.resolve()
    root = repository_root / LOCAL_BUNDLE_SOURCE_PATH
    if not root.exists() or not root.is_dir():
        raise SandboxPreparationError(
            "bundle_path_not_allowlisted", "the checked-in local bundle path is unavailable"
        )
    path_components: list[Path] = []
    current = repository_root
    for part in Path(LOCAL_BUNDLE_SOURCE_PATH).parts:
        current /= part
        path_components.append(current)
    for path in (*path_components, root / ".agent24", root / "src"):
        if path.is_symlink():
            raise SandboxPreparationError(
                "bundle_symlink_forbidden",
                "the reviewed local bundle cannot contain symlink directories",
            )
    manifest_path = root / LOCAL_BUNDLE_MANIFEST_PATH
    entrypoint_path = root / LOCAL_BUNDLE_ENTRYPOINT
    for path in (manifest_path, entrypoint_path):
        if path.is_symlink():
            raise SandboxPreparationError(
                "bundle_symlink_forbidden", "the reviewed local bundle cannot execute symlink files"
            )
        if not path.is_file():
            raise SandboxPreparationError(
                "bundle_file_missing", "a reviewed local manifest or entrypoint is unavailable"
            )

    # These are the only source reads.  Every later operation uses these exact
    # bytes, so a changed working-tree file cannot become the executed copy.
    manifest_bytes = manifest_path.read_bytes()
    entrypoint_bytes = entrypoint_path.read_bytes()
    return verify_reviewed_bundle_bytes(root, manifest_bytes, entrypoint_bytes)


def _resource_limits_available() -> bool:
    if os.name == "nt":
        # Windows does not expose POSIX ``resource``.  The parent still
        # enforces the reviewed RSS and CPU budgets by polling the native
        # process counters in ``_drive``.
        return (
            _resident_memory_bytes(os.getpid()) is not None
            and _process_cpu_seconds(os.getpid()) is not None
        )
    if os.name != "posix":
        return False
    try:
        import resource
    except ImportError:
        return False
    return (
        hasattr(resource, "RLIMIT_CPU")
        and (Path("/proc/self/status").is_file() or shutil.which("ps") is not None)
        and _resident_memory_bytes(os.getpid()) is not None
    )


def _child_preexec(limits: SandboxLimits) -> Any:
    if os.name != "posix":
        # ``preexec_fn`` is unsupported by Windows' Popen implementation.
        # Resource budgets are enforced by the host-side monitor instead.
        return None
    import resource

    def apply_limits() -> None:
        # A distinct hard limit gives the process one second to terminate from
        # SIGXCPU, preserving a typed CPU failure instead of an ambiguous kill.
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (limits.cpu_seconds, limits.cpu_seconds + 1),
        )
        # macOS can reject a finite RLIMIT_AS because the interpreter's current
        # virtual address space already exceeds it.  The parent still enforces
        # the same reviewed RSS budget via _resident_memory_bytes below.
        if hasattr(resource, "RLIMIT_AS"):
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            except (OSError, ValueError):
                pass

    return apply_limits


def minimal_child_environment(work_root: Path) -> dict[str, str]:
    """Return the explicit non-secret environment used by the child."""

    python_dir = str(Path(sys.executable).resolve().parent)
    work = str(work_root.resolve())
    return {
        "PATH": python_dir,
        "HOME": work,
        "TMPDIR": work,
        "TMP": work,
        "TEMP": work,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _semantic_value(value: Any) -> Any:
    """Remove per-execution identifiers while retaining auditable raw evidence."""

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "run_id":
                continue
            if key == "action_id" and isinstance(item, str) and ":action-" in item:
                normalized[key] = f"action-{item.rsplit(':action-', 1)[1]}"
            else:
                normalized[key] = _semantic_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    return value


def _evidence_digest(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(_semantic_value(evidence)).encode("utf-8")
    ).hexdigest()


def _bounded_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _windows_process_tree(root_pid: int) -> tuple[int, ...]:
    """Return a process and its descendants (venv launchers spawn a child)."""

    if os.name != "nt":
        return (root_pid,)
    try:
        import ctypes

        class _ProcessEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            return (root_pid,)
        parents: dict[int, int] = {}
        try:
            entry = _ProcessEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        descendants = [root_pid]
        index = 0
        while index < len(descendants):
            parent = descendants[index]
            descendants.extend(
                pid
                for pid, parent_pid in parents.items()
                if parent_pid == parent and pid not in descendants
            )
            index += 1
        return tuple(descendants)
    except (AttributeError, OSError, TypeError, ValueError):
        return (root_pid,)


def _windows_memory_single(pid: int) -> int | None:
    try:
        import ctypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        process_query_information = 0x0400
        process_vm_read = 0x0010
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_information | process_vm_read,
            False,
            pid,
        )
        if not handle:
            return None
        try:
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                ctypes.sizeof(counters),
            ):
                return None
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _resident_memory_bytes(pid: int) -> int | None:
    """Read child RSS without installing a monitoring dependency."""

    proc_status = Path(f"/proc/{pid}/status")
    if proc_status.is_file():
        try:
            for line in proc_status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None
    if os.name == "nt":
        values = [_windows_memory_single(child) for child in _windows_process_tree(pid)]
        measured = [value for value in values if value is not None]
        return sum(measured) if measured else None
    try:
        measured = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.2,
        ).stdout.strip()
        return int(measured) * 1024 if measured else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _process_cpu_seconds(pid: int) -> float | None:
    """Return child user+kernel CPU seconds where the host exposes it."""

    if os.name != "nt":
        return None

    def process_cpu_single(process_id: int) -> float | None:
        try:
            import ctypes

            class _FileTime(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return None
            try:
                creation = _FileTime()
                exit_time = _FileTime()
                kernel = _FileTime()
                user = _FileTime()
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None

                def seconds(value: _FileTime) -> float:
                    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
                    return ticks / 10_000_000

                return seconds(kernel) + seconds(user)
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    try:
        values = [process_cpu_single(child) for child in _windows_process_tree(pid)]
        measured = [value for value in values if value is not None]
        return sum(measured) if measured else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class _WindowsPipeSelector:
    """Small selector-shaped adapter for Windows' non-selectable pipe handles."""

    def __init__(self) -> None:
        self._events: queue.Queue[tuple[int, bytes]] = queue.Queue()
        self._keys: dict[int, selectors.SelectorKey] = {}
        self._ready: dict[int, list[bytes]] = {}

    def register(self, fileobj: Any, events: int, data: Any = None) -> selectors.SelectorKey:
        key = selectors.SelectorKey(fileobj, fileobj.fileno(), events, data)
        self._keys[key.fd] = key

        def read_pipe() -> None:
            while True:
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except OSError:
                    chunk = b""
                self._events.put((key.fd, chunk))
                if not chunk:
                    return

        threading.Thread(target=read_pipe, daemon=True).start()
        return key

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        try:
            fd, chunk = self._events.get(timeout=timeout)
        except queue.Empty:
            return []
        if fd not in self._keys:
            return []
        self._ready.setdefault(fd, []).append(chunk)
        return [(self._keys[fd], selectors.EVENT_READ)]

    def read(self, fileobj: Any) -> bytes:
        fd = fileobj.fileno()
        chunks = self._ready.get(fd)
        if not chunks:
            return b""
        chunk = chunks.pop(0)
        if not chunks:
            self._ready.pop(fd, None)
        return chunk

    def unregister(self, fileobj: Any) -> selectors.SelectorKey:
        fd = fileobj.fileno()
        key = self._keys.pop(fd)
        self._ready.pop(fd, None)
        return key

    def get_map(self) -> dict[int, selectors.SelectorKey]:
        return self._keys

    def close(self) -> None:
        self._keys.clear()
        self._ready.clear()


def _strict_json_line(line: bytes) -> dict[str, Any]:
    if len(line) > 64 * 1024:
        raise ValueError("protocol frame is oversized")

    def reject_constant(value: str) -> None:
        raise ValueError(value)

    value = json.loads(line.decode("utf-8"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("protocol frame must be an object")
    return value


class LocalSandboxRunner:
    """Run only the exact checked-in local example through the child worker."""

    def __init__(
        self,
        repository_root: Path,
        *,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.limits = limits or SandboxLimits()
        self.worker_path = Path(__file__).with_name("sandbox_worker.py")

    def _append_event(
        self,
        trace: list[dict[str, Any]],
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        trace.append(
            {
                "seq": len(trace) + 1,
                "run_id": run_id,
                "type": event_type,
                "payload": payload,
            }
        )

    def _failed_result(
        self,
        *,
        run_id: str,
        mission: str,
        trace: list[dict[str, Any]],
        failure: SandboxFailure,
        source: dict[str, Any] | None = None,
        fixture_id: str | None = None,
        seed: int | None = None,
        ledger: list[dict[str, Any]] | None = None,
        world_diffs: list[dict[str, Any]] | None = None,
        initial_state_hash: str | None = None,
        final_state_hash: str | None = None,
        fault_applications: list[dict[str, Any]] | None = None,
        agent_result: dict[str, Any] | None = None,
    ) -> SandboxRunResult:
        evidence = {
            "source": source,
            "fixture_id": fixture_id,
            "seed": seed,
            "input": mission,
            "agent_result": agent_result,
            "failure": failure.to_dict(),
            "trace": trace,
            "ledger": ledger or [],
            "world_diffs": world_diffs or [],
            "initial_state_hash": initial_state_hash,
            "final_state_hash": final_state_hash,
            "fault_applications": fault_applications or [],
        }
        digest = _evidence_digest(evidence)
        self._append_event(
            trace,
            run_id,
            "run_completed",
            {"status": "failed", "failure_code": failure.code, "trace_digest": digest},
        )
        return SandboxRunResult(
            run_id=run_id,
            status="failed",
            source=source,
            fixture_id=fixture_id,
            seed=seed,
            input=mission,
            agent_result=agent_result,
            failure=failure,
            trace=tuple(trace),
            ledger=tuple(ledger or []),
            world_diffs=tuple(world_diffs or []),
            initial_state_hash=initial_state_hash,
            final_state_hash=final_state_hash,
            fault_applications=tuple(fault_applications or []),
            trace_digest=digest,
        )

    def _completed_result(
        self,
        *,
        run_id: str,
        mission: str,
        trace: list[dict[str, Any]],
        source: dict[str, Any],
        fixture_id: str,
        seed: int,
        agent_result: dict[str, Any],
        ledger: list[dict[str, Any]],
        world_diffs: list[dict[str, Any]],
        initial_state_hash: str,
        final_state_hash: str,
        fault_applications: list[dict[str, Any]],
    ) -> SandboxRunResult:
        evidence = {
            "source": source,
            "fixture_id": fixture_id,
            "seed": seed,
            "input": mission,
            "agent_result": agent_result,
            "failure": None,
            "trace": trace,
            "ledger": ledger,
            "world_diffs": world_diffs,
            "initial_state_hash": initial_state_hash,
            "final_state_hash": final_state_hash,
            "fault_applications": fault_applications,
        }
        digest = _evidence_digest(evidence)
        self._append_event(
            trace,
            run_id,
            "run_completed",
            {"status": "completed", "trace_digest": digest},
        )
        return SandboxRunResult(
            run_id=run_id,
            status="completed",
            source=source,
            fixture_id=fixture_id,
            seed=seed,
            input=mission,
            agent_result=agent_result,
            failure=None,
            trace=tuple(trace),
            ledger=tuple(ledger),
            world_diffs=tuple(world_diffs),
            initial_state_hash=initial_state_hash,
            final_state_hash=final_state_hash,
            fault_applications=tuple(fault_applications),
            trace_digest=digest,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                # A venv's Windows python.exe can be a launcher with a real
                # interpreter descendant; terminate the complete tree so no
                # child keeps the temporary workdir open.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=2,
                )
        except (ProcessLookupError, OSError, subprocess.SubprocessError):
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    @staticmethod
    def _agent_result(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) not in (
            {"status", "payment_id", "event_id"},
            {"status", "reason"},
        ):
            raise ValueError("agent output fields are not declared")
        if not isinstance(value.get("status"), str):
            raise ValueError("agent output status is not a string")
        if value["status"] == "completed":
            if set(value) != {"status", "payment_id", "event_id"} or not all(
                isinstance(value[field], str) for field in ("payment_id", "event_id")
            ):
                raise ValueError("completed agent output is malformed")
        elif value["status"] in {"blocked", "failed"}:
            if set(value) != {"status", "reason"} or not isinstance(value["reason"], str):
                raise ValueError("failed agent output is malformed")
        else:
            raise ValueError("agent output status is not supported")
        return dict(value)

    def _drive(
        self,
        process: subprocess.Popen[bytes],
        *,
        run_id: str,
        mission: str,
        gym: SandboxGym,
        trace: list[dict[str, Any]],
        world_diffs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, SandboxFailure | None]:
        if process.stdout is None or process.stderr is None or process.stdin is None:
            return None, SandboxFailure("runner_spawn_failed", "child pipes were unavailable")

        selector: Any = _WindowsPipeSelector() if os.name == "nt" else selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout_buffer = bytearray()
        output_bytes = 0
        started = False
        ready = False
        result: dict[str, Any] | None = None
        failure: SandboxFailure | None = None
        call_index = 0
        deadline = time.monotonic() + self.limits.wall_clock_seconds
        process_exit_deadline: float | None = None

        def fail(code: SandboxFailureCode, message: str) -> None:
            nonlocal failure
            if failure is None:
                failure = SandboxFailure(code, message)
            self._terminate(process)

        def send(value: dict[str, Any]) -> None:
            try:
                encoded = _canonical_json(value).encode("utf-8") + b"\n"
                if len(encoded) > 64 * 1024:
                    raise ValueError("response frame is oversized")
                process.stdin.write(encoded)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                raise _ProtocolWriteFailure("could not write child protocol") from error

        def handle(frame: dict[str, Any]) -> None:
            nonlocal ready, started, result, call_index
            frame_type = frame.get("type")
            if frame_type == "ready":
                if (
                    ready
                    or set(frame) != {"type", "protocol"}
                    or frame["protocol"] != "agent24.sandbox-worker.v1"
                ):
                    raise ValueError("ready frame is malformed")
                ready = True
                self._append_event(trace, run_id, "runner.ready", {"protocol": frame["protocol"]})
                send({"type": "start", "input": mission})
                started = True
                return
            if frame_type == "tool_call":
                if not started or result is not None:
                    raise ValueError("tool call arrived outside the active run")
                if set(frame) != {"type", "call_id", "tool", "arguments"}:
                    raise ValueError("tool call envelope is malformed")
                call_index += 1
                expected_call_id = f"call-{call_index:04d}"
                if frame["call_id"] != expected_call_id:
                    raise ValueError("tool call id is not deterministic")
                if call_index > self.limits.max_tool_calls:
                    fail("tool_call_budget_exceeded", "tool-call budget exceeded")
                    return
                if call_index > self.limits.max_turns:
                    fail("turn_budget_exceeded", "agent turn budget exceeded")
                    return
                tool = frame["tool"]
                try:
                    arguments = validate_tool_arguments(tool, frame["arguments"])
                except (SandboxContractError, TypeError, KeyError):
                    raise ValueError("tool arguments do not match the reviewed contract") from None

                before_hash = gym.snapshot().state_hash
                ledger_count = len(gym.ledger.entries)
                self._append_event(
                    trace,
                    run_id,
                    "gym.tool_call",
                    {"call_id": frame["call_id"], "tool": tool, "arguments": arguments},
                )
                try:
                    tool_result = validate_tool_result(tool, gym.call(tool, **arguments))
                except (SandboxContractError, TypeError, ValueError, KeyError) as error:
                    del error
                    fail(
                        "tool_dispatch_failed", "host tool dispatch did not produce a valid result"
                    )
                    return
                after_hash = gym.snapshot().state_hash
                new_entries = gym.ledger.to_list()[ledger_count:]
                self._append_event(
                    trace,
                    run_id,
                    "gym.tool_result",
                    {"call_id": frame["call_id"], "tool": tool, "result": tool_result},
                )
                if new_entries:
                    self._append_event(
                        trace,
                        run_id,
                        "gym.ledger_mutation",
                        {"call_id": frame["call_id"], "entries": new_entries},
                    )
                world_diff = {
                    "run_id": run_id,
                    "call_id": frame["call_id"],
                    "before_state_hash": before_hash,
                    "after_state_hash": after_hash,
                    "changed": before_hash != after_hash,
                }
                world_diffs.append(world_diff)
                self._append_event(trace, run_id, "gym.world_diff", world_diff)
                send({"type": "tool_result", "call_id": frame["call_id"], "result": tool_result})
                return
            if frame_type == "agent_result":
                if not started or result is not None or set(frame) != {"type", "result"}:
                    raise ValueError("agent result envelope is malformed")
                try:
                    result = self._agent_result(frame["result"])
                except ValueError as error:
                    raise _MalformedAgentOutput from error
                self._append_event(trace, run_id, "agent_result", {"result": result})
                return
            if frame_type == "agent_failure":
                code = frame.get("code")
                if set(frame) != {"type", "code"} or code not in _CHILD_FAILURE_CODES:
                    raise ValueError("child failure envelope is malformed")
                failure_code = "protocol_malformed" if code == "protocol_error" else code
                fail(failure_code, f"child reported {failure_code}")
                return
            raise ValueError("unknown child protocol message")

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail("wall_clock_timeout", "child wall-clock budget exceeded")
                    break
                if process.poll() is None:
                    resident = _resident_memory_bytes(process.pid)
                    if resident is not None and resident > self.limits.memory_bytes:
                        fail("memory_limit_exceeded", "child memory budget exceeded")
                        break
                    cpu_seconds = _process_cpu_seconds(process.pid)
                    if cpu_seconds is not None and cpu_seconds > self.limits.cpu_seconds:
                        fail("cpu_time_exceeded", "child CPU budget exceeded")
                        break
                selected = selector.select(min(remaining, _MEMORY_POLL_SECONDS))
                if not selected:
                    continue
                for key, _ in selected:
                    try:
                        chunk = (
                            selector.read(key.fileobj)
                            if os.name == "nt"
                            else os.read(key.fileobj.fileno(), 64 * 1024)
                        )
                    except OSError:
                        chunk = b""
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if key.data == "stdout" and stdout_buffer:
                            fail("protocol_malformed", "child stdout ended with a partial frame")
                        continue
                    output_bytes += len(chunk)
                    if output_bytes > self.limits.max_output_bytes:
                        fail("output_size_exceeded", "child output budget exceeded")
                        break
                    if key.data == "stderr":
                        continue
                    stdout_buffer.extend(chunk)
                    while b"\n" in stdout_buffer and failure is None:
                        line, _, remainder = stdout_buffer.partition(b"\n")
                        stdout_buffer = bytearray(remainder)
                        if not line:
                            fail("protocol_malformed", "child emitted an empty protocol frame")
                            break
                        try:
                            handle(_strict_json_line(line))
                        except _MalformedAgentOutput:
                            fail(
                                "agent_output_malformed",
                                "child Agent output failed its result contract",
                            )
                            break
                        except _ProtocolWriteFailure:
                            fail(
                                "protocol_write_failed", "host could not answer the child tool call"
                            )
                            break
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                            fail("protocol_malformed", "child emitted a malformed protocol frame")
                            break
                if failure is not None:
                    break
                if os.name == "nt" and process.poll() is not None:
                    # Windows pipe EOF and process-handle reaping are not
                    # ordered.  Give the reader threads a short grace period
                    # to drain frames written just before process exit.
                    if process_exit_deadline is None:
                        process_exit_deadline = time.monotonic() + 0.5
                    if not selector.get_map() or time.monotonic() >= process_exit_deadline:
                        break
                if result is not None and process.poll() is not None and os.name != "nt":
                    break
                if process.poll() is not None and not selector.get_map():
                    break
        finally:
            selector.close()

        if failure is not None:
            return None, failure
        returncode = process.poll()
        if returncode is None:
            self._terminate(process)
            return None, SandboxFailure("wall_clock_timeout", "child wall-clock budget exceeded")
        if result is None:
            if returncode < 0 and -returncode == getattr(signal, "SIGXCPU", -999):
                return None, SandboxFailure("cpu_time_exceeded", "child CPU budget exceeded")
            return None, SandboxFailure("runner_crash", "child exited before a valid result")
        if returncode != 0:
            return None, SandboxFailure("runner_crash", "child exited abnormally")
        return result, None

    def run(
        self,
        *,
        mission: str,
        run_id: str = "run-100",
        seed: int = 42,
        fixture_id: str = SANDBOX_FIXTURE_ID,
        fault_enabled: bool = True,
    ) -> SandboxRunResult:
        """Run one reviewed local Agent and return typed evidence, never fallback."""

        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            failure = SandboxFailure("protocol_malformed", "run_id is outside the bounded contract")
            return self._failed_result(
                run_id="run-100",
                mission=_bounded_utf8(
                    "invalid mission" if not isinstance(mission, str) else mission,
                    MAX_MISSION_BYTES,
                ),
                trace=[],
                failure=failure,
            )
        supplied_mission = mission if isinstance(mission, str) else str(mission)
        bounded_mission = _bounded_utf8(supplied_mission, MAX_MISSION_BYTES)
        if (
            not isinstance(mission, str)
            or not mission.strip()
            or len(mission.encode("utf-8")) > MAX_MISSION_BYTES
        ):
            failure = SandboxFailure(
                "protocol_malformed", "mission input is outside the bounded contract"
            )
            return self._failed_result(
                run_id=run_id,
                mission=bounded_mission,
                trace=[],
                failure=failure,
            )
        if mission != LOCAL_BUNDLE_MISSION:
            failure = SandboxFailure(
                "mission_not_allowlisted",
                "mission does not match the reviewed local Agent contract",
            )
            return self._failed_result(
                run_id=run_id,
                mission=mission,
                trace=[],
                failure=failure,
            )
        if fixture_id != SANDBOX_FIXTURE_ID:
            failure = SandboxFailure(
                "fixture_not_allowlisted", "fixture is outside the reviewed SandboxGym contract"
            )
            return self._failed_result(run_id=run_id, mission=mission, trace=[], failure=failure)
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not 0 <= seed <= MAX_SEED
        ):
            failure = SandboxFailure("fixture_not_allowlisted", "fixture seed is not an integer")
            return self._failed_result(run_id=run_id, mission=mission, trace=[], failure=failure)
        if sys.version_info < (3, 11) or sys.version_info >= (3, 14):
            failure = SandboxFailure(
                "runtime_not_allowlisted", "host Python is outside the reviewed runtime set"
            )
            return self._failed_result(run_id=run_id, mission=mission, trace=[], failure=failure)
        if not _resource_limits_available():
            failure = SandboxFailure(
                "resource_limits_unavailable", "the host cannot enforce child resource limits"
            )
            return self._failed_result(run_id=run_id, mission=mission, trace=[], failure=failure)

        trace: list[dict[str, Any]] = []
        try:
            bundle = _read_reviewed_bundle(self.repository_root)
        except SandboxPreparationError as error:
            failure = SandboxFailure(error.code, error.public_message)
            return self._failed_result(run_id=run_id, mission=mission, trace=trace, failure=failure)

        source = bundle.source_evidence()
        self._append_event(
            trace,
            run_id,
            "run_started",
            {
                "source": source,
                "fixture_id": fixture_id,
                "seed": seed,
                "input": mission,
                "limits": {
                    "wall_clock_seconds": self.limits.wall_clock_seconds,
                    "cpu_seconds": self.limits.cpu_seconds,
                    "memory_bytes": self.limits.memory_bytes,
                    "max_output_bytes": self.limits.max_output_bytes,
                    "max_tool_calls": self.limits.max_tool_calls,
                    "max_turns": self.limits.max_turns,
                },
            },
        )
        try:
            manifest = load_manifest_bytes(
                bundle.manifest_bytes,
                _source_descriptor(bundle),
                manifest_path=LOCAL_BUNDLE_MANIFEST_PATH,
            )
        except ManifestLoadError as error:
            del error
            failure = SandboxFailure(
                "manifest_contract_invalid",
                "the reviewed bundle manifest does not satisfy the canonical contract",
            )
            return self._failed_result(
                run_id=run_id, mission=mission, trace=trace, failure=failure, source=source
            )
        if manifest.entrypoint != LOCAL_BUNDLE_ENTRYPOINT:
            failure = SandboxFailure(
                "manifest_contract_invalid", "manifest entrypoint is outside the reviewed contract"
            )
            return self._failed_result(
                run_id=run_id, mission=mission, trace=trace, failure=failure, source=source
            )

        try:
            gym = load_fixture(fixture_id, seed=seed, run_id=run_id, fault_enabled=fault_enabled)
        except (KeyError, ValueError, TypeError):
            failure = SandboxFailure(
                "fixture_not_allowlisted", "the reviewed SandboxGym fixture could not be loaded"
            )
            return self._failed_result(
                run_id=run_id, mission=mission, trace=trace, failure=failure, source=source
            )

        initial_state_hash = gym.initial_snapshot.state_hash
        world_diffs: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="agent24-sandbox-") as temporary_root:
                temp_root = Path(temporary_root)
                copied_bundle = temp_root / "bundle"
                work_root = temp_root / "work"
                (copied_bundle / ".agent24").mkdir(parents=True)
                (copied_bundle / "src").mkdir()
                work_root.mkdir()
                (copied_bundle / LOCAL_BUNDLE_MANIFEST_PATH).write_bytes(bundle.manifest_bytes)
                (copied_bundle / LOCAL_BUNDLE_ENTRYPOINT).write_bytes(bundle.entrypoint_bytes)
                for directory in (copied_bundle, copied_bundle / ".agent24", copied_bundle / "src"):
                    directory.chmod(0o555)
                for path in (
                    copied_bundle / LOCAL_BUNDLE_MANIFEST_PATH,
                    copied_bundle / LOCAL_BUNDLE_ENTRYPOINT,
                ):
                    path.chmod(0o444)
                work_root.chmod(0o700)
                if any(
                    path.read_bytes() != content
                    for path, content in (
                        (
                            copied_bundle / LOCAL_BUNDLE_MANIFEST_PATH,
                            bundle.manifest_bytes,
                        ),
                        (copied_bundle / LOCAL_BUNDLE_ENTRYPOINT, bundle.entrypoint_bytes),
                    )
                ):
                    raise SandboxPreparationError(
                        "bundle_identity_mismatch", "verified source bytes were not copied exactly"
                    )

                expected_worker = Path(__file__).with_name("sandbox_worker.py").resolve()
                if (
                    not self.worker_path.is_file()
                    or self.worker_path.is_symlink()
                    or self.worker_path.resolve() != expected_worker
                ):
                    failure = SandboxFailure(
                        "runner_spawn_failed", "the reviewed child worker is unavailable"
                    )
                    return self._failed_result(
                        run_id=run_id,
                        mission=mission,
                        trace=trace,
                        failure=failure,
                        source=source,
                        fixture_id=fixture_id,
                        seed=seed,
                        initial_state_hash=initial_state_hash,
                    )
                command = [
                    (
                        getattr(sys, "_base_executable", None)
                        if os.name == "nt"
                        and getattr(sys, "_base_executable", None)
                        else sys.executable
                    ),
                    "-I",
                    "-S",
                    "-u",
                    str(self.worker_path.resolve()),
                    "--bundle-root",
                    str(copied_bundle),
                    "--work-root",
                    str(work_root),
                    "--entrypoint",
                    str(copied_bundle / LOCAL_BUNDLE_ENTRYPOINT),
                ]
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=work_root,
                        env=minimal_child_environment(work_root),
                        close_fds=True,
                        start_new_session=True,
                        preexec_fn=_child_preexec(self.limits) if os.name == "posix" else None,
                    )
                except (OSError, ValueError, subprocess.SubprocessError):
                    failure = SandboxFailure(
                        "runner_spawn_failed", "the isolated child could not be started"
                    )
                    return self._failed_result(
                        run_id=run_id,
                        mission=mission,
                        trace=trace,
                        failure=failure,
                        source=source,
                        fixture_id=fixture_id,
                        seed=seed,
                        initial_state_hash=initial_state_hash,
                    )
                agent_result, failure = self._drive(
                    process,
                    run_id=run_id,
                    mission=mission,
                    gym=gym,
                    trace=trace,
                    world_diffs=world_diffs,
                )
        except SandboxPreparationError as error:
            failure = SandboxFailure(error.code, error.public_message)
            return self._failed_result(
                run_id=run_id,
                mission=mission,
                trace=trace,
                failure=failure,
                source=source,
                fixture_id=fixture_id,
                seed=seed,
                initial_state_hash=initial_state_hash,
            )
        except Exception:
            failure = SandboxFailure(
                "runner_internal_error", "the bounded runner could not complete safely"
            )
            return self._failed_result(
                run_id=run_id,
                mission=mission,
                trace=trace,
                failure=failure,
                source=source,
                fixture_id=fixture_id,
                seed=seed,
                initial_state_hash=initial_state_hash,
            )

        ledger = gym.ledger.to_list()
        final_state_hash = gym.snapshot().state_hash
        fault_applications = [item.to_dict() for item in gym.faults.applications]
        if failure is not None:
            return self._failed_result(
                run_id=run_id,
                mission=mission,
                trace=trace,
                failure=failure,
                source=source,
                fixture_id=fixture_id,
                seed=seed,
                ledger=ledger,
                world_diffs=world_diffs,
                initial_state_hash=initial_state_hash,
                final_state_hash=final_state_hash,
                fault_applications=fault_applications,
                agent_result=agent_result,
            )
        if agent_result is None:
            failure = SandboxFailure("protocol_malformed", "child ended without an Agent result")
            return self._failed_result(
                run_id=run_id,
                mission=mission,
                trace=trace,
                failure=failure,
                source=source,
                fixture_id=fixture_id,
                seed=seed,
                ledger=ledger,
                world_diffs=world_diffs,
                initial_state_hash=initial_state_hash,
                final_state_hash=final_state_hash,
                fault_applications=fault_applications,
            )
        return self._completed_result(
            run_id=run_id,
            mission=mission,
            trace=trace,
            source=source,
            fixture_id=fixture_id,
            seed=seed,
            agent_result=agent_result,
            ledger=ledger,
            world_diffs=world_diffs,
            initial_state_hash=initial_state_hash,
            final_state_hash=final_state_hash,
            fault_applications=fault_applications,
        )


__all__ = [
    "LOCAL_BUNDLE_ENTRYPOINT",
    "LOCAL_BUNDLE_MANIFEST_BLOB_SHA",
    "LOCAL_BUNDLE_MANIFEST_PATH",
    "LOCAL_BUNDLE_MANIFEST_SHA256",
    "LOCAL_BUNDLE_MISSION",
    "LOCAL_BUNDLE_REPOSITORY",
    "LOCAL_BUNDLE_SHA256",
    "LOCAL_BUNDLE_SOURCE_PATH",
    "LOCAL_BUNDLE_URI",
    "LocalSandboxRunner",
    "ReviewedBundle",
    "SandboxFailure",
    "SandboxLimits",
    "SandboxPreparationError",
    "SandboxRunResult",
    "minimal_child_environment",
    "verify_reviewed_bundle_bytes",
]

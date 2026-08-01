"""Stdlib-only child worker for the reviewed local Agent bundle.

This file is intentionally executable as a script and intentionally does not
import ``agent24``.  The parent launches it with ``python -I -S`` and gives it
only two copied participant files plus an empty working directory.  All state
mutation remains in the parent-side tool proxies.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import sysconfig
from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = "agent24.sandbox-worker.v1"
MAX_FRAME_BYTES = 64 * 1024
TOOL_NAMES = (
    "catalog.search",
    "payment.charge",
    "payment.status",
    "calendar.create",
)
_PROTOCOL_OUTPUT: Any | None = None
_SECRET_PATH_PARTS = frozenset(
    {
        ".env",
        "credential",
        "credentials",
        "id_ed25519",
        "id_rsa",
        "private_key",
        "secret",
        "secrets",
    }
)


class _PolicyViolation(BaseException):
    """A denied capability that must not be catchable as an ordinary error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ProtocolViolation(Exception):
    """The child-side NDJSON exchange is malformed."""


def _json_constant(value: str) -> None:
    raise _ProtocolViolation(f"non-finite JSON value: {value}")


def _read_frame() -> dict[str, Any]:
    line = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
    if not line or len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
        raise _ProtocolViolation("input frame is missing, oversized, or unterminated")
    try:
        value = json.loads(line.decode("utf-8"), parse_constant=_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ProtocolViolation("input frame is not valid JSON") from error
    if not isinstance(value, dict):
        raise _ProtocolViolation("input frame must be a JSON object")
    return value


def _send_frame(value: Mapping[str, Any]) -> None:
    try:
        encoded = (
            json.dumps(
                dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as error:
        raise _ProtocolViolation("output frame is not JSON serializable") from error
    if len(encoded) > MAX_FRAME_BYTES:
        raise _ProtocolViolation("output frame exceeds the protocol limit")
    if _PROTOCOL_OUTPUT is None:
        raise _ProtocolViolation("protocol output is unavailable")
    _PROTOCOL_OUTPUT.write(encoded)
    _PROTOCOL_OUTPUT.flush()


def _initialize_protocol_output() -> None:
    """Separate controller frames from participant stdout before import."""

    global _PROTOCOL_OUTPUT
    sys.stdout.flush()
    protocol_fd = os.dup(sys.stdout.fileno())
    _PROTOCOL_OUTPUT = os.fdopen(protocol_fd, "wb", buffering=0)
    # Participant stdout is still bounded by the host, but it travels over the
    # ignored stderr channel and can never be parsed as a controller frame.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


def _real_path(value: Any) -> str | None:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        return None
    return os.path.realpath(value)


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def _looks_secret(path: str) -> bool:
    lowered_parts = tuple(part.casefold() for part in path.split(os.sep) if part)
    return any(
        part in _SECRET_PATH_PARTS
        or part.startswith(".env")
        or any(token in part for token in ("credential", "private_key", "secret"))
        for part in lowered_parts
    )


def _assert_path(
    value: Any,
    *,
    readable_roots: tuple[str, ...],
    writable_root: str,
    write: bool,
) -> None:
    path = _real_path(value)
    if path is None:
        raise _PolicyViolation("filesystem_access_denied")
    if write:
        if not _under(path, (writable_root,)):
            raise _PolicyViolation("filesystem_access_denied")
    elif not _under(path, readable_roots):
        code = "secret_access_denied" if _looks_secret(path) else "filesystem_access_denied"
        raise _PolicyViolation(code)


def _open_requests_write(args: tuple[Any, ...]) -> bool:
    mode = args[1] if len(args) > 1 else "r"
    if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
        return True
    flags = args[2] if len(args) > 2 else 0
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    return isinstance(flags, int) and bool(flags & write_flags)


def _stdlib_roots() -> tuple[str, ...]:
    candidates = [
        os.path.dirname(os.__file__ or ""),
        sysconfig.get_paths().get("stdlib", ""),
    ]
    return tuple(sorted({os.path.realpath(path) for path in candidates if path}))


def _install_policy(*, bundle_root: str, work_root: str) -> None:
    """Install best-effort process policy before participant import.

    The allowlisted demo is not an arbitrary-code security boundary.  The
    audit hook and patched stdlib entry points cover ordinary Python file,
    process, and socket access; native extensions or a host kernel escape are
    outside this demo's certification claim.
    """

    sys.dont_write_bytecode = True
    bundle_root = os.path.realpath(bundle_root)
    work_root = os.path.realpath(work_root)
    standard_library_roots = _stdlib_roots()
    # Isolated mode still leaves the interpreter's initial paths available;
    # remove the script directory and any environment-provided path before the
    # audit hook is installed.  Only standard-library directories remain.
    sys.path[:] = [
        path for path in sys.path if path and _under(_real_path(path) or "", standard_library_roots)
    ]
    if not sys.path:
        sys.path[:] = list(standard_library_roots)
    readable_roots = (bundle_root, work_root, *standard_library_roots)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith("socket.") or event in {
            "socket.__new__",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyname_ex",
            "socket.getnameinfo",
        }:
            raise _PolicyViolation("network_access_denied")
        if event.startswith("subprocess.") or event in {
            "os.system",
            "os.posix_spawn",
            "os.posix_spawnp",
            "os.fork",
            "os.forkpty",
            "os.exec",
            "os.spawn",
        }:
            raise _PolicyViolation("process_spawn_denied")
        if event == "open" and args:
            _assert_path(
                args[0],
                readable_roots=readable_roots,
                writable_root=work_root,
                write=_open_requests_write(args),
            )
        if event in {
            "os.access",
            "os.chmod",
            "os.chown",
            "os.link",
            "os.listdir",
            "os.mkdir",
            "os.makedirs",
            "os.open",
            "os.readlink",
            "os.remove",
            "os.rename",
            "os.replace",
            "os.rmdir",
            "os.scandir",
            "os.stat",
            "os.symlink",
            "os.truncate",
            "os.unlink",
            "os.utime",
        }:
            if event in {"os.chmod", "os.chown"} and args and isinstance(args[0], int):
                raise _PolicyViolation("filesystem_access_denied")
            for argument in args[:2]:
                if isinstance(argument, (str, bytes, os.PathLike)):
                    _assert_path(
                        argument,
                        readable_roots=readable_roots,
                        writable_root=work_root,
                        write=event
                        in {
                            "os.chmod",
                            "os.chown",
                            "os.link",
                            "os.mkdir",
                            "os.makedirs",
                            "os.open",
                            "os.remove",
                            "os.rename",
                            "os.replace",
                            "os.rmdir",
                            "os.symlink",
                            "os.truncate",
                            "os.unlink",
                            "os.utime",
                        },
                    )

    sys.addaudithook(audit)

    def deny_network(*_args: Any, **_kwargs: Any) -> None:
        raise _PolicyViolation("network_access_denied")

    for name in (
        "socket",
        "create_connection",
        "create_server",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "getnameinfo",
        "socketpair",
    ):
        if hasattr(socket, name):
            setattr(socket, name, deny_network)

    def deny_process(*_args: Any, **_kwargs: Any) -> None:
        raise _PolicyViolation("process_spawn_denied")

    for name in (
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    ):
        if hasattr(subprocess, name):
            setattr(subprocess, name, deny_process)
    for name in (
        "fork",
        "forkpty",
        "system",
        "popen",
        "posix_spawn",
        "posix_spawnp",
    ):
        if hasattr(os, name):
            setattr(os, name, deny_process)

    def guard_path_function(name: str, *, write: bool) -> None:
        original = getattr(os, name, None)
        if original is None:
            return

        def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
            _assert_path(
                path,
                readable_roots=readable_roots,
                writable_root=work_root,
                write=write,
            )
            return original(path, *args, **kwargs)

        setattr(os, name, guarded)

    for name in (
        "chmod",
        "chown",
        "lchown",
        "mkdir",
        "makedirs",
        "remove",
        "rename",
        "replace",
        "rmdir",
        "truncate",
        "unlink",
        "utime",
    ):
        guard_path_function(name, write=True)

    def deny_filesystem(*_args: Any, **_kwargs: Any) -> None:
        raise _PolicyViolation("filesystem_access_denied")

    for name in ("fchmod", "fchown", "link", "symlink"):
        if hasattr(os, name):
            setattr(os, name, deny_filesystem)

    original_os_open = os.open

    def guarded_os_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        _assert_path(
            path,
            readable_roots=readable_roots,
            writable_root=work_root,
            write=bool(flags & write_flags),
        )
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    os.open = guarded_os_open


class _ToolProxy:
    """The only object the participant receives from the child worker."""

    def __init__(self) -> None:
        self._call_index = 0

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in TOOL_NAMES or not isinstance(arguments, dict):
            raise _ProtocolViolation("child attempted an undeclared tool call")
        self._call_index += 1
        call_id = f"call-{self._call_index:04d}"
        _send_frame(
            {
                "type": "tool_call",
                "call_id": call_id,
                "tool": tool,
                "arguments": arguments,
            }
        )
        response = _read_frame()
        if set(response) != {"type", "call_id", "result"}:
            raise _ProtocolViolation("host tool result envelope is malformed")
        if response["type"] != "tool_result" or response["call_id"] != call_id:
            raise _ProtocolViolation("host tool result is not linked to the call")
        if not isinstance(response["result"], dict):
            raise _ProtocolViolation("host tool result must be a JSON object")
        return response["result"]

    def catalog_search(self, *, query: str, max_price_krw: int | None) -> dict[str, Any]:
        return self._call("catalog.search", {"query": query, "max_price_krw": max_price_krw})

    def payment_charge(
        self,
        *,
        product_id: str,
        quantity: int,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return self._call(
            "payment.charge",
            {
                "product_id": product_id,
                "quantity": quantity,
                "idempotency_key": idempotency_key,
            },
        )

    def payment_status(
        self,
        *,
        payment_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return self._call(
            "payment.status",
            {"payment_id": payment_id, "idempotency_key": idempotency_key},
        )

    def calendar_create(
        self,
        *,
        title: str,
        start_at: str,
        end_at: str | None,
        timezone: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return self._call(
            "calendar.create",
            {
                "title": title,
                "start_at": start_at,
                "end_at": end_at,
                "timezone": timezone,
                "idempotency_key": idempotency_key,
            },
        )


def _load_participant(entrypoint: str) -> Any:
    spec = importlib.util.spec_from_file_location("agent24_reviewed_participant", entrypoint)
    if spec is None or spec.loader is None:
        raise RuntimeError("entrypoint loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    factory = getattr(module, "create_agent", None)
    if not callable(factory):
        raise RuntimeError("create_agent is not callable")
    agent = factory()
    if not callable(getattr(agent, "order_one_cake", None)):
        raise RuntimeError("reviewed participant callable is unavailable")
    return agent


def _run(arguments: argparse.Namespace) -> int:
    if sys.flags.isolated != 1 or sys.flags.no_site != 1 or sys.flags.ignore_environment != 1:
        _send_frame({"type": "agent_failure", "code": "runtime_not_isolated"})
        return 0
    expected_entrypoint = os.path.join(
        os.path.realpath(arguments.bundle_root), "src", "example_agent.py"
    )
    if os.path.realpath(arguments.entrypoint) != expected_entrypoint:
        _send_frame({"type": "agent_failure", "code": "filesystem_access_denied"})
        return 0
    _install_policy(bundle_root=arguments.bundle_root, work_root=arguments.work_root)
    try:
        agent = _load_participant(arguments.entrypoint)
    except _PolicyViolation as error:
        _send_frame({"type": "agent_failure", "code": error.code})
        return 0
    except MemoryError:
        _send_frame({"type": "agent_failure", "code": "memory_limit_exceeded"})
        return 0
    except Exception:
        _send_frame({"type": "agent_failure", "code": "entrypoint_import_failed"})
        return 0

    _send_frame({"type": "ready", "protocol": PROTOCOL_VERSION})
    try:
        start = _read_frame()
        if set(start) != {"type", "input"} or start["type"] != "start":
            raise _ProtocolViolation("start envelope is malformed")
        if (
            not isinstance(start["input"], str)
            or len(start["input"].encode("utf-8")) > 2_000
        ):
            raise _ProtocolViolation("start input is malformed")
        del start
        result = agent.order_one_cake(_ToolProxy())
        if not isinstance(result, dict):
            raise _ProtocolViolation("agent output must be a JSON object")
        _send_frame({"type": "agent_result", "result": result})
        return 0
    except _PolicyViolation as error:
        _send_frame({"type": "agent_failure", "code": error.code})
        return 0
    except _ProtocolViolation:
        _send_frame({"type": "agent_failure", "code": "protocol_error"})
        return 0
    except MemoryError:
        _send_frame({"type": "agent_failure", "code": "memory_limit_exceeded"})
        return 0
    except Exception:
        _send_frame({"type": "agent_failure", "code": "agent_exception"})
        return 0


def main() -> int:
    _initialize_protocol_output()
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--entrypoint", required=True)
    arguments = parser.parse_args()
    return _run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

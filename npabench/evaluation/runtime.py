from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TextIO

from npabench.config import DEFAULT_BASE_GAME_PORT, DEFAULT_BASE_RCON_PORT

_LEASE_ROOT = Path("/tmp/npabench-runtime-leases")
_DYNAMIC_BASE_PORT = 30000
_PORTS_PER_FAMILY = 128
_PORTS_PER_LEASE = _PORTS_PER_FAMILY * 2
_MAX_LEASES = 138


@dataclass(frozen=True)
class EvaluationRuntime:
    namespace: str
    base_game_port: int
    base_rcon_port: int


def unique_namespace(output_dir: Path) -> str:
    material = f"{output_dir.resolve()}\0{os.getpid()}\0{secrets.token_hex(8)}".encode()
    return f"npab-{hashlib.sha256(material).hexdigest()[:12]}"


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _ports_are_available(base_game_port: int, base_rcon_port: int, slot_count: int) -> bool:
    return all(
        _port_is_available(port)
        for base in (base_game_port, base_rcon_port)
        for port in range(base, base + slot_count)
    )


def _try_lock(path: Path) -> TextIO | None:
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


@contextmanager
def reserve_evaluation_runtime(
    *,
    output_dir: Path,
    slot_count: int,
    base_game_port: int,
    base_rcon_port: int,
) -> Iterator[EvaluationRuntime]:
    if slot_count < 1:
        raise ValueError("slot_count must be at least one")
    if slot_count > _PORTS_PER_FAMILY:
        raise ValueError(
            f"at most {_PORTS_PER_FAMILY} concurrent agents are supported per evaluation"
        )

    namespace = unique_namespace(output_dir)
    uses_defaults = (
        base_game_port == DEFAULT_BASE_GAME_PORT and base_rcon_port == DEFAULT_BASE_RCON_PORT
    )
    if not uses_defaults:
        yield EvaluationRuntime(namespace, base_game_port, base_rcon_port)
        return

    _LEASE_ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    for lease_id in range(_MAX_LEASES):
        game_port = _DYNAMIC_BASE_PORT + lease_id * _PORTS_PER_LEASE
        rcon_port = game_port + _PORTS_PER_FAMILY
        if rcon_port + slot_count > 65536:
            break
        handle = _try_lock(_LEASE_ROOT / f"lease-{lease_id}.lock")
        if handle is None:
            continue
        try:
            if not _ports_are_available(game_port, rcon_port, slot_count):
                continue
            yield EvaluationRuntime(namespace, game_port, rcon_port)
            return
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
    raise RuntimeError("no free npabench Docker port lease is available")

"""Replay artifact helpers for packet recordings."""

from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

MCPR_FILE_FORMAT_VERSION = 14
REPLAY_STATES = {"login", "configuration", "play"}
PROTOCOL_TO_VERSION = {
    774: "1.21.11",
}


def _read_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    cursor = offset
    while cursor < len(data):
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, cursor
        shift += 7
        if shift >= 35:
            raise ValueError("varint is too large")
    raise ValueError("unexpected end of varint")


def _iter_packet_log_lines(packet_log: Path):
    if not packet_log.name.endswith(".gz"):
        with packet_log.open("rb") as stream:
            yield from stream
        return

    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = b""
    with packet_log.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            pending += decompressor.decompress(chunk)
            while True:
                line, sep, pending = pending.partition(b"\n")
                if not sep:
                    pending = line
                    break
                yield line + b"\n"
        pending += decompressor.flush()
    if pending.strip():
        yield pending


def _load_manifest(packet_log: Path) -> dict[str, Any]:
    manifest = packet_log.with_name("packets.manifest.json")
    if not manifest.exists():
        return {}
    with manifest.open("r", encoding="utf-8") as f:
        return json.load(f)


def _extract_players(event: dict[str, Any], players: set[str]) -> None:
    data = event.get("data")
    if not isinstance(data, dict):
        return
    entries = data.get("data")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        uuid = entry.get("uuid")
        if isinstance(uuid, str):
            players.add(uuid)


def _extract_self_id(event: dict[str, Any]) -> int | None:
    if event.get("name") != "login" or event.get("state") != "play":
        return None
    raw = event.get("raw")
    if not isinstance(raw, str):
        return None
    packet = base64.b64decode(raw)
    try:
        _, cursor = _read_varint(packet)
    except ValueError:
        return None
    if len(packet) < cursor + 4:
        return None
    return struct.unpack(">i", packet[cursor:cursor + 4])[0]


def _started_ms(manifest: dict[str, Any]) -> int:
    created_at = str(manifest.get("startedAt") or "")
    if not created_at:
        return 0
    return int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp() * 1000)


def export_mcpr(packet_log: Path, output: Path | None = None) -> Path:
    """Export a packet log to a ReplayMod .mcpr archive."""
    packet_log = packet_log.resolve()
    if not packet_log.exists():
        raise RuntimeError(f"packet log does not exist: {packet_log}")
    output = (output or packet_log.with_name("recording.mcpr")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(packet_log)
    minecraft_version = str(manifest.get("minecraftVersion") or "unknown")

    protocol_version: int | None = None
    first_replay_time: float | None = None
    last_time_ms = 0
    players: set[str] = set()
    self_id = -1
    packet_count = 0

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w+b", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            for raw_line in _iter_packet_log_lines(packet_log):
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") != "packet":
                    continue

                if event.get("dir") == "out" and event.get("name") == "set_protocol":
                    data = event.get("data")
                    if isinstance(data, dict) and isinstance(data.get("protocolVersion"), int):
                        protocol_version = data["protocolVersion"]
                    continue

                if event.get("dir") != "in" or event.get("state") not in REPLAY_STATES:
                    continue

                if event.get("state") == "play":
                    _extract_players(event, players)
                if event.get("state") == "play" and self_id == -1:
                    extracted_self_id = _extract_self_id(event)
                    if extracted_self_id is not None:
                        self_id = extracted_self_id

                raw_packet = event.get("raw")
                if not isinstance(raw_packet, str):
                    continue
                packet = base64.b64decode(raw_packet)
                event_time = float(event.get("t") or 0.0)
                if first_replay_time is None:
                    first_replay_time = event_time
                timestamp_ms = max(0, int(round((event_time - first_replay_time) * 1000)))
                last_time_ms = max(last_time_ms, timestamp_ms)
                tmp.write(struct.pack(">ii", timestamp_ms, len(packet)))
                tmp.write(packet)
                packet_count += 1

            tmp.flush()
            os.fsync(tmp.fileno())

        if packet_count == 0:
            raise RuntimeError("packet log has no replayable inbound packets")
        if protocol_version is None:
            raise RuntimeError("packet log does not include a set_protocol protocolVersion")
        if minecraft_version == "unknown":
            minecraft_version = PROTOCOL_TO_VERSION.get(protocol_version, minecraft_version)

        meta = {
            "singleplayer": False,
            "serverName": "mcbench replay",
            "customServerName": "mcbench replay",
            "duration": last_time_ms,
            "date": _started_ms(manifest),
            "mcversion": minecraft_version,
            "fileFormat": "MCPR",
            "fileFormatVersion": MCPR_FILE_FORMAT_VERSION,
            "protocol": protocol_version,
            "generator": "mcbench packet recorder",
            "selfId": self_id,
            "players": sorted(players),
        }

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(tmp_path, "recording.tmcpr")
            archive.writestr("metaData.json", json.dumps(meta, separators=(",", ":")))
            archive.writestr("markers.json", "[]")
            archive.writestr("mods.json", json.dumps({"requiredMods": []}, separators=(",", ":")))
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return output

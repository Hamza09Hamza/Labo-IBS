"""Thread-safe configuration editing for OperationBloc Bridge.

The collector supervisor reads config.json when the process starts. This
module lets the web console edit that same file safely and exposes a stable,
public representation for the UI. Connection changes intentionally report
``restart_required``: unlike the laboratory bridge listeners, these clinical
collector processes are not rebound while they are carrying a live stream.
"""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import threading

try:
    from labo_bridge.admin.photo_processing import remove_background
except ModuleNotFoundError:  # Pillow is optional at runtime despite requirements.txt.
    remove_background = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "clinical_portal", "config.json")
STATIC_DIR = os.path.join(ROOT, "clinical_portal", "static")
MACHINES_DIR = os.path.join(STATIC_DIR, "machines")
ALLOWED_PHOTO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024

SOURCE_DEFAULTS = {
    "umec12": {
        "label": "Mindray uMEC12",
        "kind": "Patient monitor",
        "default_port": 4601,
        "port_editable": False,
        "connection_mode": "outbound",
        "default_photo": "machines/umec12.png",
    },
    "wato": {
        "label": "Mindray WATO EX-35",
        "kind": "Anesthesia workstation",
        "default_port": 6010,
        "port_editable": True,
        "connection_mode": "listener",
        "default_photo": "machines/wato-ex35.jpg",
    },
}

BLOCK_COLORS = ("#0C8599", "#7C3AED", "#F59E0B")
_lock = threading.RLock()


def _read_unlocked() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config() -> dict:
    with _lock:
        return _read_unlocked()


def _write_unlocked(config: dict) -> None:
    directory = os.path.dirname(CONFIG_PATH)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix="config-", suffix=".tmp", delete=False
    )
    temporary_path = handle.name
    try:
        with handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def _block(config: dict, block_id: int) -> dict:
    for item in config.get("chambers") or []:
        if int(item.get("id", 0)) == block_id:
            return item
    raise KeyError(block_id)


def _source_public(block: dict, source: str, block_id: int) -> dict:
    raw = block.get(source) or {}
    defaults = SOURCE_DEFAULTS[source]
    if source == "umec12":
        port = defaults["default_port"]
        ip_address = str(raw.get("ip") or "")
        local_port = raw.get("local_port")
        local_port = int(local_port) if local_port not in (None, "") else None
    else:
        port = int(raw.get("listen_port", defaults["default_port"] + block_id - 1))
        ip_address = str(raw.get("destination_ip") or "")
        local_port = None
    default_machine_id = f"{source.upper()}-{block_id:02d}"
    return {
        "source": source,
        "machine_id": str(raw.get("machine_id") or default_machine_id),
        "label": str(raw.get("label") or defaults["label"]),
        "kind": str(raw.get("kind") or defaults["kind"]),
        "enabled": bool(raw.get("enabled", False)),
        "ip": ip_address,
        "port": port,
        "local_port": local_port,
        "port_editable": defaults["port_editable"],
        "connection_mode": defaults["connection_mode"],
        "photo": str(raw.get("photo") or defaults["default_photo"]),
    }


def public_block(block: dict) -> dict:
    block_id = int(block["id"])
    return {
        "id": block_id,
        "name": str(block.get("name") or f"Operation Block {block_id}"),
        "code": str(block.get("code") or f"OB-{block_id:02d}"),
        "color": str(block.get("color") or BLOCK_COLORS[(block_id - 1) % len(BLOCK_COLORS)]),
        "restart_required": True,
        "machines": {
            source: _source_public(block, source, block_id)
            for source in SOURCE_DEFAULTS
        },
    }


def public_config() -> dict:
    config = load_config()
    return {
        "application_name": "OperationBloc Bridge",
        "restart_required": True,
        "blocks": [public_block(block) for block in config.get("chambers") or []],
    }


def _validated_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("port must be a number") from None
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port


def _validated_ip(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("monitor IP must be a valid IPv4 or IPv6 address") from None
    return value


def _validate_local_ports(config: dict) -> None:
    """Ensure enabled listeners and explicitly bound source sockets do not collide."""
    web_port = int((config.get("web") or {}).get("port", 5051))
    used = {web_port: "OperationBloc web server"}
    for block in config.get("chambers") or []:
        block_name = block.get("name") or f"Operation Block {block.get('id', '?')}"
        umec = block.get("umec12") or {}
        local_port = umec.get("local_port")
        if umec.get("enabled") and local_port not in (None, ""):
            port = _validated_port(local_port)
            if port in used:
                raise ValueError(f"local port {port} is already used by {used[port]}")
            used[port] = f"{block_name} uMEC12 source socket"
        wato = block.get("wato") or {}
        if wato.get("enabled"):
            port = _validated_port(wato.get("listen_port"))
            if port in used:
                raise ValueError(f"local port {port} is already used by {used[port]}")
            used[port] = f"{block_name} WATO listener"


def update_machine(block_id: int, source: str, fields, photo_file=None) -> dict:
    if source not in SOURCE_DEFAULTS:
        raise KeyError(source)

    with _lock:
        config = _read_unlocked()
        block = _block(config, block_id)
        machine = block.setdefault(source, {})

        if "block_name" in fields:
            name = str(fields.get("block_name") or "").strip()
            if not name:
                raise ValueError("block name cannot be empty")
            block["name"] = name

        if "color" in fields:
            color = str(fields.get("color") or "").strip()
            if not (len(color) == 7 and color.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
                raise ValueError("accent color must be a six-digit hex color")
            block["color"] = color

        if "machine_id" in fields:
            machine_id = str(fields.get("machine_id") or "").strip()
            if not machine_id:
                raise ValueError("machine ID cannot be empty")
            if len(machine_id) > 40 or not all(c.isalnum() or c in "-_" for c in machine_id):
                raise ValueError("machine ID must be 40 characters or fewer, using only letters, digits, - or _")
            for other_block in config.get("chambers") or []:
                for other_source in SOURCE_DEFAULTS:
                    if int(other_block.get("id", 0)) == block_id and other_source == source:
                        continue
                    other_machine = other_block.get(other_source) or {}
                    if str(other_machine.get("machine_id") or "") == machine_id:
                        raise ValueError(
                            f"machine ID '{machine_id}' is already assigned to "
                            f"{other_block.get('name') or 'another block'} / {other_source}"
                        )
            machine["machine_id"] = machine_id

        if "label" in fields:
            label = str(fields.get("label") or "").strip()
            if not label:
                raise ValueError("machine name cannot be empty")
            machine["label"] = label

        if "kind" in fields:
            kind = str(fields.get("kind") or "").strip()
            if not kind:
                raise ValueError("machine type cannot be empty")
            machine["kind"] = kind

        if "enabled" in fields:
            machine["enabled"] = str(fields.get("enabled") or "").lower() in {"1", "true", "yes", "on"}

        if "port" in fields:
            port = _validated_port(fields.get("port"))
            if source == "umec12":
                if port != SOURCE_DEFAULTS["umec12"]["default_port"]:
                    raise ValueError("uMEC12 PDS port is fixed at 4601")
            else:
                for other in config.get("chambers") or []:
                    if int(other.get("id", 0)) == block_id:
                        continue
                    other_wato = other.get("wato") or {}
                    if other_wato.get("enabled") and int(other_wato.get("listen_port", 0)) == port:
                        raise ValueError(f"listen port {port} is already used by {other.get('name') or 'another block'}")
                machine["listen_port"] = port

        if source == "umec12" and "ip" in fields:
            machine["ip"] = _validated_ip(fields.get("ip"))

        if source == "wato" and "ip" in fields:
            machine["destination_ip"] = _validated_ip(fields.get("ip"))

        if source == "umec12" and "local_port" in fields:
            local_port_value = str(fields.get("local_port") or "").strip()
            if local_port_value:
                machine["local_port"] = _validated_port(local_port_value)
            else:
                machine.pop("local_port", None)

        if source == "umec12" and machine.get("enabled") and not str(machine.get("ip") or "").strip():
            raise ValueError("an enabled uMEC12 requires a monitor IP")

        if source == "wato" and machine.get("enabled") and not str(machine.get("destination_ip") or "").strip():
            raise ValueError("an enabled WATO requires the bridge destination IP configured on the machine")

        _validate_local_ports(config)

        if photo_file and photo_file.filename:
            extension = os.path.splitext(photo_file.filename)[1].lower()
            if extension not in ALLOWED_PHOTO_EXTENSIONS:
                raise ValueError("photo must be PNG, JPG, JPEG, or WebP")
            raw = photo_file.read(MAX_PHOTO_BYTES + 1)
            if len(raw) > MAX_PHOTO_BYTES:
                raise ValueError("photo must be 8 MB or smaller")
            processed = remove_background(raw) if remove_background is not None else raw
            os.makedirs(MACHINES_DIR, exist_ok=True)
            output_extension = ".png" if remove_background is not None else extension
            filename = f"block-{block_id}-{source}{output_extension}"
            with open(os.path.join(MACHINES_DIR, filename), "wb") as handle:
                handle.write(processed)
            machine["photo"] = f"machines/{filename}"

        _write_unlocked(config)
        return public_block(block)

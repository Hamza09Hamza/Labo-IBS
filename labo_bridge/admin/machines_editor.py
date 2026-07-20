"""
Safe, surgical editor that adds a brand-new analyzer entry into server.py's
MACHINES dict. Same approach as mappings_editor.py/machine_meta_editor.py -
anchor on the dict literal via brace-depth counting and insert one new block
before the closing brace, leaving every existing entry/comment untouched.

Only ADDS new machines - editing/removing an existing one isn't needed yet
(existing machines are hand-verified, hardware-specific configs; adding a
new one at runtime is the actual feature being built here).
"""

import re
import threading
from pathlib import Path

SERVER_PY_PATH = Path(__file__).resolve().parent.parent / "server.py"

# decoder module -> which function name that module exposes, keyed by
# protocol style (ASTM/LIS2-A modules expose decode_record, HL7 exposes
# decode_segment). Matches server.py's MACHINES cfg shape exactly.
DECODER_MODULES = {
    "xn330": ("decoders.xn330", "decode_record"),
    "ismart": ("decoders.ismart", "decode_record"),
    "selectra": ("decoders.selectra", "decode_record"),
    "cyanvision": ("decoders.cyanvision", "decode_segment"),
    "xs500i": ("decoders.xn330", "decode_record"),  # xs500i reuses xn330's decoder
}

_lock = threading.Lock()


def _find_machines_block(text: str):
    m = re.search(r"^MACHINES\s*=\s*\{", text, re.MULTILINE)
    if not m:
        raise ValueError("MACHINES dict not found in server.py")
    brace_start = m.end() - 1
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return brace_start, i + 1
        i += 1
    raise ValueError("unterminated MACHINES dict literal")


def add_machine(machine: str, protocol: str, reuse_decoder_from: str,
                 port: int, initial_ack: bool = False) -> None:
    """
    Insert a new "machine": {...} entry into MACHINES, reusing an existing
    machine's decoder function (protocol/reuse_decoder_from both validated
    by the caller - the admin UI only ever offers picking from the existing
    machines' own protocol+decoder, never freehand code).
    """
    if reuse_decoder_from not in DECODER_MODULES:
        raise ValueError(f"unknown decoder source machine {reuse_decoder_from!r}")
    module_path, func_name = DECODER_MODULES[reuse_decoder_from]
    decoder_module = module_path.split(".")[-1]  # e.g. "xn330"
    cfg_key = "decode_record" if protocol == "astm" else "decode_segment"

    with _lock:
        text = SERVER_PY_PATH.read_text(encoding="utf-8")

        if re.search(rf'"{re.escape(machine)}"\s*:\s*\{{', text):
            raise ValueError(f"machine {machine!r} already exists in MACHINES")

        start, end = _find_machines_block(text)
        block = text[start:end]

        entry = (
            f'    "{machine}":'.ljust(18) +
            f'{{"protocol": "{protocol}", "{cfg_key}": {decoder_module}.{func_name},\n'
            f'                   "initial_ack": {initial_ack!r}, "port": {port}}},\n'
        )
        insert_at = block.rstrip().rfind("}")
        new_block = block[:insert_at] + entry + block[insert_at:]
        new_text = text[:start] + new_block + text[end:]
        SERVER_PY_PATH.write_text(new_text, encoding="utf-8")

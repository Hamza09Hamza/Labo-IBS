"""
Safe, surgical editor for MACHINE_META's per-machine "label" field, defined
in app.py. Same approach as mappings_editor.py/config_editor.py - anchor on
the machine's dict block and rewrite only the "label": "..." line, leaving
color/photo/protocol/port and everything else in the file untouched.
"""

import re
import threading
from pathlib import Path

APP_PY_PATH = Path(__file__).resolve().parent / "app.py"

_lock = threading.Lock()


def set_label(machine: str, new_label: str) -> None:
    with _lock:
        text = APP_PY_PATH.read_text(encoding="utf-8")

        # Find this machine's dict entry: "machine":      {...}, scoped to
        # its own brace pair so we never touch a different machine's block.
        entry_re = re.compile(rf'"{re.escape(machine)}"\s*:\s*\{{')
        m = entry_re.search(text)
        if not m:
            raise ValueError(f"machine {machine!r} not found in MACHINE_META")

        depth = 0
        start = m.end() - 1
        i = start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = i + 1
        block = text[start:end]

        quoted = new_label.replace("\\", "\\\\").replace('"', '\\"')
        new_block, count = re.subn(r'"label"\s*:\s*"[^"]*"', f'"label": "{quoted}"',
                                   block, count=1)
        if count == 0:
            raise ValueError(f"'label' field not found for machine {machine!r}")

        new_text = text[:start] + new_block + text[end:]
        APP_PY_PATH.write_text(new_text, encoding="utf-8")


def _quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_machine(machine: str, label: str, kind: str, protocol: str, port: int,
                 color: str, photo: str = None, photo_bg: str = "transparent") -> None:
    """
    Insert a brand-new machine's metadata block into MACHINE_META, right
    before the dict's closing brace - same surgical approach as set_label,
    but adding a whole new entry instead of rewriting one field of an
    existing one. Used by the Add Analyzer flow.
    """
    with _lock:
        text = APP_PY_PATH.read_text(encoding="utf-8")

        if re.search(rf'"{re.escape(machine)}"\s*:\s*\{{', text):
            raise ValueError(f"machine {machine!r} already exists in MACHINE_META")

        m = re.search(r"^MACHINE_META\s*=\s*\{", text, re.MULTILINE)
        if not m:
            raise ValueError("MACHINE_META dict not found in app.py")
        brace_start = m.end() - 1
        depth = 0
        i = brace_start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = i + 1
        block = text[brace_start:end]

        photo_repr = _quote(photo) if photo else "None"
        entry = (
            f'    "{machine}":'.ljust(18) +
            f'{{"label": {_quote(label)},   "kind": {_quote(kind)},\n'
            f'                   "protocol": {_quote(protocol)}, "port": {port}, '
            f'"color": {_quote(color)},\n'
            f'                   "photo": {photo_repr}, "photo_bg": {_quote(photo_bg)}}},\n'
        )
        insert_at = block.rstrip().rfind("}")
        new_block = block[:insert_at] + entry + block[insert_at:]
        new_text = text[:brace_start] + new_block + text[end:]
        APP_PY_PATH.write_text(new_text, encoding="utf-8")

"""
Safe, surgical editor for labo_bridge/mappings.py.

mappings.py is hand-written and heavily documented (rationale per machine,
per-entry footnote comments like "# 18 vs 8 orders vs..."). A naive
"regenerate the whole file from a dict" approach would destroy all of that
context. Instead, this module finds and rewrites ONE tuple-literal line at a
time via a precise regex anchored on the dict key, leaving every comment,
docstring, and unrelated line byte-for-byte untouched.

Only ever called from the local admin UI (labo_bridge/admin/app.py), which
only listens on localhost - this is a trusted, single-operator tool editing
a file that only WE read (the clinic Postgres DB is never written here).
"""

import re
import threading
from pathlib import Path

from .. import pg

MAPPINGS_PATH = Path(__file__).resolve().parent.parent / "mappings.py"

# machine key (as used in labo_bridge.matcher/MAPS) -> the dict variable name
# in mappings.py. Kept explicit (not derived) so a naming drift in one file
# doesn't silently break the other.
MAP_VAR_NAMES = {
    "xn330": "XN330_MAP",
    "ismart": "ISMART_MAP",
    "selectra": "SELECTRA_MAP",
    "cyanvision": "CYANVISION_MAP",
    "xs500i": "XS500I_MAP",
    "minividas": "MINIVIDAS_MAP",
}

_lock = threading.Lock()  # editing the file is a rare, single-operator action


def _quote(s: str) -> str:
    """Render a Python string literal the same style mappings.py already uses."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_value(param_id, st_id, st_name, abbrev, name) -> str:
    param_repr = "None" if param_id is None else str(int(param_id))
    st_id_repr = "None" if st_id is None else str(int(st_id))
    return f"({param_repr}, {st_id_repr}, {_quote(st_name)}, {_quote(abbrev)}, {_quote(name)})"


def _find_map_block(text: str, var_name: str):
    """
    Return (start, end) char offsets of the `VAR_NAME = { ... }` block,
    matched by brace-depth counting (not regex) so nested braces/strings
    containing "}" don't truncate it early.
    """
    m = re.search(rf"^{re.escape(var_name)}\s*=\s*(dict\([A-Z0-9_]+\)|\{{)", text, re.MULTILINE)
    if not m:
        raise ValueError(f"{var_name} not found in {MAPPINGS_PATH}")
    if m.group(1).startswith("dict("):
        # e.g. XS500I_MAP = dict(XN330_MAP) - not a literal dict, can't
        # surgically edit one entry; caller must handle this case.
        return None
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
    raise ValueError(f"unterminated dict literal for {var_name}")


def _find_entry_span(block_text: str, code: str):
    """
    Locate one `"CODE": (...)` entry inside a map's dict-literal body and
    return (key_start, tuple_start, tuple_end) - or None if not found.

    The tuple's closing paren is found by depth-counting from tuple_start,
    NOT by a `\\([^)]*\\)` regex - values like "Basophiles (absolute)"
    contain their own ")" characters, and a naive regex stops at that FIRST
    ")" instead of the tuple's real end, silently truncating the match and
    corrupting the file on write. Depth-counting (skipping ")" inside string
    literals) handles this correctly regardless of what the string values
    contain.
    """
    key_re = re.compile(r'(["\'])' + re.escape(code) + r'\1\s*:\s*\(')
    m = key_re.search(block_text)
    if not m:
        return None
    key_start = m.start()
    tuple_start = m.end() - 1  # position of the opening "("

    depth = 0
    i = tuple_start
    in_string = None  # None, or the quote char currently inside
    while i < len(block_text):
        ch = block_text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"'):
            in_string = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return key_start, tuple_start, i + 1
        i += 1
    return None


def list_machines():
    """Machines whose map is a real dict literal we can edit in place."""
    text = MAPPINGS_PATH.read_text(encoding="utf-8")
    editable = {}
    for machine, var_name in MAP_VAR_NAMES.items():
        block = _find_map_block(text, var_name)
        editable[machine] = block is not None
    return editable


def add_machine_map(machine: str) -> None:
    """
    Register a brand-new machine with an empty curated map: adds
    `<MACHINE>_MAP = {}` right before the `MAPS = {...}` dict, and adds
    `"machine": <MACHINE>_MAP` inside MAPS itself. Used by the Add Analyzer
    flow - a new machine always starts with zero curated mappings (every
    code it sends lands in pending_params until a human maps it, one code
    at a time, same as every other machine did).
    """
    with _lock:
        if machine in MAP_VAR_NAMES:
            raise ValueError(f"machine {machine!r} already has a map")

        var_name = f"{machine.upper()}_MAP"
        text = MAPPINGS_PATH.read_text(encoding="utf-8")

        maps_m = re.search(r"^MAPS\s*=\s*\{", text, re.MULTILINE)
        if not maps_m:
            raise ValueError("MAPS dict not found in mappings.py")

        new_map_decl = f'{var_name} = {{}}\n\n'
        text = text[:maps_m.start()] + new_map_decl + text[maps_m.start():]

        maps_m2 = re.search(r"^MAPS\s*=\s*\{", text, re.MULTILINE)
        brace_start = maps_m2.end() - 1
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
        insert_at = text.rfind("\n", brace_start, i) + 1
        new_line = f'    "{machine}": {var_name},\n'
        text = text[:insert_at] + new_line + text[insert_at:]

        MAPPINGS_PATH.write_text(text, encoding="utf-8")
        MAP_VAR_NAMES[machine] = var_name


def upsert_entry(machine: str, code: str, param_id, service_tarification_id,
                  service_tarification_name: str, abbrev: str, name: str) -> None:
    """
    Add or update one `"CODE": (...)` line inside the given machine's dict
    literal. Preserves any trailing inline comment on that line (e.g. "# 18
    vs 8 orders...") - only the tuple value is replaced, comment kept as-is.
    Raises ValueError with a clear message if the machine's map isn't a
    plain dict literal (e.g. xs500i is `dict(XN330_MAP)` - see aliased_from()).
    """
    with _lock:
        var_name = MAP_VAR_NAMES.get(machine)
        if not var_name:
            raise ValueError(f"unknown machine {machine!r}")

        text = MAPPINGS_PATH.read_text(encoding="utf-8")
        block = _find_map_block(text, var_name)
        if block is None:
            raise ValueError(
                f"{machine}'s map ({var_name}) is defined as an alias of another "
                f"machine's map (e.g. `dict(XN330_MAP)`), not its own dict literal - "
                f"edit the source machine's map instead, or convert it to a literal first."
            )
        start, end = block
        block_text = text[start:end]

        new_value = _format_value(param_id, service_tarification_id,
                                  service_tarification_name, abbrev, name)
        key_quoted = _quote(code)
        span = _find_entry_span(block_text, code)
        if span:
            key_start, tuple_start, tuple_end = span
            # preserve whatever trailing comma/inline comment already
            # followed the old tuple, exactly as it was
            trailer_m = re.match(r'(\s*,?)([ \t]*#[^\n]*)?', block_text[tuple_end:])
            trailing_comma = trailer_m.group(1) or ","
            comment = trailer_m.group(2) or ""
            replacement = f"{key_quoted}: {new_value}{trailing_comma}{comment}"
            new_block_text = (block_text[:key_start] + replacement
                              + block_text[tuple_end + trailer_m.end():])
        else:
            # New entry - insert just before the closing brace, matching the
            # file's existing 4-space indent convention.
            insert_at = block_text.rstrip().rfind("}")
            # find indent of the last existing entry line to match style,
            # falling back to 4 spaces if the dict is empty.
            indent_m = re.search(r"\n([ \t]+)\S", block_text[:insert_at])
            indent = indent_m.group(1) if indent_m else "    "
            new_line = f"{indent}{key_quoted}: {new_value},\n"
            new_block_text = block_text[:insert_at] + new_line + block_text[insert_at:]

        new_text = text[:start] + new_block_text + text[end:]
        MAPPINGS_PATH.write_text(new_text, encoding="utf-8")

    # Mirror into labo_bridge.mappings AFTER the file write succeeds and the
    # lock is released - mappings.py is still the source of truth, this is
    # just a queryable copy (see pg.py's module docstring).
    pg.sync_mapping(machine, code, param_id, service_tarification_id,
                    service_tarification_name, abbrev, name)
    # This code is no longer "waiting to be mapped" - clear any stale
    # pending_params row for it so the Pending tab doesn't keep flagging
    # something that's now resolved.
    pg.clear_pending_param(machine, code)


def delete_entry(machine: str, code: str) -> bool:
    """Remove one code's entry entirely. Returns False if it wasn't found."""
    with _lock:
        var_name = MAP_VAR_NAMES.get(machine)
        if not var_name:
            raise ValueError(f"unknown machine {machine!r}")
        text = MAPPINGS_PATH.read_text(encoding="utf-8")
        block = _find_map_block(text, var_name)
        if block is None:
            raise ValueError(f"{machine}'s map is an alias, not directly editable")
        start, end = block
        block_text = text[start:end]

        span = _find_entry_span(block_text, code)
        if not span:
            return False
        _, _, tuple_end = span
        # extend the removal to cover the whole line: back up to the
        # preceding newline, and forward past any trailing comma/comment
        line_start = block_text.rfind("\n", 0, span[0]) + 1
        trailer_m = re.match(r'\s*,?[ \t]*(#[^\n]*)?', block_text[tuple_end:])
        remove_end = tuple_end + trailer_m.end()
        new_block_text = block_text[:line_start] + block_text[remove_end:]
        new_text = text[:start] + new_block_text + text[end:]
        MAPPINGS_PATH.write_text(new_text, encoding="utf-8")

    pg.delete_mapping_sync(machine, code)
    return True

"""
Local admin web UI for labo_bridge - view/edit machine <-> labo_param
mappings, browse captured samples, and see live match/pending stats.

Run with: python -m labo_bridge.admin
Binds to 127.0.0.1 only - this is a single-operator local tool, not exposed
to the network. It edits labo_bridge/mappings.py directly (see
mappings_editor.py) and reads/writes the clinic Postgres DB
(labo_bridge.samples / labo_bridge_results / pending_params), which is the
ONLY persistence layer for this project (local SQLite was retired).
"""

import importlib
import logging
import os
import re
import sys

from flask import Flask, jsonify, request, send_from_directory

# Werkzeug (Flask's dev server) logs every request by default - with the
# admin UI's live 2-3s polling (machines/mappings/pending/samples/status),
# that's a wall of "GET ... 200 -" lines drowning out anything useful in
# the terminal. Only warnings/errors (failed requests, tracebacks) print now;
# routine 200s from polling are silenced.
logging.getLogger("werkzeug").setLevel(logging.WARNING)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)))

from labo_bridge import mappings as mappings_module, server as server_module
from labo_bridge import runtime_ports, live_status
from labo_bridge.admin import mappings_editor as me
from labo_bridge.admin import config_editor as ce
from labo_bridge.admin import machines_editor as mce

try:
    import psycopg2
    from labo_bridge import pg as pg_module
except Exception:
    psycopg2 = None
    pg_module = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=None)


def _reload_mappings():
    """Re-import mappings.py after an edit so subsequent reads see the change."""
    importlib.reload(mappings_module)


def _pg():
    """
    Return a live Postgres connection, or None if unreachable. Callers must
    handle None gracefully (empty result, 503, etc.) - Postgres being down
    should degrade individual endpoints, never crash the whole admin UI.
    """
    if pg_module is None:
        return None
    return pg_module._get_conn()


def _pg_query(sql, params=()):
    """Run a SELECT and return (columns, rows), or (None, None) if PG is down."""
    conn = _pg()
    if conn is None:
        return None, None
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        rows = cur.fetchall()
    return cols, rows


def _pg_rows_as_dicts(sql, params=()):
    cols, rows = _pg_query(sql, params)
    if cols is None:
        return []
    return [dict(zip(cols, row)) for row in rows]


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

def _no_cache(resp):
    """
    Force the browser to always re-fetch static files (never serve a stale
    cached app.js/style.css/index.html). Without this, the dev server answers
    conditional requests with 304 Not Modified and the browser keeps running
    an OLD cached copy of the JS even after the file on disk changed - which
    caused a whole class of "the fix isn't taking effect" confusion, including
    a stale app.js re-registering the poll loop many times over and flooding
    the server with requests.
    """
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/")
def index():
    return _no_cache(send_from_directory(STATIC_DIR, "index.html"))


@app.route("/<path:filename>")
def static_files(filename):
    return _no_cache(send_from_directory(STATIC_DIR, filename))


# ---------------------------------------------------------------------------
# Machines overview
# ---------------------------------------------------------------------------

# Per-machine display/API settings (label, kind, protocol, port, color,
# photo, machine_id) now live in labo_bridge.machine_config (see pg.py's
# module docstring for why: rewriting this file's source on every settings
# change was fragile - a save could hang waiting on the OS/antivirus to
# release the file handle, or a botched edit could leave invalid Python and
# crash the whole admin server on next reload; both happened in practice).
# This function replaces the old hardcoded MACHINE_META dict - call it
# fresh wherever the old dict used to be read directly, since Postgres is
# now the actual source of truth, not module-level state.
def _machine_meta():
    return pg_module.get_all_machine_configs() if pg_module else {}


# machine key -> which existing machine's decoder it reuses + what protocol
# that decoder speaks. Mirrors machines_editor.DECODER_MODULES - the admin UI
# only ever offers picking one of these, never freehand decoder code, since
# writing a new decoder is a real engineering task (see decoders/ - each one
# encodes hard-won knowledge about that exact analyzer's wire format), not
# something to generate blind from a web form.
DECODER_CHOICES = {
    "xn330": "astm", "ismart": "astm", "selectra": "astm", "cyanvision": "hl7",
    "xs500i": "astm",
}

MACHINES_DIR = os.path.join(STATIC_DIR, "machines")
ALLOWED_PHOTO_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


@app.route("/api/decoders")
def api_decoders():
    """List existing machines whose decoder can be reused for a new analyzer."""
    meta = _machine_meta()
    return jsonify([
        {"machine": m, "label": meta.get(m, {}).get("label", m), "protocol": p}
        for m, p in DECODER_CHOICES.items()
    ])


@app.route("/api/machines", methods=["POST"])
def api_add_machine():
    """
    Add a brand-new analyzer: writes labo_bridge.machine_config (Postgres),
    MACHINES (server.py), and an empty curated map (mappings.py), saves an
    optional photo upload, then starts its listener thread live via
    server_module.register_machine() - no process restart needed, same
    principle as the live port-rebind mechanism.
    """
    machine = (request.form.get("machine") or "").strip().lower()
    label = (request.form.get("label") or "").strip()
    kind = (request.form.get("kind") or "").strip()
    reuse_decoder_from = (request.form.get("reuse_decoder_from") or "").strip()
    port_raw = (request.form.get("port") or "").strip()
    color = (request.form.get("color") or "#0C8599").strip()
    machine_id_raw = (request.form.get("machine_id") or "").strip()

    if not re.match(r"^[a-z][a-z0-9_]*$", machine):
        return jsonify({"error": "machine key must be lowercase letters/numbers/underscore, "
                                  "starting with a letter (e.g. 'cobas_c111')"}), 400
    if machine in server_module.MACHINES:
        return jsonify({"error": f"machine {machine!r} already exists"}), 400
    if not label:
        return jsonify({"error": "display name is required"}), 400
    if reuse_decoder_from not in DECODER_CHOICES:
        return jsonify({"error": f"pick a decoder to reuse from: {sorted(DECODER_CHOICES)}"}), 400

    try:
        port = int(port_raw)
    except ValueError:
        return jsonify({"error": "port must be a number"}), 400
    if not (1024 <= port <= 65535):
        return jsonify({"error": "port must be between 1024 and 65535"}), 400
    machine_id = None
    if machine_id_raw:
        try:
            machine_id = int(machine_id_raw)
        except ValueError:
            return jsonify({"error": "machine_id must be a number"}), 400
    in_use = [m for m, cfg in server_module.MACHINES.items()
             if runtime_ports.get_port_for(m, cfg["port"]) == port]
    if in_use:
        return jsonify({"error": f"port {port} is already used by {in_use[0]}"}), 400

    protocol = DECODER_CHOICES[reuse_decoder_from]

    photo_rel = None
    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        ext = os.path.splitext(photo_file.filename)[1].lower()
        if ext not in ALLOWED_PHOTO_EXTS:
            return jsonify({"error": f"photo must be one of: {sorted(ALLOWED_PHOTO_EXTS)}"}), 400
        os.makedirs(MACHINES_DIR, exist_ok=True)
        photo_file.save(os.path.join(MACHINES_DIR, f"{machine}{ext}"))
        photo_rel = f"machines/{machine}{ext}"

    try:
        mce.add_machine(machine, protocol, reuse_decoder_from, port)
        me.add_machine_map(machine)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cfg_key = "decode_record" if protocol == "astm" else "decode_segment"
    # pull the actual function object from the already-imported decoder
    # module - server_module imports xn330/ismart/selectra/cyanvision itself,
    # so no new import is needed here.
    decoder_module = {
        "xn330": server_module.xn330, "ismart": server_module.ismart,
        "selectra": server_module.selectra, "cyanvision": server_module.cyanvision,
        "xs500i": server_module.xn330,
    }[reuse_decoder_from]
    new_cfg = {"protocol": protocol, cfg_key: getattr(decoder_module, cfg_key),
              "initial_ack": False, "port": port}
    server_module.register_machine(machine, new_cfg)

    pg_module.upsert_machine_config(machine, label=label, kind=kind or "Analyzer",
                                    protocol=protocol.upper(), port=port,
                                    color=color, photo=photo_rel, photo_bg="transparent",
                                    machine_id=machine_id if machine_id is not None else "__unset__")
    _reload_mappings()

    return jsonify({"ok": True, "machine": machine})


@app.route("/api/machines")
def api_machines():
    editable = me.list_machines()
    matched_counts = dict(_pg_query(
        "SELECT machine, COUNT(*) FROM labo_bridge.labo_bridge_results GROUP BY machine"
    )[1] or [])
    pending_counts = dict(_pg_query(
        "SELECT machine, COUNT(*) FROM labo_bridge.pending_params GROUP BY machine"
    )[1] or [])
    sample_counts = dict(_pg_query(
        "SELECT machine, COUNT(*) FROM labo_bridge.samples GROUP BY machine"
    )[1] or [])
    last_seen_map = dict(_pg_query(
        "SELECT machine, MAX(received_at) FROM labo_bridge.samples GROUP BY machine"
    )[1] or [])

    all_meta = _machine_meta()
    out = []
    for machine, cfg in server_module.MACHINES.items():
        meta = all_meta.get(machine, {})
        machine_map = mappings_module.MAPS.get(machine, {})
        live = live_status.get(machine)
        last_seen = last_seen_map.get(machine)

        out.append({
            "machine": machine,
            "label": meta.get("label", machine),
            "kind": meta.get("kind", ""),
            "protocol": meta.get("protocol", cfg.get("protocol", "").upper()),
            "port": runtime_ports.get_port_for(machine, cfg.get("port")),
            "color": meta.get("color", "#0C8599"),
            "photo": meta.get("photo"),
            "photo_bg": meta.get("photo_bg", "transparent"),
            "machine_id": meta.get("machine_id"),
            "mapped_codes": len(machine_map),
            "sample_count": sample_counts.get(machine, 0),
            "matched_count": matched_counts.get(machine, 0),
            "pending_count": pending_counts.get(machine, 0),
            "last_seen": last_seen.isoformat() if last_seen else None,
            "editable": editable.get(machine, False),
            # Real-time state from the listener thread itself (only accurate
            # when the listener runs in this same process - i.e. via
            # run_all.py, not a standalone `python -m labo_bridge.admin`).
            "live_state": live["state"],       # "listening" | "connected" | "unknown"
            "live_since": live["since"],
            "live_source_ip": live["source_ip"],
        })
    return jsonify(out)


# ---------------------------------------------------------------------------
# Mapping table for one machine
# ---------------------------------------------------------------------------

@app.route("/api/machines/<machine>/mappings")
def api_machine_mappings(machine):
    machine_map = mappings_module.MAPS.get(machine)
    if machine_map is None:
        return jsonify({"error": f"unknown machine {machine!r}"}), 404

    recent_by_code = {}
    if machine_map:
        rows = _pg_rows_as_dicts(
            """
            SELECT DISTINCT ON (test_code) test_code, result_value, unit, received_at
            FROM labo_bridge.labo_bridge_results
            WHERE machine = %s
            ORDER BY test_code, received_at DESC
            """,
            (machine,),
        )
        recent_by_code = {r["test_code"]: r for r in rows}

    entries = []
    for code, (param_id, st_id, st_name, abbrev, name) in machine_map.items():
        recent = recent_by_code.get(code)
        entries.append({
            "code": code,
            "param_id": param_id,
            "service_tarification_id": st_id,
            "service_tarification_name": st_name,
            "abbrev": abbrev,
            "name": name,
            "last_value": recent["result_value"] if recent else None,
            "last_unit": recent["unit"] if recent else None,
            "last_seen": recent["received_at"].isoformat() if recent else None,
        })
    entries.sort(key=lambda r: r["code"])
    return jsonify({
        "machine": machine,
        "editable": me.list_machines().get(machine, False),
        "entries": entries,
    })


@app.route("/api/machines/<machine>/mappings/<code>", methods=["PUT"])
def api_upsert_mapping(machine, code):
    body = request.get_json(force=True)
    try:
        me.upsert_entry(
            machine, code,
            param_id=body.get("param_id") or None,
            service_tarification_id=body.get("service_tarification_id") or None,
            service_tarification_name=body.get("service_tarification_name") or "",
            abbrev=body.get("abbrev") or "",
            name=body.get("name") or "",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _reload_mappings()
    return jsonify({"ok": True})


@app.route("/api/machines/<machine>/mappings/<code>", methods=["DELETE"])
def api_delete_mapping(machine, code):
    try:
        found = me.delete_entry(machine, code)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not found:
        return jsonify({"error": f"{code!r} not found in {machine}'s map"}), 404
    _reload_mappings()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Pending (unmapped) codes actually seen from the analyzer - the whole point
# of "find what needs mapping next" instead of guessing.
# ---------------------------------------------------------------------------

@app.route("/api/machines/<machine>/pending")
def api_machine_pending(machine):
    # pending_params is already one row per (machine, test_code) - a mapping
    # backlog, not a result log - so no aggregation is needed here anymore.
    rows = _pg_rows_as_dicts(
        """
        SELECT test_code, seen_count,
               example_value AS sample_value, example_unit AS sample_unit,
               last_seen_at AS last_seen
        FROM labo_bridge.pending_params
        WHERE machine = %s
        ORDER BY seen_count DESC
        """,
        (machine,),
    )
    for r in rows:
        if r.get("last_seen") is not None:
            r["last_seen"] = r["last_seen"].isoformat()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Recent samples for one machine (drill-down)
# ---------------------------------------------------------------------------

@app.route("/api/machines/<machine>/samples")
def api_machine_samples(machine):
    limit = int(request.args.get("limit", 25))
    rows = _pg_rows_as_dicts(
        "SELECT * FROM labo_bridge.samples WHERE machine = %s "
        "ORDER BY received_at DESC LIMIT %s",
        (machine, limit),
    )
    for r in rows:
        if r.get("received_at") is not None:
            r["received_at"] = r["received_at"].isoformat()
    return jsonify(rows)


@app.route("/api/samples/<machine>/<sample_id>")
def api_sample_detail(machine, sample_id):
    # Only matched results are sample-scoped. pending_params tracks unmapped
    # CODES, not results tied to any one sample - there's nothing per-sample
    # to show for pending (see pg.py's module docstring).
    sample_rows = _pg_rows_as_dicts(
        "SELECT * FROM labo_bridge.samples WHERE machine = %s AND sample_id = %s LIMIT 1",
        (machine, sample_id),
    )
    sample = sample_rows[0] if sample_rows else None
    if sample and sample.get("received_at") is not None:
        sample["received_at"] = sample["received_at"].isoformat()

    matched = _pg_rows_as_dicts(
        "SELECT * FROM labo_bridge.labo_bridge_results "
        "WHERE machine = %s AND sample_id = %s ORDER BY id",
        (machine, sample_id),
    )
    for r in matched:
        if r.get("received_at") is not None:
            r["received_at"] = r["received_at"].isoformat()
    return jsonify({"sample": sample, "matched": matched})


# ---------------------------------------------------------------------------
# labo_param / exam search - used by the mapping edit modal's "match to a
# clinic parameter" field. Clinic-DB-only by design: a mapping is [our
# unmatched code] -> [their clinic param/exam] - the machine-code side is
# picked from OUR pending list (see the fCode datalist in app.js), so this
# search only needs to answer "what does the clinic call this", not also
# re-surface codes we've already mapped (that would blur the two sides of
# the match together).
# ---------------------------------------------------------------------------

@app.route("/api/param-search")
def api_param_search():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])

    if pg_module is None:
        return jsonify({"error": "clinic Postgres DB unreachable"}), 503
    conn = pg_module._get_conn()
    if conn is None:
        return jsonify({"error": "clinic Postgres DB unreachable"}), 503
    # A purely numeric query also matches lp.id exactly (e.g. typing "99138"
    # jumps straight to that param) - in addition to, not instead of, the
    # usual name/abbreviation search, since a query could coincidentally be
    # numeric-looking text too.
    id_match = int(q) if q.isdigit() else None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT lp.id, lp.abbreviation, lp.name, lp.um,
                   ltp.service_tarification_id, st.name AS service_tarification_name
            FROM labo.labo_param lp
            LEFT JOIN labo.labo_test_param ltp ON ltp.param_id = lp.id
            LEFT JOIN clinic_management.service_tarification st ON st.id = ltp.service_tarification_id
            WHERE lp.name ILIKE %s OR lp.abbreviation ILIKE %s OR lp.id = %s
            ORDER BY (lp.id = %s) DESC, lp.name LIMIT 25
            """,
            (f"%{q}%", f"%{q}%", id_match, id_match),
        )
        cols = [c.name for c in cur.description]
        db_hits = [dict(zip(cols, row)) for row in cur.fetchall()]
    return jsonify(db_hits)


@app.route("/api/exam-search")
def api_exam_search():
    q = request.args.get("q", "").strip()
    if len(q) < 1 or pg_module is None:
        return jsonify([])
    conn = pg_module._get_conn()
    if conn is None:
        return jsonify({"error": "clinic Postgres DB unreachable"}), 503
    id_match = int(q) if q.isdigit() else None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, is_composed
            FROM clinic_management.service_tarification
            WHERE (name ILIKE %s OR id = %s) AND deleted_at IS NULL
            ORDER BY (id = %s) DESC, name LIMIT 25
            """,
            (f"%{q}%", id_match, id_match),
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return jsonify(rows)


# ---------------------------------------------------------------------------
# System status - DB connectivity, API flag, etc.
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    from labo_bridge import config as config_module
    pg_ok = False
    pg_error = None
    if pg_module is not None:
        conn = pg_module._get_conn()
        pg_ok = conn is not None
        if not pg_ok:
            pg_error = "unreachable"
    return jsonify({
        "postgres_ok": pg_ok,
        "postgres_error": pg_error,
        "use_machine_result_api": getattr(config_module, "USE_MACHINE_RESULT_API", False),
        "api_endpoint": getattr(__import__("labo_bridge.api_client", fromlist=["ENDPOINT"]),
                               "ENDPOINT", None),
    })


# ---------------------------------------------------------------------------
# API settings - real edits to api_client.py / config.py
# ---------------------------------------------------------------------------

@app.route("/api/settings/api")
def api_get_api_settings():
    return jsonify(ce.get_current())


@app.route("/api/settings/api", methods=["PUT"])
def api_put_api_settings():
    body = request.get_json(force=True)
    try:
        result = ce.update(
            endpoint=body.get("endpoint"),
            api_token=body.get("api_token"),
            use_machine_result_api=body.get("use_machine_result_api"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


# ---------------------------------------------------------------------------
# Machine config - display name, machine_id (both in labo_bridge.
# machine_config, a plain Postgres UPDATE - see pg.py's module docstring for
# why this isn't a file edit anymore) and listen port (live rebind via
# runtime_ports.json - no restart needed, see server.py's _serve_one_machine
# which polls this on every accept-loop tick)
# ---------------------------------------------------------------------------

@app.route("/api/machines/<machine>/config", methods=["PUT"])
def api_put_machine_config(machine):
    if machine not in server_module.MACHINES:
        return jsonify({"error": f"unknown machine {machine!r}"}), 404
    if pg_module is None:
        return jsonify({"error": "clinic Postgres DB unreachable"}), 503

    # multipart/form-data when a photo file is attached, JSON otherwise - the
    # frontend always sends multipart now so one code path handles both.
    if request.content_type and request.content_type.startswith("multipart/"):
        data = request.form
    else:
        data = request.get_json(force=True) or {}

    def _get(key):
        # Distinguish "key absent" (don't touch) from "key present but empty"
        # (clear it) - request.form/get_json both return None for missing.
        return data.get(key) if key in data else "__absent__"

    label = _get("label")
    port = _get("port")
    kind = _get("kind")
    color = _get("color")
    machine_id = _get("machine_id")

    if label not in (None, "__absent__"):
        label = label.strip()
        if not label:
            return jsonify({"error": "label cannot be empty"}), 400
    elif label == "__absent__":
        label = None

    if port not in (None, "", "__absent__"):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return jsonify({"error": "port must be a number"}), 400
        if not (1024 <= port <= 65535):
            return jsonify({"error": "port must be between 1024 and 65535"}), 400
        in_use = [m for m, cfg in server_module.MACHINES.items()
                 if m != machine and runtime_ports.get_port_for(m, cfg["port"]) == port]
        if in_use:
            return jsonify({"error": f"port {port} is already used by {in_use[0]}"}), 400
        runtime_ports.set_override(machine, port)
    else:
        port = None

    if kind == "__absent__":
        kind = None
    if color == "__absent__":
        color = None

    if machine_id != "__absent__":
        if machine_id is not None and machine_id != "":
            try:
                machine_id = int(machine_id)
            except (TypeError, ValueError):
                return jsonify({"error": "machine_id must be a number"}), 400
        else:
            machine_id = None
    else:
        machine_id = "__unset__"

    photo_rel = None
    photo_file = request.files.get("photo") if hasattr(request, "files") else None
    if photo_file and photo_file.filename:
        ext = os.path.splitext(photo_file.filename)[1].lower()
        if ext not in ALLOWED_PHOTO_EXTS:
            return jsonify({"error": f"photo must be one of: {sorted(ALLOWED_PHOTO_EXTS)}"}), 400
        os.makedirs(MACHINES_DIR, exist_ok=True)
        photo_file.save(os.path.join(MACHINES_DIR, f"{machine}{ext}"))
        photo_rel = f"machines/{machine}{ext}"

    if any(v is not None for v in (label, kind, color, photo_rel)) or machine_id != "__unset__":
        ok = pg_module.upsert_machine_config(machine, label=label, kind=kind, color=color,
                                             photo=photo_rel, machine_id=machine_id)
        if not ok:
            return jsonify({"error": "failed to save - clinic Postgres DB unreachable"}), 503

    meta = pg_module.get_machine_config(machine) or {}
    return jsonify({"ok": True, "label": meta.get("label"), "kind": meta.get("kind"),
                    "color": meta.get("color"), "photo": meta.get("photo"),
                    "port": runtime_ports.get_port_for(machine, server_module.MACHINES[machine]["port"]),
                    "machine_id": meta.get("machine_id")})


def main():
    print("Labo Bridge Admin running at http://127.0.0.1:5050")
    # threaded=True: without it, Flask's dev server handles ONE request at a
    # time. With a machine actively streaming (Postgres writes on every
    # result) plus the frontend's 2s polling loop, single-threaded mode lets
    # requests queue up behind each other indefinitely under real load - a
    # config save could sit "pending" for a long time not because anything
    # is broken, but because it's stuck waiting for its turn.
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)


if __name__ == "__main__":
    main()

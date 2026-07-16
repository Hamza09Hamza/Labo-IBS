import socket
import os
from datetime import datetime

# --- ASTM control characters (E1381/E1394 low-level protocol) ---
ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
ETB = 0x17
EOT = 0x04
CR = 0x0D
LF = 0x0A

HOST = "0.0.0.0"
PORT = 6000

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# Friendly names/units for measured numeric test codes.
TEST_LABELS = {
    "WBC":       ("White Blood Cell count", "10^3/uL"),
    "RBC":       ("Red Blood Cell count", "10^6/uL"),
    "HGB":       ("Hemoglobin", "g/dL"),
    "HCT":       ("Hematocrit", "%"),
    "MCV":       ("Mean Corpuscular Volume", "fL"),
    "MCH":       ("Mean Corpuscular Hemoglobin", "pg"),
    "MCHC":      ("Mean Corpuscular Hemoglobin Concentration", "g/dL"),
    "PLT":       ("Platelet count", "10^3/uL"),
    "RDW-SD":    ("Red cell Distribution Width (SD)", "fL"),
    "RDW-CV":    ("Red cell Distribution Width (CV)", "%"),
    "PDW":       ("Platelet Distribution Width", "fL"),
    "MPV":       ("Mean Platelet Volume", "fL"),
    "P-LCR":     ("Platelet Large Cell Ratio", "%"),
    "PCT":       ("Plateletcrit", "%"),
    "NEUT#":     ("Neutrophils (absolute)", "10^3/uL"),
    "LYMPH#":    ("Lymphocytes (absolute)", "10^3/uL"),
    "MONO#":     ("Monocytes (absolute)", "10^3/uL"),
    "EO#":       ("Eosinophils (absolute)", "10^3/uL"),
    "BASO#":     ("Basophils (absolute)", "10^3/uL"),
    "NEUT%":     ("Neutrophils (%)", "%"),
    "LYMPH%":    ("Lymphocytes (%)", "%"),
    "MONO%":     ("Monocytes (%)", "%"),
    "EO%":       ("Eosinophils (%)", "%"),
    "BASO%":     ("Basophils (%)", "%"),
    "IG#":       ("Immature Granulocytes (absolute)", "10^3/uL"),
    "IG%":       ("Immature Granulocytes (%)", "%"),
    "MICROR":    ("Microcytic RBC ratio", "%"),
    "MACROR":    ("Macrocytic RBC ratio", "%"),
}

FLAG_LABELS = {
    "L": "LOW",
    "H": "HIGH",
    "N": "normal",
    "A": "ABNORMAL",
}

# Codes ending in "?" are Sysmex suspect-flag confidence scores (0-100), not measurements.
# Codes containing these prefixes are references to graphic files, not results.
GRAPHIC_PREFIXES = ("SCAT_", "DIST_")


def extract_code(test_field: str) -> str:
    """Pull the test code out of ASTM's nested ^^^^CODE^rep field."""
    parts = test_field.split("^")
    return parts[4] if len(parts) > 4 else test_field


def parse_result_record(fields):
    """
    R record layout (pipe-delimited):
    R|seq|^^^^TESTCODE^rep|value|unit|refrange|flag|...|status|operator|timestamp
    Returns a tuple: (category, formatted_line)
    category is one of: "result", "flag", "graphic"
    """
    if len(fields) < 4:
        return None

    test_field = fields[2]
    value = fields[3]
    flag = fields[6] if len(fields) > 6 else ""
    code = extract_code(test_field)

    if code.startswith(GRAPHIC_PREFIXES):
        return ("graphic", f"  - {code}: {value}")

    if code.endswith("?"):
        # Suspect-flag confidence score (0-100), not a measured value
        label = code[:-1].replace("_", " ")
        return ("flag", f"  - {label} (suspect score): {value}/100")

    if code in TEST_LABELS:
        label, unit = TEST_LABELS[code]
        flag_text = FLAG_LABELS.get(flag, flag)
        flag_note = f"  [{flag_text}]" if flag_text and flag_text != "normal" else ""
        return ("result", f"  - {label}: {value} {unit}{flag_note}")

    if not value:
        # Interpretive/clinical flag with no numeric value (e.g. "Anemia")
        flag_text = FLAG_LABELS.get(flag, flag)
        label = code.replace("_", " ")
        return ("flag", f"  - {label}: flagged {flag_text or '(present)'}")

    # Unknown numeric code we haven't labeled yet - still show it, just unlabeled
    flag_text = FLAG_LABELS.get(flag, flag)
    flag_note = f"  [{flag_text}]" if flag_text and flag_text != "normal" else ""
    return ("result", f"  - {code}: {value}{flag_note}")


def extract_specimen_id(fields):
    """
    O record layout (pipe-delimited), per ASTM spec:
    O|seq|specimen_id|instrument_specimen_id^^^^rep^B|test_list|...
    The actual sample/specimen ID is usually nested inside the
    instrument_specimen_id field between ^ separators, e.g. "^^2607044407^B".
    Falls back to the plain specimen_id field (index 2) if that's populated instead.
    """
    if len(fields) < 4:
        return None

    plain_specimen_id = fields[2].strip() if len(fields) > 2 else ""
    if plain_specimen_id:
        return plain_specimen_id

    instrument_field = fields[3] if len(fields) > 3 else ""
    parts = [p for p in instrument_field.split("^") if p]
    return parts[0] if parts else None


def parse_message(all_frames_text: str) -> str:
    """Turn the joined ASTM text into a human-readable, sectioned summary."""
    results, flags, graphics = [], [], []
    machine_id = None
    specimen_id = None

    for record in all_frames_text.split("\r"):
        record = record.strip()
        if not record:
            continue
        fields = record.split("|")
        rtype = fields[0][-1] if fields[0] else ""

        if rtype == "H" and len(fields) > 4:
            machine_id = fields[4].strip()
        elif rtype == "O":
            found_id = extract_specimen_id(fields)
            if found_id:
                specimen_id = found_id
        elif rtype == "R":
            parsed = parse_result_record(fields)
            if parsed:
                category, line = parsed
                if category == "result":
                    results.append(line)
                elif category == "flag":
                    flags.append(line)
                elif category == "graphic":
                    graphics.append(line)

    out = [f"=== Specimen ID: {specimen_id or 'UNKNOWN'} ===",
           f"Analyzer: {machine_id or 'unknown'}",
           f"Captured: {datetime.now().isoformat(timespec='seconds')}", ""]

    out.append("-- Measured Results --")
    out.extend(results if results else ["  (none)"])
    out.append("")
    out.append("-- Interpretive / Suspect Flags --")
    out.extend(flags if flags else ["  (none)"])
    out.append("")
    out.append("-- Referenced Graphics (filenames only, not embedded images) --")
    out.extend(graphics if graphics else ["  (none)"])

    # If we parsed nothing useful at all, something arrived that we're not
    # recognizing - show the raw text so we can see what it actually was
    # instead of just silently reporting "(none)" everywhere.
    if not results and not flags and not graphics:
        out.append("")
        out.append("-- RAW MESSAGE (nothing above was recognized - here's what arrived) --")
        out.append(all_frames_text.replace("\r", "\n"))

    return "\n".join(out), specimen_id


def save_report(report_text: str, specimen_id: str = None) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_id = (specimen_id or "unknown").replace("/", "-").replace("\\", "-")
    filename = f"result_{safe_id}_{timestamp}.txt"
    filepath = os.path.join(RESULTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)
    return filepath


def handle_connection(conn, addr):
    """Process one analyzer connection from ENQ through EOT."""
    print(f"Connected from: {addr}")
    buffer = b""
    message_text = ""

    while True:
        data = conn.recv(4096)
        if not data:
            print("Connection closed by analyzer.")
            return

        buffer += data

        while buffer:
            first_byte = buffer[0]

            if first_byte == ENQ:
                conn.sendall(bytes([ACK]))
                buffer = buffer[1:]

            elif first_byte == EOT:
                buffer = buffer[1:]
                report, specimen_id = parse_message(message_text)
                print("\n" + report)
                saved_path = save_report(report, specimen_id)
                print(f"(saved to {saved_path})\n")
                message_text = ""

            elif first_byte == STX:
                crlf_idx = buffer.find(bytes([CR, LF]))
                if crlf_idx == -1:
                    break
                full_frame = buffer[:crlf_idx + 2]
                buffer = buffer[crlf_idx + 2:]

                inner = full_frame[2:-4]
                try:
                    text = inner.decode("ascii", errors="replace")
                except Exception:
                    text = ""
                message_text += text + "\r"

                conn.sendall(bytes([ACK]))

            else:
                buffer = buffer[1:]


def run_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(5)  # allow a few pending connections to queue up, not just 1
    print(f"Listening on {HOST}:{PORT} ... waiting for results. Press Ctrl+C to stop.")

    try:
        while True:
            # Loop forever: after each result finishes, go straight back to
            # waiting for the next connection. This is what makes it a real
            # unattended listener instead of a one-shot script.
            conn, addr = s.accept()
            try:
                handle_connection(conn, addr)
            except Exception as e:
                print(f"Error handling connection from {addr}: {e}")
            finally:
                conn.close()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        s.close()


if __name__ == "__main__":
    run_server()
#!/usr/bin/env python3
"""
CYAN CYANVISION HL7 v2.3.1 / MLLP capture daemon.

Runs continuously as a server. The analyzer connects over MLLP and pushes
ORU^R01 result messages. Every OBX observation is decoded and written to
a local SQLite database (cyanvision.db), queryable with cyanvision_query.py.

MLLP framing: <VT> HL7 message (segments separated by \\r) <FS><CR>
  VT = 0x0B, FS = 0x1C, CR = 0x0D
Unlike ASTM, HL7/MLLP expects a proper HL7 ACK message back, not a
single-byte ACK - without it the analyzer will keep resending.
"""

import socket
import sqlite3
import os
from datetime import datetime

HOST = '0.0.0.0'
PORT = 6000
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cyanvision.db')

VT = b'\x0b'   # start block
FS = b'\x1c'   # end block
CR = b'\x0d'


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id       TEXT,
            message_type    TEXT,
            patient_name    TEXT,
            patient_id      TEXT,
            source_ip       TEXT,
            received_at     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            sample_id    TEXT,
            test         TEXT,
            value        TEXT,
            unit         TEXT,
            ref_range    TEXT,
            flag         TEXT,
            obs_time     TEXT,
            received_at  TEXT,
            raw          TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# HL7 decoding
# ---------------------------------------------------------------------------

def parse_message(raw: bytes):
    text = raw.decode('utf-8', errors='replace')
    segments = [s for s in text.split('\r') if s.strip()]
    return segments


def build_ack(control_id: str, sending_app='LABOIBS', sending_facility='LABOIBS') -> bytes:
    """Build a minimal HL7 ACK response wrapped in MLLP framing."""
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    msh = f"MSH|^~\\&|{sending_app}|{sending_facility}|||{ts}||ACK|{control_id}-ACK|P|2.3.1"
    msa = f"MSA|AA|{control_id}"
    hl7_msg = msh + '\r' + msa + '\r'
    return VT + hl7_msg.encode('utf-8') + FS + CR


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def store_sample(db, sample_id, message_type, patient_name, patient_id, source_ip):
    db.execute("DELETE FROM results WHERE sample_id = ?", (sample_id,))
    db.execute("DELETE FROM samples WHERE sample_id = ?", (sample_id,))
    db.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?)",
        (sample_id, message_type, patient_name, patient_id, source_ip, datetime.now().isoformat()),
    )
    db.commit()


def store_result(db, sample_id, test, value, unit, ref_range, flag, obs_time, raw):
    db.execute(
        "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?)",
        (sample_id, test, value, unit, ref_range, flag, obs_time, datetime.now().isoformat(), raw),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Segment handling
# ---------------------------------------------------------------------------

def handle_message(db, segments, source_ip):
    control_id = ''
    message_type = ''
    patient_name = ''
    patient_id = ''
    sample_id = ''
    result_count = 0

    for seg in segments:
        fields = seg.split('|')
        seg_type = fields[0]

        if seg_type == 'MSH':
            message_type = fields[8] if len(fields) > 8 else ''
            control_id = fields[9] if len(fields) > 9 else ''
            print(f'  [MSH] type={message_type} control_id={control_id}')

        elif seg_type == 'PID':
            patient_id = fields[2] if len(fields) > 2 else ''
            patient_name = fields[5] if len(fields) > 5 else ''
            sample_id = patient_id or control_id
            print(f'  [PID] {patient_name or "(none)"} (ID: {patient_id or "(none)"})')

        elif seg_type == 'OBR':
            order_id = fields[2] if len(fields) > 2 else ''
            if order_id:
                sample_id = order_id
            store_sample(db, sample_id or control_id, message_type, patient_name, patient_id, source_ip)
            print(f'  [OBR] sample {sample_id or control_id}')

        elif seg_type == 'OBX':
            test = fields[4] if len(fields) > 4 else (fields[3] if len(fields) > 3 else '')
            value = fields[5] if len(fields) > 5 else ''
            unit = fields[6] if len(fields) > 6 else ''
            ref_range = fields[7] if len(fields) > 7 else ''
            flag = fields[8] if len(fields) > 8 else ''
            obs_time = fields[13] if len(fields) > 13 else ''
            store_result(db, sample_id or control_id, test, value, unit, ref_range, flag, obs_time, seg)
            result_count += 1
            flag_str = f" [{flag.strip()}]" if flag.strip() else ''
            print(f"    {test:15s} {value:>10s} {unit:10s}{flag_str}")

    return control_id, result_count


def handle_connection(conn, addr, db):
    buffer = b''
    while True:
        try:
            data = conn.recv(4096)
        except ConnectionResetError:
            print('  connection reset by analyzer')
            break
        if not data:
            print('  connection closed by analyzer')
            break

        buffer += data

        while VT in buffer and FS in buffer:
            start = buffer.find(VT)
            end = buffer.find(FS, start)
            if end == -1:
                break
            message = buffer[start + 1:end]
            buffer = buffer[end + 2:]  # skip FS + trailing CR

            segments = parse_message(message)
            control_id, result_count = handle_message(db, segments, addr[0])
            print(f'  >> message complete ({result_count} results)')

            ack = build_ack(control_id or '0')
            conn.sendall(ack)


def main():
    db = init_db()
    print(f'Database: {DB_PATH}')

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    print(f'Listening on {HOST}:{PORT} - waiting for the analyzer...\n')

    try:
        while True:
            conn, addr = sock.accept()
            print(f'Connected by {addr[0]}:{addr[1]}')
            try:
                handle_connection(conn, addr, db)
            finally:
                conn.close()
            print('Ready for next connection.\n')
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        sock.close()
        db.close()


if __name__ == '__main__':
    main()

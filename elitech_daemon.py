#!/usr/bin/env python3
"""
ELITech Chemistry Analyzer LIS2-A capture daemon.

Runs continuously as a server. The analyzer connects and pushes
results as samples finish. Every result is decoded and written to
a local SQLite database (elitech.db), which can be queried with
the shared query tool.

LIS2-A protocol (similar to ASTM but different record types).
"""

import socket
import re
import sqlite3
import os
from datetime import datetime

HOST = '0.0.0.0'
PORT = 6000
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'elitech.db')

ENQ = b'\x05'
ACK = b'\x06'
NAK = b'\x15'
EOT = b'\x04'
STX = b'\x02'
ETX = b'\x03'
CR = b'\x0d'
LF = b'\x0a'


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id       TEXT,
            analyzer_model  TEXT,
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
            flag         TEXT,
            status       TEXT,
            result_time  TEXT,
            received_at  TEXT,
            raw          TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# LIS2-A decoding
# ---------------------------------------------------------------------------

def strip_frame(raw: bytes) -> str:
    """Extract payload from LIS2-A frame (STX...ETX + checksum + CR LF)."""
    text = raw.decode('utf-8', errors='replace')
    # Remove STX + frame number
    if text.startswith('\x02'):
        text = text[2:]
    # Cut at ETX
    if '\x03' in text:
        text = text.split('\x03')[0]
    return text.rstrip('\r\n')


RECORD_TYPES = {
    'H': 'Header', 'P': 'Patient', 'O': 'Order', 'C': 'Comment',
    'R': 'Result', 'Q': 'Query', 'L': 'Terminator',
}


def decode_record(line: str) -> dict:
    """Decode a single LIS2-A record line."""
    line = re.sub(r'^\d(?=[A-Z]\|)', '', line)
    if not line or '|' not in line:
        return {'type': 'Unknown', 'raw': line}

    rec_type = line[0]
    fields = line.split('|')
    kind = RECORD_TYPES.get(rec_type, f'Unknown({rec_type})')

    if rec_type == 'H':
        sender = fields[4] if len(fields) > 4 else ''
        parts = sender.split('^')
        return {
            'type': kind,
            'analyzer_model': parts[0].strip() if parts else '',
            'software_version': parts[1] if len(parts) > 1 else '',
            'raw': line,
        }

    if rec_type == 'P':
        patient_id = fields[2] if len(fields) > 2 else ''
        patient_name = fields[5] if len(fields) > 5 else ''
        return {
            'type': kind,
            'patient_id': patient_id,
            'patient_name': patient_name,
            'raw': line,
        }

    if rec_type == 'O':
        sample_field = fields[2] if len(fields) > 2 else ''
        sample_id = sample_field.strip() if sample_field else ''
        specimen_type = fields[3] if len(fields) > 3 else ''
        return {
            'type': kind,
            'sample_id': sample_id,
            'specimen_type': specimen_type,
            'raw': line,
        }

    if rec_type == 'R':
        seq = fields[1] if len(fields) > 1 else ''
        test_field = fields[2] if len(fields) > 2 else ''
        # Extract test name from ^-delimited field (e.g., "^^^Uree^Uree uv sl")
        test_parts = test_field.split('^')
        test_name = test_parts[-1] if test_parts else ''
        value = fields[3] if len(fields) > 3 else ''
        unit = fields[4] if len(fields) > 4 else ''
        flag = fields[6] if len(fields) > 6 else ''
        status = fields[8] if len(fields) > 8 else ''
        timestamp = fields[-1] if fields else ''
        return {
            'type': kind,
            'seq': seq,
            'test': test_name,
            'value': value,
            'unit': unit,
            'flag': flag,
            'status': status,
            'timestamp': timestamp,
            'raw': line,
        }

    return {'type': kind, 'raw': line}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def store_sample(db, sample_id, analyzer_model, patient_name, patient_id, source_ip):
    db.execute("DELETE FROM results WHERE sample_id = ?", (sample_id,))
    db.execute("DELETE FROM samples WHERE sample_id = ?", (sample_id,))
    db.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?)",
        (sample_id, analyzer_model, patient_name, patient_id, source_ip, datetime.now().isoformat()),
    )
    db.commit()


def store_result(db, sample_id, rec):
    db.execute(
        "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sample_id, rec['test'], rec['value'], rec['unit'], rec['flag'],
            rec['status'], rec['timestamp'],
            datetime.now().isoformat(), rec['raw'],
        ),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Session handling
# ---------------------------------------------------------------------------

def handle_connection(conn, addr, db):
    """Read from one analyzer connection. Handle ENQ..EOT batches."""
    buffer = b''
    current_sample = None
    analyzer_model = ''
    patient_name = ''
    patient_id = ''
    result_count = 0

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

        while buffer:
            b0 = buffer[0:1]

            if b0 == ENQ:
                conn.sendall(ACK)
                buffer = buffer[1:]
                continue

            if b0 == EOT:
                buffer = buffer[1:]
                print(f'  >> batch complete ({result_count} results so far)')
                continue

            if b0 == STX:
                idx = buffer.find(LF)
                if idx == -1:
                    break
                frame = buffer[:idx + 1]
                buffer = buffer[idx + 1:]
                conn.sendall(ACK)

                payload = strip_frame(frame)
                # LIS2-A frames may contain multiple records separated by \r
                records_text = payload.split('\r')

                for record_line in records_text:
                    if not record_line.strip():
                        continue
                    rec = decode_record(record_line)

                    if rec['type'] == 'Header':
                        analyzer_model = rec.get('analyzer_model', '')
                        print(f'  [HEADER] {analyzer_model}')
                    elif rec['type'] == 'Patient':
                        patient_id = rec.get('patient_id', '')
                        patient_name = rec.get('patient_name', '')
                        print(f'  [PATIENT] {patient_name} (ID: {patient_id})')
                    elif rec['type'] == 'Order':
                        current_sample = rec.get('sample_id', '')
                        store_sample(db, current_sample, analyzer_model, patient_name, patient_id, addr[0])
                        print(f'  [ORDER]  sample {current_sample}')
                    elif rec['type'] == 'Result':
                        if current_sample is not None:
                            store_result(db, current_sample, rec)
                            result_count += 1
                            print(f"    {rec['test']:20s} {rec['value']:>10s} {rec['unit']}")
                continue

            buffer = buffer[1:]


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

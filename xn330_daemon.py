#!/usr/bin/env python3
"""
Sysmex XN-330 ASTM capture daemon.

Runs continuously as a server. The analyzer (IPU) connects to it and pushes
results as samples finish. Every result is decoded and written to a local
SQLite database (xn330.db), which the query tool reads from.

This is the passive/server side - the ONLY connection model the analyzer
supports (the IPU is always the TCP client; it never listens).
"""

import socket
import re
import sqlite3
import os
from datetime import datetime

HOST = '0.0.0.0'
PORT = 6000
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xn330.db')

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
            serial_number   TEXT,
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
            operator     TEXT,
            result_time  TEXT,
            received_at  TEXT,
            raw          TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# ASTM decoding (same verified logic as the listener)
# ---------------------------------------------------------------------------

def strip_frame(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace')
    if text.startswith('\x02'):
        text = text[2:]          # drop STX + frame number digit
    if '\x03' in text:
        text = text.split('\x03')[0]   # cut at ETX, drop checksum+CRLF
    return text.rstrip('\r\n')


RECORD_TYPES = {
    'H': 'Header', 'P': 'Patient', 'O': 'Order', 'C': 'Comment',
    'R': 'Result', 'Q': 'Query', 'L': 'Terminator',
}


def decode_record(line: str) -> dict:
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
            'serial_number': parts[2] if len(parts) > 2 else '',
            'raw': line,
        }

    if rec_type == 'O':
        sample_field = fields[2] if len(fields) > 2 else ''
        sample_id = sample_field.split('^')[-2].strip() if '^' in sample_field else sample_field.strip()
        tests = fields[3] if len(fields) > 3 else ''
        test_names = [t.split('^')[-1] for t in tests.split('\\') if t]
        return {'type': kind, 'sample_id': sample_id, 'tests_ordered': test_names, 'raw': line}

    if rec_type == 'R':
        seq = fields[1] if len(fields) > 1 else ''
        test_field = fields[2] if len(fields) > 2 else ''
        test_name = test_field.split('^')[-2] if '^' in test_field else test_field
        value = fields[3] if len(fields) > 3 else ''
        unit = fields[4] if len(fields) > 4 else ''
        flag = fields[6] if len(fields) > 6 else ''
        status = fields[8] if len(fields) > 8 else ''
        operator = fields[10] if len(fields) > 10 else ''
        timestamp = fields[-1] if fields else ''
        return {
            'type': kind, 'seq': seq, 'test': test_name, 'value': value,
            'unit': unit, 'flag': flag, 'status': status, 'operator': operator,
            'timestamp': timestamp, 'raw': line,
        }

    if rec_type == 'C':
        comment = fields[3] if len(fields) > 3 else ''
        return {'type': kind, 'comment': comment, 'raw': line}

    return {'type': kind, 'raw': line}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def store_sample(db, sample_id, analyzer_model, serial, source_ip):
    # Fresh run for this sample: clear old rows so a query returns the latest.
    db.execute("DELETE FROM results WHERE sample_id = ?", (sample_id,))
    db.execute("DELETE FROM samples WHERE sample_id = ?", (sample_id,))
    db.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?)",
        (sample_id, analyzer_model, serial, source_ip, datetime.now().isoformat()),
    )
    db.commit()


def store_result(db, sample_id, rec):
    db.execute(
        "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            sample_id, rec['test'], rec['value'], rec['unit'], rec['flag'],
            rec['status'], rec['operator'], rec['timestamp'],
            datetime.now().isoformat(), rec['raw'],
        ),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Session handling
# ---------------------------------------------------------------------------

def handle_connection(conn, addr, db):
    """Read from one analyzer connection until it closes. Handles any number
    of ENQ..EOT batches over the same connection."""
    buffer = b''
    current_sample = None
    analyzer_model = ''
    serial = ''
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

        # Process every complete token currently in the buffer.
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
                # A data frame ends with LF. Wait until we have the whole thing.
                idx = buffer.find(LF)
                if idx == -1:
                    break  # incomplete frame, wait for more bytes
                frame = buffer[:idx + 1]
                buffer = buffer[idx + 1:]
                conn.sendall(ACK)

                payload = strip_frame(frame)
                rec = decode_record(payload)

                if rec['type'] == 'Header':
                    analyzer_model = rec.get('analyzer_model', '')
                    serial = rec.get('serial_number', '')
                    print(f'  [HEADER] {analyzer_model}  SN:{serial}')
                elif rec['type'] == 'Order':
                    current_sample = rec.get('sample_id', '')
                    store_sample(db, current_sample, analyzer_model, serial, addr[0])
                    print(f'  [ORDER]  sample {current_sample}')
                elif rec['type'] == 'Result':
                    if current_sample is not None:
                        store_result(db, current_sample, rec)
                        result_count += 1
                        print(f"    {rec['test']:20s} {rec['value']:>10s} {rec['unit']}")
                continue

            # Stray byte (leftover CR/LF, ACK echo, etc.) - discard it.
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

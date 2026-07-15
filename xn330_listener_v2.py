#!/usr/bin/env python3
"""
Sysmex XN-330 ASTM listener with active query capability.
Now sends host-initiated queries to ask for specific sample data.
"""

import socket
import re
import json
from datetime import datetime

HOST = '0.0.0.0'
PORT = 6000

ENQ = b'\x05'
ACK = b'\x06'
NAK = b'\x15'
EOT = b'\x04'
STX = b'\x02'
ETX = b'\x03'
CR = b'\x0d'
LF = b'\x0a'


def build_frame(frame_no: int, text: str) -> bytes:
    """Build a properly framed + checksummed ASTM message."""
    body = f"{frame_no}{text}\r".encode('ascii')
    payload = body + ETX
    checksum = sum(payload) & 0xFF
    checksum_hex = f"{checksum:02X}".encode('ascii')
    return STX + payload + checksum_hex + CR + LF


def build_query(sample_id: str, seq: int = 1, frame_no: int = 1) -> bytes:
    """Build a Query record: Q|seq|^SampleID"""
    text = f"Q|{seq}|^{sample_id}"
    return build_frame(frame_no, text)


def strip_frame(raw: bytes) -> str:
    """Strip ASTM framing, return payload text."""
    text = raw.decode('utf-8', errors='replace')
    if text.startswith('\x02'):
        text = text[2:]
    if '\x03' in text:
        text = text.split('\x03')[0]
    return text.rstrip('\r\n')


RECORD_TYPES = {
    'H': 'Header',
    'P': 'Patient',
    'O': 'Order',
    'C': 'Comment',
    'R': 'Result',
    'Q': 'Query',
    'L': 'Terminator',
}


def decode_record(line: str) -> dict:
    """Decode a single ASTM record line."""
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
        return {
            'type': kind,
            'sample_id': sample_id,
            'tests_ordered': test_names,
            'raw': line,
        }

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
            'type': kind,
            'seq': seq,
            'test': test_name,
            'value': value,
            'unit': unit,
            'flag': flag,
            'status': status,
            'operator': operator,
            'timestamp': timestamp,
            'raw': line,
        }

    if rec_type == 'P':
        return {'type': kind, 'raw': line}

    if rec_type == 'C':
        comment = fields[3] if len(fields) > 3 else ''
        return {'type': kind, 'comment': comment, 'raw': line}

    if rec_type == 'L':
        return {'type': kind, 'raw': line}

    return {'type': kind, 'raw': line}


def print_record(rec: dict):
    t = rec['type']
    if t == 'Header':
        print(f"[HEADER] {rec['analyzer_model']}  SN:{rec['serial_number']}  SW:{rec['software_version']}")
    elif t == 'Order':
        print(f"[ORDER]  Sample: {rec['sample_id']}  Tests: {', '.join(rec['tests_ordered'][:5])}...")
    elif t == 'Result':
        flag_str = f" [{rec['flag']}]" if rec['flag'] else ''
        print(f"  {rec['test']:20s} {rec['value']:>10s} {rec['unit']:10s}{flag_str}")
    elif t == 'Terminator':
        print(f"[END]    {rec['raw']}")
    else:
        print(f"[{t.upper()}]  {rec['raw']}")


def main():
    session_records = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    print(f'Listening on {HOST}:{PORT}...')
    conn, addr = sock.accept()
    print(f'Connected by {addr}\n')

    query_sent = False
    frame_no = 0

    try:
        while True:
            data = conn.recv(4096)
            if not data:
                print('Connection closed')
                break

            if data == ENQ:
                conn.sendall(ACK)
                continue

            if data == EOT:
                print('\n>> Session ended (EOT)')
                break

            if data.startswith(STX):
                payload = strip_frame(data)
                conn.sendall(ACK)
                rec = decode_record(payload)
                session_records.append(rec)
                print_record(rec)

                # After receiving Header, send a query for a specific sample
                if rec['type'] == 'Header' and not query_sent:
                    frame_no = 1
                    sample_id = input("\n>> Enter sample ID to query (or press Enter to skip): ").strip()
                    if sample_id:
                        query_frame = build_query(sample_id, seq=1, frame_no=frame_no)
                        print(f">> Sending query for sample: {sample_id}")
                        print(f"   Raw bytes: {query_frame}")
                        conn.sendall(query_frame)
                        query_sent = True
                    else:
                        query_sent = True  # Mark sent to avoid asking again

                continue

            # Unknown byte
            conn.sendall(ACK)

    except KeyboardInterrupt:
        print('\nStopped by user.')
    finally:
        conn.close()
        sock.close()

    results = [r for r in session_records if r['type'] == 'Result']
    out = {
        'received_at': datetime.now().isoformat(),
        'source': addr[0],
        'records': session_records,
        'results': results,
    }
    fname = f"xn330_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(fname, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'\nSaved {len(results)} result records to {fname}')


if __name__ == '__main__':
    main()

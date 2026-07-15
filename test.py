

import socket
import re
import json
from datetime import datetime

HOST = '0.0.0.0'   # bind to all interfaces so it works regardless of which
                    # link-local/static IP is currently active
PORT = 6000

ENQ = b'\x05'
ACK = b'\x06'
NAK = b'\x15'
EOT = b'\x04'
STX = b'\x02'
ETX = b'\x03'
CR = b'\x0d'
LF = b'\x0a'


# ---------------------------------------------------------------------------
# Frame building (for host-initiated messages, e.g. queries)
# ---------------------------------------------------------------------------

def build_frame(frame_no: int, text: str) -> bytes:
    """Build a properly framed + checksummed ASTM message ready to send."""
    body = f"{frame_no}{text}\r".encode('ascii')
    payload = body + ETX
    checksum = sum(payload) & 0xFF
    checksum_hex = f"{checksum:02X}".encode('ascii')
    return STX + payload + checksum_hex + CR + LF


def build_query(sample_id: str, seq: int = 1, frame_no: int = 1) -> bytes:
    """
    Build a host -> IPU Query (Q) record asking for analysis order info
    on a given sample ID. General ASTM format: Q|seq|^SampleID

    NOTE: this follows the general ASTM E1381/E1394 query convention seen
    across ASTM-based clinical analyzers. Sysmex XN may expect additional
    sub-fields specific to its spec. Treat this as a first attempt and
    confirm the analyzer responds (rather than NAKs) before relying on it.
    """
    text = f"Q|{seq}|^{sample_id}"
    return build_frame(frame_no, text)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def strip_frame(raw: bytes) -> str:
    """Strip STX/frame-number/ETX/checksum/CRLF wrapper, return payload text."""
    text = raw.decode('utf-8', errors='replace')
    if text.startswith('\x02'):
        text = text[2:]          # drop STX + frame number digit
    if '\x03' in text:
        text = text.split('\x03')[0]   # cut at ETX, drop checksum+CRLF
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
    """Decode a single ASTM record line into a structured dict by type."""
    # strip leading frame-number digit, e.g. "6R|1|..." -> "R|1|..."
    line = re.sub(r'^\d(?=[A-Z]\|)', '', line)
    if not line or '|' not in line:
        return {'type': 'Unknown', 'raw': line}

    rec_type = line[0]
    fields = line.split('|')
    kind = RECORD_TYPES.get(rec_type, f'Unknown({rec_type})')

    if rec_type == 'H':
        # H|\^&|||    XN-330^00-29^18762^^^^CX851950||||||||E1394-97
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
        # O|1||^^   sample_id^M|test_list...
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
        # R|seq|^^^^TESTNAME^rep|value|unit||flag||status||operator||timestamp
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
            'flag': flag,        # L=low, H=high, A=abnormal, N=normal
            'status': status,    # F=final
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


# ---------------------------------------------------------------------------
# Main listener
# ---------------------------------------------------------------------------

def main():
    session_records = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    print(f'Listening on {HOST}:{PORT} (all interfaces)...')
    conn, addr = sock.accept()
    print(f'Connected by {addr}\n')

    try:
        while True:
            data = conn.recv(4096)
            if not data:
                print('Connection closed by analyzer')
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
                continue

            # Unknown byte sequence, ACK to keep protocol moving
            conn.sendall(ACK)

        # --- Example: send a host-initiated query on a NEW session ---
        # The analyzer always initiates the TCP connection (see docstring),
        # so a query can only be sent *after* it connects and during an
        # active session - e.g. right after receiving its Header record,
        # before it proceeds to send results on its own.
        #
        # query_frame = build_query(sample_id="100")
        # conn.sendall(query_frame)
        # response = conn.recv(4096)
        # print(f"Query response: {response}")

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
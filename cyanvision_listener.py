#!/usr/bin/env python3
"""
CYAN CYANVISION - raw MLLP/HL7 capture listener.

Diagnostic-only tool: prints every complete HL7 message received, and
replies with a real HL7 ACK (not a raw byte) so the analyzer doesn't
retry sending. Use this to inspect new/unknown message types before
extending cyanvision_daemon.py's parsing logic.
"""

import socket
from datetime import datetime

HOST = '0.0.0.0'
PORT = 6000

VT = b'\x0b'   # start block
FS = b'\x1c'   # end block
CR = b'\x0d'


def build_ack(control_id: str) -> bytes:
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    msh = f"MSH|^~\\&|LABOIBS|LABOIBS|||{ts}||ACK|{control_id}-ACK|P|2.3.1"
    msa = f"MSA|AA|{control_id}"
    hl7_msg = msh + '\r' + msa + '\r'
    return VT + hl7_msg.encode('utf-8') + FS + CR


sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((HOST, PORT))
sock.listen(1)
print(f'Listening on {HOST}:{PORT} for CYAN CYANVISION (raw HL7 dump)...\n')

try:
    while True:
        conn, addr = sock.accept()
        print(f'Connected by {addr}')
        conn.settimeout(120)
        buffer = b''
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    print('  connection closed\n')
                    break
                buffer += data

                while VT in buffer and FS in buffer:
                    start = buffer.find(VT)
                    end = buffer.find(FS, start)
                    if end == -1:
                        break
                    message = buffer[start + 1:end]
                    buffer = buffer[end + 2:]

                    text = message.decode('utf-8', errors='replace')
                    print('--- HL7 message ---')
                    for seg in text.split('\r'):
                        if seg.strip():
                            print(f'  {seg}')
                    print('-------------------')

                    control_id = ''
                    for seg in text.split('\r'):
                        if seg.startswith('MSH|'):
                            fields = seg.split('|')
                            control_id = fields[9] if len(fields) > 9 else ''
                    conn.sendall(build_ack(control_id or '0'))
                    print('  >> sent HL7 ACK\n')
        except socket.timeout:
            print('  timeout, closing\n')
        finally:
            conn.close()
except KeyboardInterrupt:
    print('\nStopped.')
finally:
    sock.close()

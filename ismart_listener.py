#!/usr/bin/env python3
"""
I-Smart 30 PRO - raw capture listener.

Just listens and prints everything received, so we can see the real
protocol/record format this analyzer uses before writing a proper decoder.

Analyzer is configured to auto-send to this host on port 6000.
"""

import socket

HOST = '0.0.0.0'
PORT = 6000

ENQ = b'\x05'
ACK = b'\x06'
EOT = b'\x04'
STX = b'\x02'

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((HOST, PORT))
sock.listen(1)
print(f'Listening on {HOST}:{PORT} for I-Smart 30 PRO...\n')

try:
    while True:
        conn, addr = sock.accept()
        print(f'Connected by {addr}')
        conn.sendall(ACK)
        print('  >> Sent initial ACK (handshake for TCP/IP test)')
        conn.settimeout(120)
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    print('  connection closed\n')
                    break

                print(f'Received: {data!r}')

                if data == ENQ:
                    print('  >> Got ENQ, sending ACK')
                    conn.sendall(ACK)
                elif data == EOT:
                    print('  >> Got EOT (end of transmission)\n')
                elif data.startswith(STX):
                    conn.sendall(ACK)
                    print('  >> Got data frame, sent ACK')
                else:
                    conn.sendall(ACK)
        except socket.timeout:
            print('  timeout, closing\n')
        finally:
            conn.close()
except KeyboardInterrupt:
    print('\nStopped.')
finally:
    sock.close()

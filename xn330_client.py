#!/usr/bin/env python3
"""
Sysmex XN-330 ASTM Bidirectional Server.
Binds to local port, captures the machine's outbound dial, and injects a Host Query.
"""
import socket
import re
import json
from datetime import datetime

HOST = '0.0.0.0'  # Listens on all available local network cards
PORT = 6000

ENQ = b'\x05'
ACK = b'\x06'
NAK = b'\x15'
EOT = b'\x04'
STX = b'\x02'
ETX = b'\x03'
CR  = b'\x0d'
LF  = b'\x0a'

def build_frame(frame_no: int, text: str) -> bytes:
    """Build a properly framed + checksummed ASTM message."""
    body = f"{frame_no}{text}\r".encode('ascii')
    payload = body + ETX
    checksum = sum(payload) & 0xFF
    checksum_hex = f"{checksum:02X}".encode('ascii')
    return STX + payload + checksum_hex + CR + LF

def send_record_and_wait(conn, frame_no: int, text: str) -> bool:
    """Sends an ASTM record and expects an ACK from the analyzer."""
    frame = build_frame(frame_no, text)
    print(f">> Sending: {text.strip()}")
    conn.sendall(frame)
    reply = conn.recv(1024)
    if reply == ACK:
        return True
    print(f"!! Expected ACK, but analyzer returned: {reply}")
    return False

def execute_host_query(conn, sample_id: str):
    """
    Seizes line control and transmits an ASTM query block.
    Flow required by Sysmex: ENQ -> (Wait ACK) -> H -> Q -> L -> EOT
    """
    print(f"\n--- Seizing Line Control to Query Sample: {sample_id} ---")
    
    # Send ENQ to demand line control
    conn.sendall(ENQ)
    reply = conn.recv(1024)
    if reply != ACK:
        print("!! Analyzer refused line control.")
        return False
        
    # 1. Send Header Record (Frame 1)
    if not send_record_and_wait(conn, 1, "H|\\^&|||LIS|||||||||1394-97"):
        conn.sendall(EOT)
        return False
        
    # 2. Send Query Record (Frame 2)
    # Strip down trailing delimiters to strictly match the 13-field structural array
    # Sysmex format: Q | 1 | ^SampleID | | | | | | | | | | A
    # 'A' tells the machine to look up and return ALL matching stored database results.
    query_string = f"Q|1|^{sample_id}||||||||||A"
    if not send_record_and_wait(conn, 2, query_string):
        conn.sendall(EOT)
        return False
        
    # 3. Send Terminator Record (Frame 3)
    if not send_record_and_wait(conn, 3, "L|1|N"):
        conn.sendall(EOT)
        return False
        
    # Release line back to idle status
    conn.sendall(EOT)
    print("--- Query Transmitted Successfully. Relinquishing line control. ---\n")
    return True

def strip_frame(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace')
    if text.startswith('\x02'): text = text[2:]
    if '\x03' in text: text = text.split('\x03')[0]
    return text.rstrip('\r\n')

RECORD_TYPES = {'H': 'Header', 'P': 'Patient', 'O': 'Order', 'C': 'Comment', 'R': 'Result', 'Q': 'Query', 'L': 'Terminator'}

def decode_record(line: str) -> dict:
    line = re.sub(r'^\d(?=[A-Z]\|)', '', line)
    if not line or '|' not in line:
        return {'type': 'Unknown', 'raw': line}
    rec_type = line[0]
    fields = line.split('|')
    kind = RECORD_TYPES.get(rec_type, f'Unknown({rec_type})')
    
    if rec_type == 'O':
        sample_field = fields[2] if len(fields) > 2 else ''
        sample_id = sample_field.split('^')[-2].strip() if '^' in sample_field else sample_field.strip()
        return {'type': kind, 'sample_id': sample_id, 'raw': line}
    if rec_type == 'R':
        test_field = fields[2] if len(fields) > 2 else ''
        test_name = test_field.split('^')[-2] if '^' in test_field else test_field
        value = fields[3] if len(fields) > 3 else ''
        unit = fields[4] if len(fields) > 4 else ''
        flag = fields[6] if len(fields) > 6 else ''
        return {'type': kind, 'test': test_name, 'value': value, 'unit': unit, 'flag': flag, 'raw': line}
    return {'type': kind, 'raw': line}

def main():
    sample_id = input("Enter sample ID to pull from machine history: ").strip()
    if not sample_id:
        print("Sample ID is required.")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    
    print(f"\n[SERVER] Waiting for Sysmex analyzer to connect to your PC on port {PORT}...")
    conn, addr = sock.accept()
    print(f"[SERVER] Analyzer connected from IP: {addr[0]}")
    conn.settimeout(10)

    # Execute our active query immediately upon connection interception
    query_success = execute_host_query(conn, sample_id)
    
    if not query_success:
        print("Aborting data collection due to handshake failure.")
        conn.close()
        sock.close()
        return

    print("Now listening for the data response blocks from the machine...")
    session_records = []
    
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
                
            if data == ENQ:
                conn.sendall(ACK)
                continue
            elif data == EOT:
                print('>> Analyzer finished sending data (EOT received).')
                break
            elif data.startswith(STX):
                payload = strip_frame(data)
                conn.sendall(ACK)  # Acknowledge the frame packet
                
                rec = decode_record(payload)
                session_records.append(rec)
                
                if rec['type'] == 'Result':
                    print(f" [RESULT] {rec['test']:15s} -> {rec['value']} {rec['unit']} {rec['flag']}")
                else:
                    print(f" [{rec['type'].upper()}] Record Processed")
    except socket.timeout:
        print("\nSession hit idle timeout window.")
    finally:
        conn.close()
        sock.close()

    if session_records:
        results = [r for r in session_records if r['type'] == 'Result']
        out = {
            'timestamp': datetime.now().isoformat(),
            'queried_sample': sample_id,
            'records': session_records
        }
        fname = f"xn330_data_{sample_id}.json"
        with open(fname, 'w') as f:
            json.dump(out, f, indent=2)
        print(f"\nSuccess! Stored data blocks to {fname}")

if __name__ == '__main__':
    main()

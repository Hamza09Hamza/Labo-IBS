#!/usr/bin/env python3
"""
Sysmex XN-330 Direct Active Query Engine.
Immediately sends ENQ upon connection to catch the analyzer while it is listening.
"""
import socket
import re
import json
import time
from datetime import datetime

HOST = '0.0.0.0'
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
    print(f">> TX Record: {text.strip()}")
    conn.sendall(frame)
    try:
        reply = conn.recv(1024)
        if reply == ACK:
            return True
        print(f"!! Expected ACK, got: {reply}")
    except socket.error as e:
        print(f"!! Socket error during TX: {e}")
    return False

def execute_host_query(conn, sample_id: str):
    """
    Immediately seizes line control and transmits the query block.
    """
    print(f"\n[QUERY ENGINE] Sending active ENQ to seize line control...")
    conn.sendall(ENQ)
    try:
        reply = conn.recv(1024)
    except socket.error:
        print("!! Connection timed out or reset during line seizure.")
        return False

    if reply != ACK:
        print(f"!! Analyzer refused line control. Expected ACK, got: {reply}")
        return False
        
    print("[QUERY ENGINE] Line seized successfully. Delivering ASTM frames...")
    
    # 1. Header Frame
    if not send_record_and_wait(conn, 1, "H|\\^&|||LIS|||||||||1394-97"):
        conn.sendall(EOT)
        return False
        
    # 2. Query Frame (Clean Sysmex XN format for target Sample Lookup)
    query_string = f"Q|1|^{sample_id}||ALL||||||||A"
    if not send_record_and_wait(conn, 2, query_string):
        conn.sendall(EOT)
        return False
        
    # 3. Terminator Frame
    if not send_record_and_wait(conn, 3, "L|1|N"):
        conn.sendall(EOT)
        return False
        
    # Release Line so the analyzer can switch to Send Mode and answer us
    conn.sendall(EOT)
    print("[QUERY ENGINE] Session released to analyzer. Awaiting data stream...\n")
    return True

# Simple regex-free record decoder for quick testing output
def fast_log_payload(payload: str):
    if '|' in payload:
        parts = payload.split('|')
        rec_type = parts[0][-1] if parts[0] else ''
        if rec_type == 'R' and len(parts) > 4:
            print(f" [RESULT] Parameter: {parts[2]:12s} Value: {parts[3]:10s} Unit: {parts[4]}")
        elif rec_type in ['H', 'O', 'P', 'L']:
            print(f" [{rec_type.upper()} RECORD] {payload[:50]}...")

def main():
    sample_id = input("Enter sample ID to query automatically: ").strip()
    if not sample_id:
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    
    print(f"\n[SERVER] Direct Engine listening on port {PORT}...")
    conn, addr = sock.accept()
    print(f"[SERVER] Machine socket connected from {addr[0]}")
    conn.settimeout(8) # 8 second window for response processing

    try:
        # Step 1: Fire the Query sequence instantly upon network connection
        if execute_host_query(conn, sample_id):
            
            # Step 2: Now wait for the machine to send its response data blocks
            print("[PHASE 2] Listening for returning data blocks...")
            while True:
                data = conn.recv(4096)
                if not data:
                    print(">> Connection closed by machine.")
                    break
                
                if data == ENQ:
                    conn.sendall(ACK)
                    continue
                elif data == EOT:
                    print('>> Session finished completely (EOT received).')
                    break
                elif data.startswith(STX):
                    # We caught a data frame packet! Acknowledge it instantly.
                    conn.sendall(ACK)
                    
                    # Clean and display the raw payload text
                    raw_text = data.decode('utf-8', errors='ignore')
                    clean_text = raw_text.split('\x03')[0] if '\x03' in raw_text else raw_text
                    clean_text = clean_text.replace('\x02', '').strip()
                    
                    fast_log_payload(clean_text)

    except ConnectionResetError:
        print("\n!! Error: Analyzer forcefully reset the connection (WinError 10054).")
        print(">> This means the machine received the query, but rejected sample ID format or database pulling via host is disabled.")
    except socket.timeout:
        print("\nSession hit idle timeout waiting for analyzer data response.")
    finally:
        conn.close()
        sock.close()

if __name__ == '__main__':
    main()

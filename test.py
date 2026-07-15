#!/usr/bin/env python3
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
    body = f"{frame_no}{text}\r".encode('ascii')
    payload = body + ETX
    checksum = sum(payload) & 0xFF
    checksum_hex = f"{checksum:02X}".encode('ascii')
    return STX + payload + checksum_hex + CR + LF

def send_record_and_wait(conn, frame_no: int, text: str) -> bool:
    frame = build_frame(frame_no, text)
    print(f">> TX Query Block: {text.strip()}")
    conn.sendall(frame)
    try:
        reply = conn.recv(1024)
        if reply == ACK:
            return True
        print(f"!! Expected ACK, got: {reply}")
    except socket.error as e:
        print(f"!! Socket error during query TX: {e}")
    return False

def execute_host_query(conn, sample_id: str):
    """
    Safely initiates a background query payload.
    Uses 'R' flag in 13th field to pull results instead of orders.
    """
    print(f"\n[QUERY ENGINE] Sending background fetch request for Sample: {sample_id}...")
    
    # Force Line Control
    conn.sendall(ENQ)
    try:
        reply = conn.recv(1024)
    except socket.error:
        print("!! Connection timed out or reset during line seizure.")
        return False

    if reply != ACK:
        print(f"!! Analyzer refused line control. Sent ENQ, got: {reply}")
        return False
        
    # 1. Header Frame
    if not send_record_and_wait(conn, 1, "H|\\^&|||LIS|||||||||1394-97"):
        conn.sendall(EOT)
        return False
        
    # 2. Query Frame (13th field set to 'R' for Results Lookup)
    query_string = f"Q|1|^{sample_id}||||||||||R"
    if not send_record_and_wait(conn, 2, query_string):
        conn.sendall(EOT)
        return False
        
    # 3. Terminator Frame
    if not send_record_and_wait(conn, 3, "L|1|N"):
        conn.sendall(EOT)
        return False
        
    # Release Line back to listen state
    conn.sendall(EOT)
    print("[QUERY ENGINE] Query frame broadcasted. Waiting for machine response loop...\n")
    return True

def main():
    sample_id = input("Enter sample ID to query automatically: ").strip()
    if not sample_id:
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(1)
    
    print(f"\n[SERVER] Automated background engine listening on port {PORT}...")
    conn, addr = sock.accept()
    print(f"[SERVER] Machine connected from {addr[0]}")
    conn.settimeout(5) # tight timeout windows for active swaps

    session_records = []
    
    try:
        # Step 1: Let the machine speak first or clear its buffers
        print("[PHASE 1] Initializing stream. Letting machine flush state...")
        initial_data = conn.recv(1024)
        
        if initial_data == ENQ:
            # If the machine wanted to send something, acknowledge it and let it finish
            print(">> Machine sent ENQ. Sending ACK to drain its buffer...")
            conn.sendall(ACK)
            
            # Read until machine finishes its thought with an EOT
            while True:
                chunk = conn.recv(4096)
                if not chunk or chunk == EOT:
                    print(">> Machine buffer flushed (EOT received).")
                    break
                if chunk.startswith(STX):
                    conn.sendall(ACK) # Keep it happy until it finishes
        
        # Give the machine a brief 200ms processing pause to stabilize its state machine
        time.sleep(0.2)
        
        # Step 2: Now that the line is idle, trigger our query
        query_sent = execute_host_query(conn, sample_id)
        
        if query_sent:
            print("[PHASE 2] Collecting incoming data packets matching query...")
            # Step 3: Listen for the database response returning back to us
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                
                if data == ENQ:
                    conn.sendall(ACK)
                    continue
                elif data == EOT:
                    print('>> Target results streamed completely (EOT received).')
                    break
                elif data.startswith(STX):
                    # We are receiving data frames!
                    conn.sendall(ACK)
                    print(f" [DATA RECEIVED] {len(data)} raw bytes caught.")
                    # (Insert your decode_record logic here to append to session_records)

    except ConnectionResetError:
        print("\n!! Error: Analyzer still dropped connection. Verification required.")
        print(">> Check if IPU Setting 'Realtime Query' or 'Host Query' is checked on the machine.")
    except socket.timeout:
        print("\nSession hit idle timeout window.")
    finally:
        conn.close()
        sock.close()

if __name__ == '__main__':
    main()

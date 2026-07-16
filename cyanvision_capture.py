import socket
import os
from datetime import datetime

# --- Generic connection tester / capture tool for the CyanVision machine ---
#
# We don't yet know what protocol CyanVision speaks (ASTM low-level like the
# hematology analyzer, HL7, or something proprietary). This script just
# accepts the incoming TCP connection, ACKs common ASTM control bytes if it
# sees them (harmless if it's not ASTM), and logs everything raw so we can
# inspect it and write a real parser afterwards.

ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
ETB = 0x17
EOT = 0x04
CR = 0x0D
LF = 0x0A

HOST = "0.0.0.0"
PORT = 6000

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cyanvision_captures")


def hex_dump(data: bytes) -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def run_server():
    os.makedirs(CAPTURE_DIR, exist_ok=True)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(1)
    print(f"Listening on {HOST}:{PORT} ... waiting for the CyanVision machine to connect.")

    conn, addr = s.accept()
    print(f"Connected from: {addr}")

    session_name = f"cyanvision_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    raw_path = os.path.join(CAPTURE_DIR, f"{session_name}.raw")
    log_path = os.path.join(CAPTURE_DIR, f"{session_name}.log")

    all_bytes = b""

    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            def log(msg):
                print(msg)
                logf.write(msg + "\n")
                logf.flush()

            log(f"Connected from {addr} at {datetime.now().isoformat(timespec='seconds')}")

            while True:
                data = conn.recv(4096)
                if not data:
                    log("Connection closed by CyanVision machine.")
                    break

                all_bytes += data

                log(f"\n--- received {len(data)} bytes at {datetime.now().isoformat(timespec='seconds')} ---")
                log(hex_dump(data))

                # Best-effort ASCII view, useful if this turns out to be
                # ASTM/HL7 (both are mostly printable ASCII with CR/LF framing).
                ascii_preview = data.decode("ascii", errors="replace")
                log("ASCII preview:")
                log(repr(ascii_preview))

                # If it looks like ASTM low-level framing, ACK the control
                # bytes so the machine doesn't stall waiting for a handshake.
                # Harmless no-op for any other protocol since we only react
                # to these exact single bytes.
                if data == bytes([ENQ]):
                    conn.sendall(bytes([ACK]))
                    log("-> sent ACK (in response to ENQ)")
                elif data.startswith(bytes([STX])):
                    conn.sendall(bytes([ACK]))
                    log("-> sent ACK (in response to STX frame)")
                elif data == bytes([EOT]):
                    log("-> received EOT (end of transmission)")

    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        conn.close()
        s.close()
        with open(raw_path, "wb") as f:
            f.write(all_bytes)
        print(f"\nRaw bytes saved to: {raw_path}")
        print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    run_server()

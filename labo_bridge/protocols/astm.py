"""
ASTM E1381/E1394 (and the closely-related LIS2-A) low-level protocol.

This is the control-character handshake shared by the Sysmex XN-330,
I-Smart 30, and Selectra (chemistry analyzer, runs the ELITech/LIS2-A
software stack) analyzers. The framing is:

    ENQ                         -> host replies ACK (line seized)
    STX <frame#> <text> ETX <checksum> CR LF   -> host replies ACK
    ...more frames...
    EOT                         -> end of transmission

A single STX frame's <text> can itself contain several records joined by CR.
This module only handles framing; turning records into results is the job of
the per-machine decoders.
"""

# Control bytes (as ints, for comparing buffer[0])
ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
ETB = 0x17
EOT = 0x04
CR = 0x0D
LF = 0x0A

# Same values as single bytes, convenient for sendall()
B_ENQ = bytes([ENQ])
B_ACK = bytes([ACK])
B_NAK = bytes([NAK])
B_EOT = bytes([EOT])
B_STX = bytes([STX])


def build_frame(frame_no: int, text: str) -> bytes:
    """
    Build an ASTM frame for the *host* to send (used in host-query mode).
    Layout: STX <frame#> <text> CR ETX <checksum-2hex> CR LF
    Checksum = sum of bytes from just after STX through ETX, mod 256.
    """
    body = f"{frame_no}{text}\r".encode("ascii")
    payload = body + bytes([ETX])
    checksum = sum(payload) & 0xFF
    checksum_hex = f"{checksum:02X}".encode("ascii")
    return bytes([STX]) + payload + checksum_hex + bytes([CR, LF])


def strip_frame(raw: bytes) -> str:
    """
    Extract the record text from a received STX...ETX frame.
    Drops the leading STX + frame-number digit and everything from ETX on
    (checksum + CR LF). Returns the inner text, right-stripped of CR/LF.
    """
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("\x02"):
        text = text[2:]  # drop STX + single frame-number digit
    if "\x03" in text:
        text = text.split("\x03")[0]  # cut at ETX
    return text.rstrip("\r\n")


def split_records(payload: str):
    """A frame's payload may hold multiple records joined by CR."""
    return [r for r in payload.split("\r") if r.strip()]

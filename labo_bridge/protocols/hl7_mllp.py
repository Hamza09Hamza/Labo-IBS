"""
HL7 v2.x over MLLP (Minimal Lower Layer Protocol) - used by CyanVision.

MLLP framing wraps each HL7 message as:  <VT> <message> <FS> <CR>
  VT (0x0B) = start block, FS (0x1C) = end block.
HL7 segments within the message are separated by CR (0x0D).

Unlike ASTM, the analyzer expects a full HL7 ACK message back (MSH + MSA),
not a single ACK byte - otherwise it keeps resending.
"""

from datetime import datetime

VT = 0x0B  # start block
FS = 0x1C  # end block
CR = 0x0D

B_VT = bytes([VT])
B_FS = bytes([FS])


def iter_messages(buffer: bytes):
    """
    Yield (message_bytes, remaining_buffer) for each complete MLLP-framed
    message found in `buffer`. Caller replaces its buffer with the returned
    remainder after processing each message.
    """
    while B_VT in buffer and B_FS in buffer:
        start = buffer.find(B_VT)
        end = buffer.find(B_FS, start)
        if end == -1:
            break
        message = buffer[start + 1:end]
        buffer = buffer[end + 2:]  # skip FS + trailing CR
        yield message, buffer


def split_segments(message: bytes):
    text = message.decode("utf-8", errors="replace")
    return [s for s in text.split("\r") if s.strip()]


def build_ack(control_id: str, sending_app="LABOIBS", sending_facility="LABOIBS",
              version="2.3.1") -> bytes:
    """Build a minimal positive HL7 ACK wrapped in MLLP framing."""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    msh = (f"MSH|^~\\&|{sending_app}|{sending_facility}|||{ts}||ACK|"
           f"{control_id}-ACK|P|{version}")
    msa = f"MSA|AA|{control_id}"
    hl7_msg = msh + "\r" + msa + "\r"
    return B_VT + hl7_msg.encode("utf-8") + B_FS + bytes([CR])

"""CYANVision HL7 v2.3.1 worklist messages over MLLP."""

from __future__ import annotations

from datetime import datetime

from labo_bridge.protocols import hl7_mllp


def _field(segments: list[str], name: str) -> list[str] | None:
    for segment in segments:
        fields = segment.split("|")
        if fields and fields[0] == name:
            return fields
    return None


def message_type(segments: list[str]) -> str:
    fields = _field(segments, "MSH")
    return fields[8].strip() if fields and len(fields) > 8 else ""


def control_id(segments: list[str]) -> str:
    fields = _field(segments, "MSH")
    return fields[9].strip() if fields and len(fields) > 9 else ""


def acknowledgement(segments: list[str]) -> tuple[str, str, str]:
    fields = _field(segments, "MSA")
    if not fields:
        return "", "", ""
    return (
        fields[1].strip() if len(fields) > 1 else "",
        fields[2].strip() if len(fields) > 2 else "",
        fields[3].strip() if len(fields) > 3 else "",
    )


def _clean(value: object) -> str:
    return str(value or "").strip()


def build_dsr(
    order: dict | None,
    query_segments: list[str],
    response_control_id: str,
    query_control_id: str,
    continuation_pointer: str = "",
) -> list[str]:
    """Build the single final DSR^Q03 dataset described in CY014 section 3."""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    qrd = _field(query_segments, "QRD")
    qrf = _field(query_segments, "QRF")
    query_msh = _field(query_segments, "MSH")
    receiving_application = _clean(query_msh[2]) if query_msh and len(query_msh) > 2 else ""
    receiving_facility = _clean(query_msh[3]) if query_msh and len(query_msh) > 3 else ""
    receiving_application = receiving_application or "Manufacturer"
    receiving_facility = receiving_facility or "Model"
    qrd_record = "|".join(qrd) if qrd else f"QRD|{stamp}|R|D|1|||RD||OTH|||T|"
    qrf_record = "|".join(qrf) if qrf else f"QRF|CyanVision|{stamp[:8]}000000|{stamp}|||RCT|COR|ALL||"
    status = "OK" if order else "NF"
    records = [
        # CY014's DSR example leaves the LIS sending application/facility
        # blank and addresses the response to the values which identified
        # the analyzer in its QRY MSH-3/MSH-4.
        f"MSH|^~\\&|||{receiving_application}|{receiving_facility}|{stamp}||DSR^Q03|{response_control_id}|P|2.3.1||||||ASCII|||",
        f"MSA|AA|{query_control_id}|Message accepted|||0|",
        "ERR|0|",
        f"QAK|SR|{status}|",
        qrd_record,
        qrf_record,
    ]
    if order:
        birth_date = _clean(order.get("birth_date")).replace("-", "")
        if birth_date:
            birth_date += "000000"
        records.extend([
            f"DSP|1||{_clean(order['sample_id'])}|||",
            "DSP|2||Y|||",
            f"DSP|3||{_clean(order['given_name'])}|||",
            f"DSP|4||{_clean(order['family_name'])}|||",
            f"DSP|5||{_clean(order['sex']).upper()}|||",
            f"DSP|6||{birth_date}|||",
            "DSP|7||1|||",
            f"DSP|8||{_clean(order['test_code'])}|||",
        ])
    records.append(f"DSC|{_clean(continuation_pointer)}|")
    return records


def frame(records: list[str]) -> bytes:
    message = "\r".join(records) + "\r"
    return hl7_mllp.B_VT + message.encode("utf-8") + hl7_mllp.B_FS + bytes([hl7_mllp.CR])

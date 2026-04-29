"""Tiny SNMP v2c client for UniFi WAN traffic counters.

This module intentionally implements only what this integration needs:
SNMP v2c GET requests for integer-like values such as Counter64 octets.
It avoids an extra HACS dependency for four OID reads.
"""
from __future__ import annotations

import random
import socket
from dataclasses import dataclass
from typing import Any


class SnmpError(Exception):
    """Base SNMP error."""


class SnmpTimeoutError(SnmpError):
    """SNMP request timed out."""


@dataclass(frozen=True, slots=True)
class SnmpResult:
    """Parsed SNMP value."""

    oid: str
    value: int | str | None
    tag: int


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _encode_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_integer(value: int) -> bytes:
    if value == 0:
        raw = b"\x00"
    else:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if raw[0] & 0x80:
            raw = b"\x00" + raw
    return _encode_tlv(0x02, raw)


def _encode_octet_string(value: str) -> bytes:
    return _encode_tlv(0x04, value.encode())


def _encode_null() -> bytes:
    return b"\x05\x00"


def _encode_base128(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(parts))


def _encode_oid(oid: str) -> bytes:
    parts = [int(part) for part in oid.strip(".").split(".") if part]
    if len(parts) < 2:
        raise SnmpError(f"Invalid OID: {oid}")
    encoded = bytes([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        encoded += _encode_base128(part)
    return _encode_tlv(0x06, encoded)


def _build_get_request(community: str, oid: str, request_id: int) -> bytes:
    varbind = _encode_tlv(0x30, _encode_oid(oid) + _encode_null())
    varbind_list = _encode_tlv(0x30, varbind)
    pdu = _encode_tlv(
        0xA0,
        _encode_integer(request_id)
        + _encode_integer(0)
        + _encode_integer(0)
        + varbind_list,
    )
    return _encode_tlv(0x30, _encode_integer(1) + _encode_octet_string(community) + pdu)


def _read_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise SnmpError("Unexpected end while reading BER length")
    first = data[offset]
    offset += 1
    if not first & 0x80:
        return first, offset
    count = first & 0x7F
    if count == 0 or offset + count > len(data):
        raise SnmpError("Invalid BER length")
    length = int.from_bytes(data[offset : offset + count], "big")
    return length, offset + count


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    if offset >= len(data):
        raise SnmpError("Unexpected end while reading BER tag")
    tag = data[offset]
    length, value_offset = _read_length(data, offset + 1)
    end = value_offset + length
    if end > len(data):
        raise SnmpError("BER value exceeds packet length")
    return tag, data[value_offset:end], end


def _decode_integer(value: bytes, *, signed: bool = True) -> int:
    if not value:
        return 0
    return int.from_bytes(value, "big", signed=signed)


def _decode_oid(value: bytes) -> str:
    if not value:
        return ""
    first = value[0]
    parts = [first // 40, first % 40]
    current = 0
    for byte in value[1:]:
        current = (current << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(current)
            current = 0
    return ".".join(str(part) for part in parts)


def _decode_varbind(varbind_value: bytes) -> SnmpResult:
    offset = 0
    oid_tag, oid_value, offset = _read_tlv(varbind_value, offset)
    if oid_tag != 0x06:
        raise SnmpError(f"Expected OID tag, got 0x{oid_tag:02x}")
    value_tag, value_raw, _offset = _read_tlv(varbind_value, offset)
    oid = _decode_oid(oid_value)

    if value_tag in (0x02,):
        return SnmpResult(oid=oid, value=_decode_integer(value_raw, signed=True), tag=value_tag)
    if value_tag in (0x41, 0x42, 0x43, 0x46):
        return SnmpResult(oid=oid, value=_decode_integer(value_raw, signed=False), tag=value_tag)
    if value_tag == 0x04:
        return SnmpResult(oid=oid, value=value_raw.decode(errors="replace"), tag=value_tag)
    if value_tag in (0x05, 0x80, 0x81, 0x82):
        return SnmpResult(oid=oid, value=None, tag=value_tag)
    raise SnmpError(f"Unsupported SNMP value tag 0x{value_tag:02x} for {oid}")


def _parse_response(packet: bytes, expected_request_id: int) -> SnmpResult:
    tag, message, end = _read_tlv(packet, 0)
    if tag != 0x30 or end != len(packet):
        raise SnmpError("Invalid SNMP message")

    offset = 0
    _version_tag, _version_value, offset = _read_tlv(message, offset)
    _community_tag, _community_value, offset = _read_tlv(message, offset)
    pdu_tag, pdu_value, offset = _read_tlv(message, offset)
    if pdu_tag != 0xA2:
        raise SnmpError(f"Expected GetResponse PDU, got 0x{pdu_tag:02x}")

    pdu_offset = 0
    req_tag, req_value, pdu_offset = _read_tlv(pdu_value, pdu_offset)
    if req_tag != 0x02:
        raise SnmpError("Missing request id in response")
    request_id = _decode_integer(req_value, signed=True)
    if request_id != expected_request_id:
        raise SnmpError("SNMP response request id mismatch")

    err_tag, err_value, pdu_offset = _read_tlv(pdu_value, pdu_offset)
    _idx_tag, _idx_value, pdu_offset = _read_tlv(pdu_value, pdu_offset)
    if err_tag != 0x02:
        raise SnmpError("Missing error status in response")
    error_status = _decode_integer(err_value, signed=True)
    if error_status:
        raise SnmpError(f"SNMP agent returned error status {error_status}")

    vbl_tag, vbl_value, _pdu_offset = _read_tlv(pdu_value, pdu_offset)
    if vbl_tag != 0x30:
        raise SnmpError("Missing varbind list in response")
    vb_tag, vb_value, _vb_offset = _read_tlv(vbl_value, 0)
    if vb_tag != 0x30:
        raise SnmpError("Missing varbind in response")
    return _decode_varbind(vb_value)


def snmp_get(host: str, port: int, community: str, oid: str, timeout: float = 4.0) -> SnmpResult:
    """Perform one SNMP v2c GET request synchronously."""
    request_id = random.randint(1, 2_147_483_647)
    packet = _build_get_request(community, oid, request_id)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (host, port))
            response, _addr = sock.recvfrom(65535)
        except socket.timeout as err:
            raise SnmpTimeoutError(f"SNMP timeout for {host}:{port} {oid}") from err
        except OSError as err:
            raise SnmpError(f"SNMP socket error for {host}:{port} {oid}: {err}") from err

    return _parse_response(response, request_id)


def normalize_oid_map(values: dict[str, Any]) -> dict[str, str]:
    """Return non-empty OID values."""
    return {key: str(value).strip() for key, value in values.items() if str(value or "").strip()}

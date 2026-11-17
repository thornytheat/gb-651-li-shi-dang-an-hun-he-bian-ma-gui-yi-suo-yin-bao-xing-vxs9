"""Field preservation and receiving-batch parsing.

The receiving side of M3 is explicit about three things that must survive a
template upgrade:

* the original columns (names and order) are stored verbatim;
* the physical source line of every record is kept;
* identifiers stay strings, so leading zeros never disappear.

The null convention is *not* hard-coded here: it comes from the template
snapshot bound to the batch.  That is what lets an old batch return its old
receipt after a new template has been published.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from app.errors import DomainError

SAMPLE = {"values": ["000123", "9007199254740993", "N/A", " 07 "]}
NULLS = {"", "-", "N/A", "NULL", "未知"}

DECODERS = ("utf-8-sig", "gb18030")


def preserve(value):
    if value is None:
        return {"state": "null", "value": None}
    raw = str(value)
    stripped = raw.strip()
    if stripped in NULLS:
        return {"state": "sentinel", "value": None, "raw": raw}
    return {"state": "value", "value": stripped, "raw": raw}


def decimal_text(value):
    try:
        return format(Decimal(str(value).replace(",", "")), "f")
    except InvalidOperation:
        raise ValueError("INVALID_VALUE")


def run(payload):
    values = payload.get("values", SAMPLE["values"])
    out = [preserve(v) for v in values]
    return {
        "status": "READY",
        "input_count": len(out),
        "valid_count": sum(x["state"] == "value" for x in out),
        "quarantine_count": 0,
        "values": [x["value"] for x in out],
        "reason": "OK",
    }


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------
def decode_content(content: bytes) -> dict:
    """Decode a received file completely; never emit replacement characters."""
    if content is None or content == b"":
        raise DomainError("HEADER_MISSING", "接收文件为空")
    errors = []
    for name in DECODERS:
        try:
            text = content.decode(name, "strict")
        except UnicodeDecodeError as exc:
            errors.append(f"{name}:{exc.reason}")
            continue
        if "�" in text:
            errors.append(f"{name}:replacement-character")
            continue
        return {"encoding": name, "text": text}
    raise DomainError("DECODE_FAILED", "文件无法按 UTF-8 或 GB18030 完整解码：" + ",".join(errors))


# ---------------------------------------------------------------------------
# header helpers
# ---------------------------------------------------------------------------
def _header_key(cell: str) -> str:
    return cell.lstrip("﻿").strip()


def extract_header(text: str) -> list[str]:
    """Return the first non-blank record, used to bind/validate a template."""
    reader = csv.reader(io.StringIO(text, newline=""))
    for row in reader:
        if row and any(cell.strip() for cell in row):
            return [cell.lstrip("﻿") for cell in row]
        # A leading blank line is still counted as an empty record; keep
        # scanning until the first document-like row shows up.
    raise DomainError("HEADER_MISSING", "文件缺少表头")


def validate_header(template: dict, header: list[str]) -> None:
    """Check the file header against the bound template.

    The match is by prefix: a file may omit only *optional* trailing columns
    (e.g. an export without 题名) but never reorder, rename or add columns.
    Prefix matching keeps minimal files receivable without letting a file with
    a different layout slip through.
    """
    columns = list(template.get("columns") or [])
    expected = [str(column["name"]) for column in columns]
    received = [_header_key(cell) for cell in header]
    ok = len(received) <= len(expected) and received == expected[: len(received)]
    if ok:
        omitted = columns[len(received):]
        ok = all(not column.get("required") for column in omitted)
    if not ok:
        raise DomainError(
            "TEMPLATE_MISMATCH",
            "文件列与模板 "
            + str(template.get("version"))
            + " 不符；期望 "
            + ",".join(expected)
            + "；收到 "
            + ",".join(received),
        )
    if len(set(received)) != len(received):
        raise DomainError("TEMPLATE_MISMATCH", "文件表头存在重复列名")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def _check_truncated(text: str) -> bool:
    """True when the file ends inside a quoted field / escape."""
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        for _ in reader:
            pass
    except csv.Error:
        return True
    return False


def _read_records(text: str) -> list[tuple[int, list[str]]]:
    """Return ``(starting physical line, cells)`` for each CSV record."""
    records: list[tuple[int, list[str]]] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    previous_line = 0
    for row in reader:
        start = previous_line + 1
        previous_line = reader.line_num
        records.append((start, row))
    return records


def _empty_record(seq: int, line_no: int, row: list[str]) -> dict:
    return {
        "seq": seq,
        "source_line": line_no,
        "state": "empty",
        "reason_code": None,
        "raw": list(row),
        "fields": {},
        "values": {},
    }


def parse_text(text: str, template: dict) -> dict:
    """Parse file text against a bound template snapshot.

    Returns the raw header, records in file order and the counters that prove
    ``input = valid + quarantine + empty``.  Repeated header lines are the
    旧批次 convenience convention and are skipped, not turned into rows.
    """
    records = _read_records(text)
    truncated = _check_truncated(text)

    header = None
    header_line = None
    parsed: list[dict] = []
    repeated_header = 0
    seq = 0

    for line_no, row in records:
        if not row or not any(cell.strip() for cell in row):
            seq += 1
            parsed.append(_empty_record(seq, line_no, row))
            continue
        if header is None:
            header = [cell.lstrip("﻿") for cell in row]
            header_line = line_no
            continue
        if [_header_key(cell) for cell in row] == [_header_key(cell) for cell in header]:
            repeated_header += 1
            continue
        seq += 1
        parsed.append(build_record(seq, line_no, row, header, template))

    if header is None:
        raise DomainError("HEADER_MISSING", "文件缺少表头")
    validate_header(template, header)

    if truncated:
        # An unterminated quoted tail consumes the remainder of the file, so
        # the last record read is the one we must quarantine.  Earlier rows
        # stay valid evidence.  A row that was already malformed keeps its
        # more specific reason code.
        tail = next((record for record in reversed(parsed) if record["state"] != "empty"), None)
        if tail is not None and tail["state"] == "valid":
            tail["state"] = "quarantine"
            tail["reason_code"] = "TRUNCATED"

    counts = {"valid": 0, "quarantine": 0, "empty": 0}
    for record in parsed:
        counts[record["state"]] += 1

    return {
        "header": header,
        "header_line": header_line,
        "encoding": None,
        "records": parsed,
        "counts": counts,
        "input_count": len(parsed),
        "repeated_header_count": repeated_header,
        "truncated": truncated,
    }


def build_record(seq: int, line_no: int, row: list[str], header: list[str], template: dict) -> dict:
    fields = {}
    for index, name in enumerate(header):
        fields[name] = row[index] if index < len(row) else ""

    record = {
        "seq": seq,
        "source_line": line_no,
        "state": "valid",
        "reason_code": None,
        "raw": list(row),
        "fields": fields,
        "values": {},
    }

    if len(row) != len(header):
        record["state"] = "quarantine"
        record["reason_code"] = "BAD_COLUMNS"
        return record

    null_tokens = list(template.get("null_tokens") or [""])
    values: dict[str, str | None] = {}
    for column in template.get("columns") or []:
        name = str(column["name"])
        role = column.get("role", "other")
        raw = fields.get(name, "")
        stripped = raw.strip()
        is_null = stripped in null_tokens
        if column.get("literal_na") and stripped.upper() == "N/A":
            # This column opts out of the "N/A means empty" convention.
            is_null = False
        if is_null:
            values[role] = None
            if column.get("required"):
                record["state"] = "quarantine"
                record["reason_code"] = record["reason_code"] or "EMPTY_VALUE"
        else:
            # Identifiers are strings: never numerically interpret them, so
            # leading zeros and >2^53 serials are preserved verbatim.
            values[role] = stripped

    record["values"] = values
    return record


def parse_file(content: bytes, template: dict) -> dict:
    decoded = decode_content(content)
    parsed = parse_text(decoded["text"], template)
    parsed["encoding"] = decoded["encoding"]
    return parsed


def identity_keys(template: dict, record: dict) -> dict:
    """Return the identity of a valid record under the bound template.

    Old batches expose a merged 档号; new batches expose 全宗号 + 目录号 +
    档号.  Both are reported without coercing anything to a number.
    """
    value = record.get("values") or {}
    if value.get("archive_id_merged") is not None:
        return {"archive_id": value["archive_id_merged"]}
    return {
        "fonds_no": value.get("fonds_no"),
        "catalog_no": value.get("catalog_no"),
        "archive_id": value.get("archive_id"),
    }

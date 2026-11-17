"""M3 receiving workflows: template releases and batch reception."""
from __future__ import annotations

import base64
import binascii

from app.errors import DomainError
from app.manifest import canonical_digest, content_digest

from . import core, templates


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def text_to_bytes(payload: dict) -> tuple[bytes, str | None]:
    if "hex" in payload and payload["hex"] is not None:
        try:
            return bytes.fromhex(str(payload["hex"])), payload.get("filename")
        except ValueError as exc:
            raise DomainError("INVALID_VALUE", "hex 内容无法解析") from exc
    if "base64" in payload and payload["base64"] is not None:
        try:
            return base64.b64decode(str(payload["base64"]), validate=True), payload.get("filename")
        except (binascii.Error, ValueError) as exc:
            raise DomainError("INVALID_VALUE", "base64 内容无法解析") from exc
    if "text" in payload and payload["text"] is not None:
        return str(payload["text"]).encode("utf-8"), payload.get("filename")
    if "file_text" in payload and payload["file_text"] is not None:
        return str(payload["file_text"]).encode("utf-8"), payload.get("filename")
    if "content_base64" in payload and payload["content_base64"] is not None:
        try:
            return base64.b64decode(str(payload["content_base64"]), validate=True), payload.get("filename")
        except (binascii.Error, ValueError) as exc:
            raise DomainError("INVALID_VALUE", "base64 内容无法解析") from exc
    if payload.get("file") is not None and isinstance(payload["file"], (bytes, bytearray)):
        return bytes(payload["file"]), payload.get("filename")
    if "data" in payload and payload["data"] is not None:
        return str(payload["data"]).encode("utf-8"), payload.get("filename")
    if "content" in payload and payload["content"] is not None:
        value = payload["content"]
        if isinstance(value, bytes):
            return value, payload.get("filename")
        return str(value).encode("utf-8"), payload.get("filename")
    raise DomainError("HEADER_MISSING", "缺少接收内容（text/content/hex/base64）")


def _int_param(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainError("INVALID_VALUE", f"{name} 必须是整数") from exc
    if number < 0:
        raise DomainError("INVALID_VALUE", f"{name} 不能为负数")
    return number


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def publish_template(store, payload: dict) -> dict:
    definition = templates.normalize_definition(payload)
    expected_revision = _int_param(
        payload.get("expected_revision", payload.get("base_revision")), "expected_revision"
    )
    requested_revision = _int_param(payload.get("revision"), "revision") if "revision" in payload else None
    expected_version = payload.get("expected_version", payload.get("base_version"))
    if expected_revision is None and requested_revision is None and not expected_version:
        # Implicit optimistic lock: freeze the revision observed at the start
        # of this request.  A publication that loses the race therefore fails
        # with STALE_VERSION instead of silently overwriting the release the
        # other request just made.
        expected_revision = store.active_state()["revision"]
    published = store.publish_template(
        definition,
        expected_revision=expected_revision,
        expected_version=expected_version,
        requested_revision=requested_revision,
    )
    return {
        "status": "PUBLISHED",
        "template": templates.response_view(published),
        "active_version": published["version"],
        "revision": int(published["revision"]),
    }


def get_template(store, version: str) -> dict:
    definition = store.get_template(version)
    if definition is None:
        raise DomainError("TEMPLATE_MISSING", f"模板版本不存在：{version}")
    state = store.active_state()
    view = templates.response_view(definition)
    view["active"] = definition["version"] == state["active_version"]
    return view


def list_templates(store) -> dict:
    state = store.active_state()
    return {
        "active_version": state["active_version"],
        "revision": state["revision"],
        "templates": [templates.response_view(item) for item in store.list_templates()],
    }


# ---------------------------------------------------------------------------
# receiving
# ---------------------------------------------------------------------------
def build_receipt(batch: dict, records: list[dict]) -> dict:
    ordered = sorted(records, key=lambda record: int(record["seq"]))
    valid = sum(1 for record in ordered if record["state"] == "valid")
    quarantine = sum(1 for record in ordered if record["state"] == "quarantine")
    empty = sum(1 for record in ordered if record["state"] == "empty")
    total = valid + quarantine + empty
    template = batch["template"]
    header = list(batch["header"])

    # One row per physical record.  ``source_line`` is the original file line,
    # ``raw`` the untouched cells, and ``fields`` preserves the receiving
    # column names/order.  ``line_no`` is kept as an alias for reviewers who
    # read the receipt by hand.
    records_view = [
        {
            "seq": int(record["seq"]),
            "source_line": int(record["source_line"]),
            "line_no": int(record["source_line"]),
            "state": record["state"],
            "reason_code": record.get("reason_code"),
            "raw": list(record.get("raw") or []),
            "fields": record.get("fields") or {},
            "values": record.get("values") or {},
            **(
                {"identity": core.identity_keys(template, record)}
                if record["state"] == "valid"
                else {}
            ),
        }
        for record in ordered
    ]

    receipt = {
        "receipt_id": "rcpt-" + canonical_digest({"batch_no": batch["batch_no"], "digest": batch["content_digest"]})[:16],
        "batch_no": batch["batch_no"],
        "template_version": template["version"],
        "template_revision": int(template["revision"]),
        "template": {
            "version": template["version"],
            "revision": int(template["revision"]),
            "columns": template.get("columns"),
            "null_tokens": template.get("null_tokens"),
            "keep_leading_zeros": bool(template.get("keep_leading_zeros", True)),
            "allow_literal_na_archive_id": bool(template.get("allow_literal_na_archive_id", False)),
        },
        "digest": batch["content_digest"],
        "encoding": batch.get("encoding"),
        "header": header,
        "columns": header,
        "input_count": total,
        "valid_count": valid,
        "quarantine_count": quarantine,
        "empty_count": empty,
        "valid_plus_quarantine": valid + quarantine,
        "accounted_count": total,
        "balanced": valid + quarantine + empty == total,
        "records": records_view,
        "rows": records_view,
        "quarantine": [
            {
                "seq": int(record["seq"]),
                "source_line": int(record["source_line"]),
                "line_no": int(record["source_line"]),
                "reason_code": record.get("reason_code"),
                "raw": list(record.get("raw") or []),
            }
            for record in ordered
            if record["state"] == "quarantine"
        ],
        "valid_identities": [
            core.identity_keys(template, record) for record in ordered if record["state"] == "valid"
        ],
        "repeated_header_count": 0,
        "reason_code": "OK" if quarantine == 0 else "QUARANTINE_PRESENT",
    }
    return receipt


def _decode_for(batch: dict, content: bytes) -> tuple[dict, dict]:
    decoded = core.decode_content(content)
    parsed = core.parse_text(decoded["text"], batch["template"])
    parsed["encoding"] = decoded["encoding"]
    return decoded, parsed


def _response_from_batch(store, batch: dict, *, created: bool, switched_template: bool | None = None) -> dict:
    summary = store.batch_summary(batch)
    receipt = batch.get("receipt")
    response = {
        "status": batch["status"] if batch["status"] != "RECEIVED" else "RECEIVED",
        "batch": summary,
        "receipt": receipt,
        "created": created,
        "duplicate": not created,
        "template_version": batch["template_version"],
        "template_revision": int(batch["template_revision"]),
        "template_switched": (
            switched_template
            if switched_template is not None
            else batch["template_version"] != store.active_state()["active_version"]
        ),
    }
    if batch["status"] == "RECEIVING":
        response["resumable"] = True
        response["next_seq"] = int(batch["next_seq"])
        response["reason"] = "RECEIVING"
    else:
        response["reason"] = "OK"
    return response


def matching_versions(store, header: list[str]) -> list[str]:
    """Versions whose column layout accepts this header (for diagnostics)."""
    out = []
    for item in store.list_templates():
        try:
            core.validate_header(item, header)
        except DomainError:
            continue
        out.append(item["version"])
    return out


def _hint_mismatch(store, header: list[str], selected_version: str | None) -> None:
    active = store.active_state()["active_version"]
    target = selected_version or active
    template = store.get_template(target)
    if template is None:
        raise DomainError("TEMPLATE_MISSING", f"模板版本不存在：{target}")
    try:
        core.validate_header(template, header)
        return
    except DomainError as exc:
        matches = matching_versions(store, header)
        hint = ("；匹配的模板版本：" + ",".join(matches)) if matches else "；没有已发布模板匹配该表头"
        raise DomainError(exc.code, exc.message + hint) from exc


def receive_bytes(
    store,
    *,
    content: bytes,
    batch_no: str | None = None,
    filename: str | None = None,
    template_version: str | None = None,
    max_records: int | None = None,
) -> dict:
    """Receive (or resume) one batch from raw file bytes.

    The batch number is the idempotency key.  Re-submitting the same batch
    number with the same bytes returns the stored receipt and never appends a
    row; re-submitting it with different bytes is a conflict.
    """
    if content is None or content == b"":
        raise DomainError("HEADER_MISSING", "接收文件为空")
    digest = content_digest(content)
    template_version = template_version or None
    if not batch_no:
        batch_no = "B" + digest[:12]
    batch_no = str(batch_no).strip()
    if not batch_no:
        raise DomainError("INVALID_VALUE", "批号不能为空")

    existing = store.get_batch(batch_no)
    if existing is not None:
        if template_version and str(template_version).strip() != existing["template_version"]:
            raise DomainError(
                "TEMPLATE_MISMATCH",
                f"批号 {batch_no} 已绑定模板 {existing['template_version']}，不能改为 {template_version}",
                status_code=409,
            )
        if existing["content_digest"] != digest:
            raise DomainError(
                "DUPLICATE_CONFLICT",
                f"批号 {batch_no} 已接收不同内容，拒绝覆盖",
                status_code=409,
            )
        if existing["status"] == "RECEIVED" and existing.get("receipt"):
            return _response_from_batch(store, existing, created=False)
        return _advance(store, existing, content, max_records=max_records, created=False)

    decoded = core.decode_content(content)
    header = core.extract_header(decoded["text"])

    selected_version = str(template_version).strip() if template_version else None
    if selected_version is not None and store.get_template(selected_version) is None:
        raise DomainError("TEMPLATE_MISSING", f"模板版本不存在：{selected_version}")
    # Diagnostic pre-check only; the authoritative check happens inside
    # ``begin_batch`` while the template is bound.
    _hint_mismatch(store, header, selected_version)

    # ``begin_batch`` resolves the active template inside its write
    # transaction.  A concurrent publication therefore either happens before
    # (and is bound in full) or after (and is not bound at all); a batch can
    # never be half-bound to two conventions.
    try:
        batch, created = store.begin_batch(
            batch_no=batch_no,
            digest=digest,
            content=content,
            filename=filename,
            header=header,
            template_version=selected_version,
            validate_header=core.validate_header,
        )
    except DomainError as exc:
        if exc.code in {"TEMPLATE_MISMATCH", "TEMPLATE_MISSING"}:
            # The active template may have switched between the diagnostic
            # check and the bind; re-run the hint against current state.
            _hint_mismatch(store, header, selected_version)
        raise
    return _advance(store, batch, content, max_records=max_records, created=created)


def _advance(store, batch: dict, content: bytes, *, max_records: int | None, created: bool, switched_template: bool | None = None) -> dict:
    decoded, parsed = _decode_for(batch, content)
    records = parsed["records"]
    total = len(records)
    next_seq = int(batch["next_seq"])
    pending = [record for record in records if int(record["seq"]) >= next_seq]
    remaining = len(pending) if max_records is None else min(len(pending), max_records)

    # Commit in bounded slices.  If the process is killed halfway through a
    # large file, every committed slice has a durable ``next_seq`` and the
    # next process can resume from there without duplicating rows.
    chunk_size = 200
    while remaining > 0:
        take = min(chunk_size, remaining)
        slice_records = pending[:take]
        updated = store.append_progress(batch["batch_no"], slice_records, expected_count=total)
        if updated is None:
            break
        pending = pending[take:]
        remaining -= take

    fresh = store.get_batch(batch["batch_no"])
    if fresh is None:
        raise DomainError("INVALID_VALUE", "批次不存在")
    if int(fresh["next_seq"]) - 1 >= total:
        receipt = store.finalize_batch(
            fresh["batch_no"],
            lambda current, stored: _receipt_with_meta(current, stored, decoded["encoding"], parsed["repeated_header_count"]),
        )
        final = store.get_batch(fresh["batch_no"])
        return _response_from_batch(store, final, created=created, switched_template=switched_template)
    return _response_from_batch(store, fresh, created=created, switched_template=switched_template)


def _receipt_with_meta(batch: dict, records: list[dict], encoding: str, repeated_header_count: int) -> dict:
    receipt = build_receipt(batch, records)
    receipt["encoding"] = encoding
    receipt["repeated_header_count"] = repeated_header_count
    return receipt


def resume_batch(store, batch_no: str, *, max_records: int | None = None) -> dict:
    batch = store.get_batch(batch_no)
    if batch is None:
        raise DomainError("INVALID_VALUE", f"批次不存在：{batch_no}")
    if batch["status"] == "RECEIVED" and batch.get("receipt"):
        return _response_from_batch(store, batch, created=False)
    content = store.get_batch_content(batch_no)
    if content is None:
        raise DomainError("INVALID_VALUE", f"批次 {batch_no} 缺少可恢复内容")
    return _advance(store, batch, content, max_records=max_records, created=False)


# ---------------------------------------------------------------------------
# read APIs
# ---------------------------------------------------------------------------
def batch_detail(store, batch_no: str) -> dict:
    batch = store.get_batch(batch_no)
    if batch is None:
        raise DomainError("INVALID_VALUE", f"批次不存在：{batch_no}")
    response = _response_from_batch(store, batch, created=False)
    response["batch"]["header"] = list(batch["header"])
    response["batch"]["template"] = {
        "version": batch["template"]["version"],
        "revision": int(batch["template"]["revision"]),
        "columns": batch["template"].get("columns"),
        "null_tokens": batch["template"].get("null_tokens"),
    }
    return response


def batch_receipt(store, batch_no: str) -> dict:
    batch = store.get_batch(batch_no)
    if batch is None:
        raise DomainError("INVALID_VALUE", f"批次不存在：{batch_no}")
    if batch["status"] != "RECEIVED" or not batch.get("receipt"):
        return {
            "status": "RECEIVING",
            "batch_no": batch_no,
            "receipt": None,
            "resumable": True,
            "next_seq": int(batch["next_seq"]),
        }
    return {"status": "RECEIVED", "batch_no": batch_no, "receipt": batch["receipt"]}


def list_batches(store) -> dict:
    return {"batches": store.list_batches()}


# ---------------------------------------------------------------------------
# legacy sample endpoint
# ---------------------------------------------------------------------------
def execute(payload: dict | None = None) -> dict:
    return core.run(payload or core.SAMPLE)

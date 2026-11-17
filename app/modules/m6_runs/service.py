from app.errors import DomainError

from . import core


def execute(payload: dict | None = None) -> dict:
    return core.run(payload or core.SAMPLE)


def batch_evidence(store, batch_no: str | None = None) -> dict:
    """Export evidence for one stored receipt (or the most recent batch)."""
    if batch_no is None:
        batches = store.list_batches()
        received = [batch for batch in batches if batch["status"] == "RECEIVED"]
        if not received:
            raise DomainError("INVALID_VALUE", "尚无已接收批次可导出")
        batch_no = received[-1]["batch_no"]
    batch = store.get_batch(batch_no)
    if batch is None:
        raise DomainError("INVALID_VALUE", f"批次不存在：{batch_no}")
    if batch["status"] != "RECEIVED" or not batch.get("receipt"):
        raise DomainError("INVALID_VALUE", f"批次 {batch_no} 尚未接收完成，无法导出")
    rows = core.receipt_rows(batch["receipt"])
    return {
        "status": "READY",
        "batch_no": batch_no,
        "template_version": batch["receipt"].get("template_version"),
        "input_count": batch["receipt"].get("input_count"),
        "valid_count": batch["receipt"].get("valid_count"),
        "quarantine_count": batch["receipt"].get("quarantine_count"),
        "empty_count": batch["receipt"].get("empty_count"),
        "csv": core.csv_text(rows),
        "rows": rows,
    }

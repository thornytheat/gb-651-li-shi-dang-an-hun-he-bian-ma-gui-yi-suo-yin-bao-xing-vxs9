from app.errors import DomainError

from . import core


def execute(payload: dict | None = None) -> dict:
    return core.run(payload or core.SAMPLE)


def reconcile_batch(store, batch_no: str, payload: dict | None = None) -> dict:
    """Reconcile a stored receipt against an index.

    Without an explicit ``index_ids`` the batch is checked against itself,
    which still verifies that the stored valid identities and the receipt
    counters agree (quarantined rows included).
    """
    from app.modules.m3_fields.service import batch_receipt

    payload = payload or {}
    stored = batch_receipt(store, batch_no)
    if stored.get("receipt") is None:
        raise DomainError("INVALID_VALUE", f"批次 {batch_no} 尚未接收完成，无法对账")
    receipt = stored["receipt"]
    index_ids = payload.get("index_ids")
    if index_ids is None:
        index_ids = [
            record["identity"]["archive_id"]
            for record in receipt.get("records", [])
            if record.get("state") == "valid" and record.get("identity")
        ]
    out = core.reconcile_receipt(receipt, [str(value) for value in index_ids])
    return {"status": "READY", "input_count": out["index_count"], **out}

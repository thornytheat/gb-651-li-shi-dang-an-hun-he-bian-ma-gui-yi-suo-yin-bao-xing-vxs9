SAMPLE = {
    "index_ids": ["000123", "9007199254740993", "000404"],
    "normalized_ids": ["000123", "9007199254740993", "000777"],
    "quarantine": 1,
    "empty": 0,
}


def reconcile(index_ids, normalized_ids, quarantine=0, empty=0):
    """Compare an index against normalized identifiers.

    Identifiers are compared as strings, so a leading zero is significant and
    ``9007199254740993`` is not rounded by any numeric conversion.
    """
    index_ids = [str(value) for value in index_ids]
    normalized_ids = [str(value) for value in normalized_ids]
    left = set(index_ids)
    right = set(normalized_ids)
    missing = sorted(left - right)
    orphan = sorted(right - left)
    input_count = len(index_ids)
    accounted = len(normalized_ids) + int(quarantine) + int(empty)
    return {
        "index_count": input_count,
        "output_count": len(normalized_ids),
        "missing": missing,
        "orphan": orphan,
        "quarantine_count": int(quarantine),
        "empty_count": int(empty),
        "valid_count": len(normalized_ids),
        "accounted_count": accounted,
        "balanced": accounted == input_count,
        "delta": accounted - input_count,
    }


def run(payload):
    data = SAMPLE | payload
    out = reconcile(
        data["index_ids"],
        data["normalized_ids"],
        data.get("quarantine", 0),
        data.get("empty", 0),
    )
    return {
        "status": "READY",
        "input_count": out["index_count"],
        "valid_count": out["output_count"],
        **out,
        "reason": "DIFF" if out["missing"] or out["orphan"] or not out["balanced"] else "OK",
    }


def reconcile_receipt(receipt: dict, index_ids: list[str]) -> dict:
    """Reconcile a stored batch receipt against an index.

    A receipt is internally consistent when ``valid + quarantine + empty``
    equals the input count.  Quarantined rows are isolated, not dropped, so the
    valid/quarantine sum is still checkable against the index side.
    """
    valid_ids = [
        record["identity"].get("archive_id")
        for record in receipt.get("records", [])
        if record.get("state") == "valid" and record.get("identity")
    ]
    valid_ids = [value for value in valid_ids if value is not None]
    out = reconcile(
        index_ids,
        valid_ids,
        receipt.get("quarantine_count", 0),
        receipt.get("empty_count", 0),
    )
    # In batch mode the index is not the batch input: the batch accounting is
    # ``valid + quarantine + empty == batch input``, while ``missing`` and
    # ``orphan`` describe the index difference.  Keep the two claims distinct
    # so neither can hide the other.
    receipt_total = int(receipt.get("input_count", 0))
    batch_accounted = (
        int(receipt.get("valid_count", 0))
        + int(receipt.get("quarantine_count", 0))
        + int(receipt.get("empty_count", 0))
    )
    out["receipt_balanced"] = batch_accounted == receipt_total and bool(receipt.get("balanced", False))
    out["batch_input_count"] = receipt_total
    out["batch_accounted_count"] = batch_accounted
    out["batch_balanced"] = out["receipt_balanced"]
    out["index_delta"] = len(valid_ids) - len(index_ids)
    # ``index_count`` / ``accounted_count`` would mix index and file counts;
    # report the batch-side totals explicitly instead.
    out["index_count"] = len(index_ids)
    out["output_count"] = len(valid_ids)
    out["accounted_count"] = batch_accounted
    out["delta"] = batch_accounted - receipt_total
    out["valid_plus_quarantine"] = int(receipt.get("valid_count", 0)) + int(receipt.get("quarantine_count", 0))
    out["batch_no"] = receipt.get("batch_no")
    out["template_version"] = receipt.get("template_version")
    out["balanced"] = out["receipt_balanced"]
    out["reason"] = "OK" if not out["missing"] and not out["orphan"] and out["receipt_balanced"] else "DIFF"
    return out

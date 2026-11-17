import csv
import io

from app.manifest import canonical_digest, run_id

SAMPLE = {
    "store": None,
    "rows": [
        {"source_line": 2, "archive_id": "000123", "reason": "OK"},
        {"source_line": 5, "archive_id": "BAD", "reason": "BAD_COLUMNS"},
    ],
}


def csv_text(rows):
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=["source_line", "archive_id", "reason"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def receipt_rows(receipt: dict) -> list[dict]:
    """Flatten a receiving receipt into the evidence table.

    Both valid and quarantined rows are exported so the sum can be checked;
    identifiers stay strings, preserving leading zeros.
    """
    rows = []
    for record in receipt.get("records", []):
        identity = record.get("identity") or {}
        archive_id = identity.get("archive_id")
        if archive_id is None:
            archive_id = (record.get("values") or {}).get("archive_id_merged")
        if archive_id is None:
            archive_id = (record.get("fields") or {}).get("档号", "")
        rows.append(
            {
                "source_line": record.get("source_line"),
                "archive_id": archive_id,
                "reason": "OK" if record.get("state") == "valid" else record.get("reason_code") or "QUARANTINED",
            }
        )
    return rows


def run(payload):
    rows = payload.get("rows", SAMPLE["rows"])
    digest = canonical_digest(rows)
    rid = run_id(rows)
    store = payload.get("store")
    result = {
        "input_count": len(rows),
        "valid_count": sum(r["reason"] == "OK" for r in rows),
        "quarantine_count": sum(r["reason"] != "OK" for r in rows),
    }
    if store:
        rid = store.record(digest, rid, result)["run_id"]
    return {
        "status": "READY",
        "run_id": rid,
        "digest": digest,
        "csv": csv_text(rows),
        **result,
        "reason": "OK",
    }

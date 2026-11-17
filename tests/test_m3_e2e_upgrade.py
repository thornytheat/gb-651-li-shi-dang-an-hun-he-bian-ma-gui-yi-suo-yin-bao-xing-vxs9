"""End-to-end: old batches after a template upgrade, new batches before/after."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = (ROOT / "sample_data" / "archive_clean.csv").read_bytes()
DIRTY = (ROOT / "sample_data" / "archive_dirty.csv").read_bytes()
SPLIT = (ROOT / "sample_data" / "archive_split.csv").read_bytes()


def upload(client, batch_no, content, filename, template_version=None, max_records=None):
    data = {"batch_no": batch_no}
    if template_version:
        data["template_version"] = template_version
    if max_records is not None:
        data["max_records"] = str(max_records)
    return client.post(
        "/api/fields/receive/upload",
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


def test_upgrade_keeps_old_and_accepts_new(client):
    # 1. Old sample batches arrive under the seeded merged-column template.
    clean = upload(client, "OLD-CLEAN", CLEAN, "archive_clean.csv").json()
    dirty = upload(client, "OLD-DIRTY", DIRTY, "archive_dirty.csv").json()
    assert clean["template_version"] == dirty["template_version"] == "2026.11"
    assert dirty["receipt"]["header"] == ["档号", "盒号", "题名"]
    quarantine = dirty["receipt"]["quarantine"]
    assert len(quarantine) == 1
    assert quarantine[0]["source_line"] == 5
    assert quarantine[0]["reason_code"] == "BAD_COLUMNS"
    assert quarantine[0]["raw"] == ["BAD", "09"]

    # 2. The curator publishes the new split-column template.
    published = client.post("/api/fields/templates", json={"version": "2026.12", "preset": "split"})
    assert published.status_code == 200

    # 3. A new batch with the new layout is accepted under the new template.
    new = upload(client, "NEW-SPLIT", SPLIT, "archive_split.csv").json()
    assert new["template_version"] == "2026.12"
    assert new["receipt"]["header"] == ["全宗号", "目录号", "档号", "盒号", "题名"]
    first = new["receipt"]["records"][0]
    assert first["identity"] == {"fonds_no": "A01", "catalog_no": "0001", "archive_id": "000123"}
    third = new["receipt"]["records"][2]
    assert third["identity"]["archive_id"] == "N/A"
    assert new["receipt"]["valid_count"] == 3

    # 4. Retrying the old batches returns the original receipts unchanged.
    assert upload(client, "OLD-CLEAN", CLEAN, "archive_clean.csv").json()["receipt"] == clean["receipt"]
    retry = upload(client, "OLD-DIRTY", DIRTY, "archive_dirty.csv").json()
    assert retry["receipt"] == dirty["receipt"]
    assert retry["duplicate"] is True
    assert retry["template_switched"] is True

    # 5. Nothing was double-recorded and every batch still balances.
    batches = client.get("/api/fields/batches").json()["batches"]
    assert [b["batch_no"] for b in batches] == ["OLD-CLEAN", "OLD-DIRTY", "NEW-SPLIT"]
    assert all(b["balanced"] for b in batches)
    assert all(
        b["valid_count"] + b["quarantine_count"] + b["empty_count"] == b["input_count"]
        for b in batches
    )

    # 6. Evidence export reflects the bound convention per batch.
    csv_old = client.get("/api/export/evidence.csv?batch_no=OLD-DIRTY").text
    assert "000123" in csv_old and "9007199254740993" in csv_old and "BAD_COLUMNS" in csv_old
    csv_new = client.get("/api/export/evidence.csv?batch_no=NEW-SPLIT").text
    assert "N/A" in csv_new and "000123" in csv_new


def test_interrupted_upload_resumes_after_restart(client):
    many = (
        "全宗号,目录号,档号,盒号,题名\n"
        + "".join(f"A01,{i:04d},{i:06d},{i:02d},题名{i}\n" for i in range(1, 41))
    ).encode()
    client.post("/api/fields/templates", json={"version": "2026.12", "preset": "split"})
    partial = upload(client, "RESUME-E2E", many, "big.csv", max_records=15).json()
    assert partial["status"] == "RECEIVING"
    assert partial["receipt"] is None
    # Same request again resumes rather than starting over.
    again = upload(client, "RESUME-E2E", many, "big.csv", max_records=15).json()
    assert again["batch"]["valid_count"] == 30
    final = client.post("/api/fields/batches/RESUME-E2E/resume", json={}).json()
    assert final["status"] == "RECEIVED"
    assert final["batch"]["valid_count"] == 40
    assert len(final["receipt"]["records"]) == 40
    assert [r["source_line"] for r in final["receipt"]["records"]] == list(range(2, 42))

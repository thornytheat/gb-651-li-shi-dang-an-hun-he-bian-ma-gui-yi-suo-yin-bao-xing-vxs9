import copy
import threading

import pytest

from app.errors import DomainError
from app.modules.m3_fields import core
from app.store import Store

LEGACY_FILE = (
    "﻿档号,盒号,题名\n"
    "000123,07,年度 汇编\n"
    "档号,盒号,题名\n"
    "9007199254740993,08,会议\n"
    "BAD,09\n"
).encode("utf-8")

SPLIT_FILE = (
    "全宗号,目录号,档号,盒号,题名\n"
    "A01,0001,000123,07,年度 汇编\n"
    "A01,0001,9007199254740993,08,会议\n"
).encode("utf-8")

SPLIT_FILE_WITH_NA = (
    "全宗号,目录号,档号,盒号,题名\n"
    "A01,0001,N/A,07,年度 汇编\n"
    "A01,0001,000123,08,会议\n"
).encode("utf-8")

SPLIT_VERSION = "2026.12"


def publish_split(client, version=SPLIT_VERSION, expected_revision=1):
    response = client.post(
        "/api/fields/templates",
        json={"version": version, "preset": "split", "expected_revision": expected_revision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def receive(client, batch_no, content, **extra):
    payload = {"batch_no": batch_no, "hex": content.hex(), **extra}
    return client.post("/api/fields/receive", json=payload)


# ---------------------------------------------------------------------------
# template release
# ---------------------------------------------------------------------------
def test_legacy_template_seeded_and_active(client):
    body = client.get("/api/fields/templates").json()
    assert body["active_version"] == "2026.11"
    assert body["revision"] == 1
    legacy = body["templates"][0]
    assert [c["name"] for c in legacy["columns"]] == ["档号", "盒号", "题名"]
    assert legacy["columns"][0]["role"] == "archive_id_merged"
    assert "N/A" in legacy["null_tokens"]


def test_publish_split_template_releases_new_revision(client):
    published = publish_split(client)
    assert published["revision"] == 2
    assert published["template"]["columns"][2]["name"] == "档号"
    assert published["template"]["columns"][2]["role"] == "archive_id"
    assert published["template"]["null_tokens"] == [""]
    assert published["template"]["allow_literal_na_archive_id"] is True
    listing = client.get("/api/fields/templates").json()
    assert listing["active_version"] == SPLIT_VERSION
    assert listing["revision"] == 2
    assert [t["version"] for t in listing["templates"]] == ["2026.11", SPLIT_VERSION]
    assert listing["templates"][0]["active"] is False


def test_stale_publication_is_rejected(client):
    publish_split(client)
    response = client.post(
        "/api/fields/templates",
        json={"version": "late", "preset": "split", "expected_revision": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "STALE_VERSION"
    # The rejected release did not move the revision.
    assert client.get("/api/fields/templates").json()["revision"] == 2


def test_concurrent_publication_only_one_wins(tmp_path):
    store = Store(str(tmp_path / "concurrent.db"))
    from app.modules.m3_fields import templates

    barrier = threading.Barrier(2)
    results = []

    def publish(version):
        definition = templates.normalize_definition({"version": version, "preset": "split"})
        barrier.wait()
        try:
            store.publish_template(definition, expected_revision=1)
            results.append(("ok", version))
        except DomainError as exc:
            assert exc.status_code == 409
            results.append(("stale", version))

    threads = [threading.Thread(target=publish, args=(version,)) for version in ("v-a", "v-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(status for status, _ in results) == ["ok", "stale"]
    state = store.active_state()
    assert state["revision"] == 2
    winners = [version for status, version in results if status == "ok"]
    assert state["active_version"] == winners[0]


def test_invalid_template_definition_is_rejected(client):
    # An "other" column cannot be given the archive role twice, and a split
    # template without 全宗号 would silently change identity.
    response = client.post(
        "/api/fields/templates",
        json={"version": "bad", "columns": [{"name": "档号", "role": "archive_id"}, {"name": "目录号", "role": "catalog_no"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_VALUE"


# ---------------------------------------------------------------------------
# receiving
# ---------------------------------------------------------------------------
def test_receive_legacy_file_keeps_columns_lines_and_zeros(client):
    body = receive(client, "OLD-1", LEGACY_FILE).json()
    receipt = body["receipt"]
    assert body["status"] == "RECEIVED"
    assert body["template_version"] == "2026.11"
    assert receipt["header"] == ["档号", "盒号", "题名"]
    assert [r["source_line"] for r in receipt["records"]] == [2, 4, 5]
    assert receipt["valid_identities"] == [{"archive_id": "000123"}, {"archive_id": "9007199254740993"}]
    assert receipt["records"][1]["source_line"] == 4
    assert receipt["quarantine"][0]["reason_code"] == "BAD_COLUMNS"
    assert receipt["repeated_header_count"] == 1
    assert receipt["input_count"] == 3
    assert receipt["valid_count"] == 2
    assert receipt["quarantine_count"] == 1
    assert receipt["valid_plus_quarantine"] == 3


def test_accepts_gb18030_and_rejects_undecodable(client):
    gb = "档号,盒号,题名\n000123,07,年度汇编\n".encode("gb18030")
    body = receive(client, "GB-1", gb).json()
    assert body["receipt"]["encoding"] == "gb18030"
    assert body["receipt"]["header"] == ["档号", "盒号", "题名"]

    response = receive(client, "BAD-ENC", b"\x81\x81\x81")
    assert response.status_code == 422
    assert response.json()["error"] == "DECODE_FAILED"


def test_missing_header_is_rejected(client):
    response = receive(client, "EMPTY-1", b"")
    assert response.status_code == 422
    assert response.json()["error"] == "HEADER_MISSING"


def test_truncated_tail_is_quarantined(client):
    text = "档号,盒号,题名\n000123,07,好\n000124,08,\"未完"
    body = receive(client, "TRUNC-1", text.encode("utf-8")).json()
    receipt = body["receipt"]
    assert receipt["quarantine"][-1]["reason_code"] == "TRUNCATED"
    assert receipt["valid_count"] == 1
    assert receipt["balanced"] is True


def test_split_template_keeps_literal_na_and_leading_zeros(client):
    publish_split(client)
    body = receive(client, "NEW-1", SPLIT_FILE_WITH_NA).json()
    receipt = body["receipt"]
    assert body["template_version"] == SPLIT_VERSION
    assert body["template_revision"] == 2
    first = receipt["records"][0]
    assert first["values"] == {
        "fonds_no": "A01",
        "catalog_no": "0001",
        "archive_id": "N/A",
        "box_no": "07",
        "title": "年度 汇编",
    }
    assert first["identity"] == {"fonds_no": "A01", "catalog_no": "0001", "archive_id": "N/A"}
    assert receipt["valid_identities"][1]["archive_id"] == "000123"
    assert receipt["valid_count"] == 2
    assert receipt["quarantine_count"] == 0


def test_header_must_match_bound_template(client):
    publish_split(client)
    response = receive(client, "MISMATCH-1", LEGACY_FILE)
    assert response.status_code == 422
    assert response.json()["error"] == "TEMPLATE_MISMATCH"
    # Nothing was recorded for a rejected bind.
    assert client.get("/api/fields/batches").json()["batches"] == []


def test_batch_without_template_version_binds_active_at_receive(client):
    publish_split(client)
    body = receive(client, "ACTIVE-1", SPLIT_FILE).json()
    assert body["template_version"] == SPLIT_VERSION


def test_explicit_old_template_still_accepted_after_upgrade(client):
    publish_split(client)
    response = receive(client, "BACKFILL-1", LEGACY_FILE, template_version="2026.11")
    assert response.status_code == 200
    body = response.json()
    assert body["template_version"] == "2026.11"
    assert body["template_revision"] == 1
    assert body["receipt"]["header"] == ["档号", "盒号", "题名"]


# ---------------------------------------------------------------------------
# idempotency / upgrade compatibility
# ---------------------------------------------------------------------------
def test_old_batch_retry_after_upgrade_returns_identical_receipt(client):
    before = receive(client, "OLD-RETRY", LEGACY_FILE).json()["receipt"]
    publish_split(client)
    after_response = receive(client, "OLD-RETRY", LEGACY_FILE)
    assert after_response.status_code == 200
    body = after_response.json()
    assert body["duplicate"] is True
    # Byte-for-byte the same receipt: merged columns, old null convention,
    # same row numbers, same leading zeros.
    assert body["receipt"] == before
    assert body["template_version"] == "2026.11"
    batches = client.get("/api/fields/batches").json()["batches"]
    assert len(batches) == 1
    assert batches[0]["template_revision"] == 1


def test_same_content_new_batch_after_upgrade_uses_new_template(client):
    old = receive(client, "SAME-OLD", LEGACY_FILE).json()
    publish_split(client)
    new = receive(client, "SAME-NEW", SPLIT_FILE).json()
    assert old["template_version"] == "2026.11"
    assert new["template_version"] == SPLIT_VERSION
    assert new["created"] is True
    # The logical archives survive the format change: leading zeros and the
    # large serial are identical, N/A is a value in the new convention.
    old_ids = {row["archive_id"] for row in old["receipt"]["valid_identities"]}
    new_ids = {row["archive_id"] for row in new["receipt"]["valid_identities"]}
    assert old_ids == new_ids == {"000123", "9007199254740993"}
    # And retrying the old batch still yields the pre-upgrade receipt.
    assert receive(client, "SAME-OLD", LEGACY_FILE).json()["receipt"] == old["receipt"]


def test_same_batch_no_different_content_rejected(client):
    assert receive(client, "DUP-1", LEGACY_FILE).status_code == 200
    response = receive(client, "DUP-1", SPLIT_FILE)
    assert response.status_code == 409
    assert response.json()["error"] == "DUPLICATE_CONFLICT"
    assert len(client.get("/api/fields/batches").json()["batches"]) == 1


def test_different_content_same_digest_batch_id_is_stable(client):
    first = receive(client, None, LEGACY_FILE).json()
    second = receive(client, None, LEGACY_FILE).json()
    assert first["batch"]["batch_no"] == second["batch"]["batch_no"]
    assert second["duplicate"] is True


# ---------------------------------------------------------------------------
# interrupted receive / resume
# ---------------------------------------------------------------------------
def test_partial_receive_can_resume_without_duplicates(client):
    publish_split(client)
    many = "全宗号,目录号,档号,盒号,题名\n" + "".join(
        f"A01,{i:04d},{i:06d},{i:02d},题名{i}\n" for i in range(1, 26)
    )
    prompt = receive(client, "STREAM-1", many.encode(), max_records=10).json()
    assert prompt["status"] == "RECEIVING"
    assert prompt["resumable"] is True
    assert prompt["batch"]["valid_count"] == 10

    resumed = client.post("/api/fields/batches/STREAM-1/resume", json={"max_records": 10}).json()
    assert resumed["status"] == "RECEIVING"
    assert resumed["batch"]["valid_count"] == 20

    finished = client.post("/api/fields/batches/STREAM-1/resume", json={}).json()
    assert finished["status"] == "RECEIVED"
    assert finished["batch"]["valid_count"] == 25
    assert len(finished["receipt"]["records"]) == 25
    assert [r["seq"] for r in finished["receipt"]["records"]] == list(range(1, 26))

    # A retry after completion is a pure read: no new rows, same receipt.
    retry = receive(client, "STREAM-1", many.encode()).json()
    assert retry["duplicate"] is True
    assert retry["receipt"] == finished["receipt"]
    assert len(client.get("/api/fields/batches").json()["batches"]) == 1


def test_resume_after_restart_uses_persisted_db(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    db_path = str(tmp_path / "restart.db")
    many = "档号,盒号,题名\n" + "".join(f"{i:06d},{i:02d},题名{i}\n" for i in range(1, 31))

    with TestClient(create_app(_settings(db_path))) as first:
        partial = receive(first, "RESTART-1", many.encode(), max_records=12).json()
        assert partial["status"] == "RECEIVING"

    with TestClient(create_app(_settings(db_path))) as second:
        resumed = second.post("/api/fields/batches/RESTART-1/resume", json={}).json()
        assert resumed["status"] == "RECEIVED"
        assert resumed["batch"]["valid_count"] == 30
        assert len(resumed["receipt"]["records"]) == 30
        # Receipt is reproducible: a full retry returns exactly this receipt.
        assert receive(second, "RESTART-1", many.encode()).json()["receipt"] == resumed["receipt"]
        assert second.get("/api/health").json()["batches"] == 1


def _settings(db_path):
    from app.config import Settings

    return Settings(db_path=db_path)


# ---------------------------------------------------------------------------
# reconciliation with quarantine
# ---------------------------------------------------------------------------
def test_receipt_quarantine_and_valid_sum_to_input(client):
    body = receive(client, "QUAR-1", LEGACY_FILE).json()
    receipt = body["receipt"]
    assert receipt["valid_count"] + receipt["quarantine_count"] + receipt["empty_count"] == receipt["input_count"]
    assert receipt["valid_plus_quarantine"] == 3
    reconciled = client.post(
        "/api/reconcile/batch/QUAR-1",
        json={"index_ids": ["000123", "9007199254740993"]},
    ).json()
    assert reconciled["missing"] == []
    assert reconciled["orphan"] == []
    assert reconciled["quarantine_count"] == 1
    assert reconciled["valid_plus_quarantine"] == 3
    assert reconciled["receipt_balanced"] is True
    assert reconciled["reason"] == "OK"


def test_reconcile_flags_index_mismatch_after_isolation(client):
    receive(client, "QUAR-2", LEGACY_FILE)
    reconciled = client.post(
        "/api/reconcile/batch/QUAR-2",
        json={"index_ids": ["000123", "9007199254740993", "000404"]},
    ).json()
    assert reconciled["missing"] == ["000404"]
    assert reconciled["reason"] == "DIFF"


def test_no_empty_batch_for_rejected_receive(client):
    response = receive(client, "REJECT-1", b"wrong,columns\n1,2\n")
    assert response.status_code == 422
    assert client.get("/api/fields/batches").json()["batches"] == []


def test_batch_reconcile_reports_batch_and_index_balance_separately(client):
    receive(client, "BAL-1", LEGACY_FILE)
    body = client.post(
        "/api/reconcile/batch/BAL-1",
        json={"index_ids": ["000123", "9007199254740993", "000404"]},
    ).json()
    # Index side reports one missing id; batch side still balances because
    # the quarantined row is counted, not dropped.
    assert body["missing"] == ["000404"]
    assert body["batch_input_count"] == 3
    assert body["batch_accounted_count"] == 3
    assert body["batch_balanced"] is True
    assert body["reason"] == "DIFF"


def test_batch_reconcile_without_index_checks_self_consistency(client):
    receive(client, "SELF-1", LEGACY_FILE)
    body = client.post("/api/reconcile/batch/SELF-1", json={}).json()
    assert body["missing"] == [] and body["orphan"] == []
    assert body["batch_balanced"] is True
    assert body["reason"] == "OK"


def test_batch_reconcile_on_unfinished_batch_is_rejected(client):
    many = "档号,盒号,题名\n" + "".join(f"{i:06d},{i:02d},t\n" for i in range(1, 6))
    receive(client, "OPEN-1", many.encode(), max_records=2)
    response = client.post("/api/reconcile/batch/OPEN-1", json={})
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_VALUE"


def test_parse_keeps_empty_lines_in_accounting():
    template = {"version": "t", "columns": [{"name": "档号", "role": "archive_id_merged", "required": True}, {"name": "盒号", "role": "box_no"}, {"name": "题名", "role": "title"}], "null_tokens": [""]}
    parsed = core.parse_text("档号,盒号,题名\n\n000123,07,x\n", template)
    assert parsed["counts"]["empty"] == 1
    assert parsed["input_count"] == 2
    assert parsed["records"][1]["source_line"] == 3


def test_same_bytes_new_batch_explicit_legacy_binding_after_upgrade(client):
    before = receive(client, "BYTES-OLD", LEGACY_FILE).json()["receipt"]
    publish_split(client)
    after = receive(client, "BYTES-NEW", LEGACY_FILE, template_version="2026.11")
    assert after.status_code == 200
    receipt = after.json()["receipt"]
    # Same bytes, deliberately received under the old convention: identical
    # identity evidence, only the batch-level receipt id differs.
    assert receipt["valid_identities"] == before["valid_identities"]
    assert receipt["header"] == before["header"]
    assert receipt["records"] == before["records"]


def test_batch_keeps_snapshot_when_template_published_midstream(client):
    many = "档号,盒号,题名\n" + "".join(f"{i:06d},{i:02d},题名{i}\n" for i in range(1, 11))
    full_receipt = receive(client, "FULL-REF", many.encode()).json()["receipt"]

    partial = receive(client, "MID-1", many.encode(), max_records=4).json()
    assert partial["status"] == "RECEIVING"
    publish_split(client)
    resumed = client.post("/api/fields/batches/MID-1/resume", json={}).json()
    assert resumed["status"] == "RECEIVED"
    assert resumed["template_version"] == "2026.11"
    # The batch keeps the convention it was bound to; only the receipt id
    # differs from the untouched reference batch.
    for key in ("header", "records", "valid_identities", "valid_count", "input_count"):
        assert resumed["receipt"][key] == full_receipt[key]


def test_resume_cannot_rebind_template(client):
    many = "档号,盒号,题名\n" + "".join(f"{i:06d},{i:02d},题名{i}\n" for i in range(1, 6))
    receive(client, "REBIND-1", many.encode(), max_records=2)
    publish_split(client)
    response = receive(client, "REBIND-1", many.encode(), template_version=SPLIT_VERSION)
    assert response.status_code == 409
    assert response.json()["error"] == "TEMPLATE_MISMATCH"


def test_resumed_batch_quarantine_still_balances(client):
    text = "档号,盒号,题名\n" + "".join(f"{i:06d},{i:02d},题名{i}\n" for i in range(1, 8)) + "BAD,09\n"
    first = receive(client, "QUAR-STREAM", text.encode(), max_records=3).json()
    assert first["status"] == "RECEIVING"
    final = client.post("/api/fields/batches/QUAR-STREAM/resume", json={}).json()
    receipt = final["receipt"]
    assert receipt["valid_count"] == 7
    assert receipt["quarantine_count"] == 1
    assert receipt["valid_plus_quarantine"] == receipt["input_count"] == 8
    assert receipt["quarantine"][0]["source_line"] == 9


def test_publication_rejects_unknown_template_fields(client):
    response = client.post(
        "/api/fields/templates",
        json={"version": "typo", "preset": "split", "null_token": [""]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_VALUE"


def test_duplicate_version_is_rejected(client):
    response = client.post("/api/fields/templates", json={"version": "2026.11", "preset": "legacy"})
    assert response.status_code == 409
    assert response.json()["error"] == "DUPLICATE_CONFLICT"


def test_revision_must_be_next(client):
    response = client.post(
        "/api/fields/templates",
        json={"version": "future", "preset": "split", "revision": 9},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_VALUE"


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------
def test_receive_accepts_json_form_multipart_and_raw(client):
    body = LEGACY_FILE
    json_resp = client.post("/api/fields/receive", json={"batch_no": "T-JSON", "text": body.decode()})
    form_resp = client.post("/api/fields/receive", data={"batch_no": "T-FORM", "text": body.decode()})
    multi_resp = client.post(
        "/api/fields/receive",
        files={"file": ("legacy.csv", body, "text/csv")},
        data={"batch_no": "T-MULTI"},
    )
    raw_resp = client.post(
        "/api/fields/receive/raw",
        params={"batch_no": "T-RAW"},
        content=body,
        headers={"content-type": "text/csv"},
    )
    for response in (json_resp, form_resp, multi_resp, raw_resp):
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "RECEIVED"
    receipts = [
        response.json()["receipt"]
        for response in (json_resp, form_resp, multi_resp, raw_resp)
    ]
    for key in ("header", "records", "valid_count", "quarantine_count", "valid_identities"):
        assert all(receipt[key] == receipts[0][key] for receipt in receipts)
    assert len(client.get("/api/fields/batches").json()["batches"]) == 4


def test_normalize_endpoint_still_receives_files(client):
    body = LEGACY_FILE
    response = client.post(
        "/api/fields/normalize",
        files={"file": ("legacy.csv", body, "text/csv")},
        data={"batch_no": "NORM-FILE"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RECEIVED"
    assert response.json()["batch"]["batch_no"] == "NORM-FILE"
    # The JSON normalizer contract is unchanged.
    json_resp = client.post("/api/fields/normalize", json={"values": ["000123", "N/A"]})
    assert json_resp.status_code == 200
    assert json_resp.json()["valid_count"] == 1


def test_receive_ignores_empty_form_fields(client):
    response = client.post(
        "/api/fields/receive",
        files={"file": ("legacy.csv", LEGACY_FILE, "text/csv")},
        data={"batch_no": "", "template_version": "", "max_records": ""},
    )
    assert response.status_code == 200
    assert response.json()["batch"]["batch_no"].startswith("B")

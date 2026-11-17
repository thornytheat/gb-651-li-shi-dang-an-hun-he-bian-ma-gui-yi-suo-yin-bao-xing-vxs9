"""Template registry unit tests (no HTTP layer)."""
import pytest

from app.errors import DomainError
from app.modules.m3_fields import templates
from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_legacy_is_seeded_once(tmp_path):
    path = str(tmp_path / "seed.db")
    first = Store(path)
    second = Store(path)
    assert first.list_templates() == second.list_templates()
    assert len(second.list_templates()) == 1
    assert second.active_state() == {"active_version": "2026.11", "revision": 1}


def test_presets_are_immutable_between_calls():
    a = templates.legacy_definition("v1")
    b = templates.legacy_definition("v1")
    a["columns"][0]["name"] = "篡改"
    assert b["columns"][0]["name"] == "档号"


def test_split_preset_documents_literal_na():
    definition = templates.split_definition("v2", revision=2)
    assert definition["null_tokens"] == [""]
    assert definition["allow_literal_na_archive_id"] is True
    roles = [c["role"] for c in definition["columns"]]
    assert roles[:3] == ["fonds_no", "catalog_no", "archive_id"]


def test_legacy_preset_keeps_na_as_null():
    definition = templates.legacy_definition("v1")
    assert "N/A" in definition["null_tokens"]
    assert definition["allow_literal_na_archive_id"] is False


def test_definition_digest_changes_with_null_convention():
    legacy = templates.legacy_definition("same")
    split = templates.split_definition("same")
    assert templates.definition_digest(legacy) != templates.definition_digest(split)


def test_definition_digest_ignores_label():
    a = templates.split_definition("v9")
    b = dict(a, label="别的标签")
    assert templates.definition_digest(a) == templates.definition_digest(b)


def test_unknown_role_rejected():
    with pytest.raises(DomainError) as exc:
        templates.normalize_definition(
            {"version": "v", "columns": [{"name": "档号", "role": "mystery"}]}
        )
    assert exc.value.code == "INVALID_VALUE"


def test_unknown_field_rejected():
    with pytest.raises(DomainError):
        templates.normalize_definition({"version": "v", "preset": "split", "nulls": []})


def test_split_without_fonds_rejected():
    with pytest.raises(DomainError) as exc:
        templates.normalize_definition(
            {
                "version": "v",
                "columns": [
                    {"name": "档号", "role": "archive_id"},
                    {"name": "目录号", "role": "catalog_no"},
                ],
            }
        )
    assert "全宗号" in exc.value.message


def test_literal_na_exemption_is_per_column():
    # N/A stays a null token for other columns, but the archive column opts
    # out, so "N/A" there is a value rather than an empty cell.
    definition = templates.normalize_definition(
        {
            "version": "v",
            "preset": "split",
            "null_tokens": ["", "N/A"],
            "allow_literal_na_archive_id": True,
        }
    )
    assert "N/A" in definition["null_tokens"]
    archive = next(c for c in definition["columns"] if c["role"] == "archive_id")
    assert archive["literal_na"] is True


def test_literal_na_without_split_archive_rejected():
    with pytest.raises(DomainError):
        templates.normalize_definition(
            {
                "version": "v",
                "preset": "legacy",
                "allow_literal_na_archive_id": True,
            }
        )


def test_leading_zero_drop_rejected():
    with pytest.raises(DomainError):
        templates.normalize_definition({"version": "v", "preset": "split", "keep_leading_zeros": False})


def test_publish_increments_revision_and_is_immutable(store):
    published = store.publish_template(templates.normalize_definition({"version": "v2", "preset": "split"}))
    assert published["revision"] == 2
    assert store.active_state() == {"active_version": "v2", "revision": 2}
    with pytest.raises(DomainError):
        store.publish_template(templates.normalize_definition({"version": "v2", "preset": "legacy"}))
    # The rejected update did not mutate the stored definition.
    assert store.get_template("v2")["preset"] == "split"


def test_publish_requires_expected_revision(store):
    with pytest.raises(DomainError) as exc:
        store.publish_template(
            templates.normalize_definition({"version": "v3", "preset": "split"}),
            expected_revision=0,
        )
    assert exc.value.code == "STALE_VERSION"
    assert store.active_state()["revision"] == 1


def test_publish_with_expected_version_guard(store):
    with pytest.raises(DomainError) as exc:
        store.publish_template(
            templates.normalize_definition({"version": "v4", "preset": "split"}),
            expected_version="2025.01",
        )
    assert exc.value.code == "STALE_VERSION"


def test_old_definition_remains_readable_after_release(store):
    legacy = store.get_template("2026.11")
    store.publish_template(templates.normalize_definition({"version": "v2", "preset": "split"}))
    assert store.get_template("2026.11") == legacy

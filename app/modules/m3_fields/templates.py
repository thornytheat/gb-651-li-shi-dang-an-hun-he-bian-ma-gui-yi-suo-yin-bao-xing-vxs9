"""Receiving-template definitions.

A template is an immutable description of the columns a receiving file is
expected to have, plus the null convention that was in force when it was
published.  Batches snapshot the definition they were received with, so a
later release can never reinterpret (and therefore re-identify) old rows.
"""
from __future__ import annotations

import copy
import json

from app.errors import DomainError

KNOWN_ROLES = {
    "archive_id",
    "archive_id_merged",
    "fonds_no",
    "catalog_no",
    "box_no",
    "title",
    "other",
}

# Old convention: a merged 档号 column and a broad set of "empty" markers.
LEGACY_NULL_TOKENS = ["", "-", "N/A", "NULL", "未知"]

# New convention: only a truly empty cell is null.  In particular a literal
# N/A in the 档号 column is a valid value and must survive untouched.
NEW_NULL_TOKENS = [""]

LEGACY_COLUMNS = [
    {"name": "档号", "role": "archive_id_merged", "required": True},
    {"name": "盒号", "role": "box_no", "required": False},
    {"name": "题名", "role": "title", "required": False},
]

NEW_COLUMNS = [
    {"name": "全宗号", "role": "fonds_no", "required": True},
    {"name": "目录号", "role": "catalog_no", "required": True},
    {"name": "档号", "role": "archive_id", "required": True, "literal_na": True},
    {"name": "盒号", "role": "box_no", "required": False},
    {"name": "题名", "role": "title", "required": False},
]


def legacy_definition(version: str, revision: int = 1) -> dict:
    return {
        "version": str(version),
        "revision": int(revision),
        "label": "既有合并列模板",
        "preset": "legacy",
        "columns": copy.deepcopy(LEGACY_COLUMNS),
        "null_tokens": list(LEGACY_NULL_TOKENS),
        "keep_leading_zeros": True,
        "allow_literal_na_archive_id": False,
    }


def split_definition(version: str, revision: int = 0) -> dict:
    return {
        "version": str(version),
        "revision": int(revision),
        "label": "馆方新版接收模板（全宗号/目录号拆列）",
        "preset": "split",
        "columns": copy.deepcopy(NEW_COLUMNS),
        "null_tokens": list(NEW_NULL_TOKENS),
        "keep_leading_zeros": True,
        "allow_literal_na_archive_id": True,
    }


ROLE_BY_NAME = {
    "全宗号": "fonds_no",
    "目录号": "catalog_no",
    "盒号": "box_no",
    "题名": "title",
}


def _infer_role(name: str, all_names: list[str], explicit: str | None) -> str:
    """Infer a role from a familiar column name when none was given.

    ``档号`` next to 全宗号/目录号 is the split archive column; standing alone
    it is the legacy merged 档号.  Explicit roles always win.
    """
    if explicit:
        return explicit
    if name == "档号":
        split = any(other in {"全宗号", "目录号"} for other in all_names)
        return "archive_id" if split else "archive_id_merged"
    return ROLE_BY_NAME.get(name, "other")


def _normalize_column(raw, index: int, all_names: list[str]) -> dict:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise DomainError("INVALID_VALUE", f"第 {index + 1} 列定义必须是字符串或对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise DomainError("INVALID_VALUE", f"第 {index + 1} 列缺少列名")
    role = _infer_role(name, all_names, str(raw.get("role") or "").strip() or None)
    if role not in KNOWN_ROLES:
        raise DomainError("INVALID_VALUE", f"列 {name} 的角色未登记：{role}")
    column = {
        "name": name,
        "role": role,
        "required": bool(raw.get("required", False)),
        "literal_na": bool(raw.get("literal_na", False)),
    }
    # An archive column is intrinsically required; making it optional would
    # silently turn a whole batch into anonymous rows.
    if role in {"archive_id", "archive_id_merged"}:
        column["required"] = True
    if role in {"fonds_no", "catalog_no"}:
        column["required"] = bool(raw.get("required", True))
    return column


ALLOWED_KEYS = {
    "version",
    "label",
    "preset",
    "columns",
    "null_tokens",
    "keep_leading_zeros",
    "allow_literal_na_archive_id",
    "expected_revision",
    "base_revision",
    "revision",
    "expected_version",
    "base_version",
}

# Accept a few obvious spellings for the same knob.  They map onto the
# canonical names before validation so one typo cannot silently publish a
# template with a different null convention.
KEY_ALIASES = {
    "template_version": "version",
    "nulls": "null_tokens",
    "null_values": "null_tokens",
    "nulls_as_empty": "null_tokens",
    "keep_zeros": "keep_leading_zeros",
    "preserve_leading_zeros": "keep_leading_zeros",
    "allow_na": "allow_literal_na_archive_id",
    "allow_literal_na": "allow_literal_na_archive_id",
    "na_is_value": "allow_literal_na_archive_id",
    "base_rev": "expected_revision",
}


def _apply_aliases(payload: dict) -> dict:
    out = dict(payload)
    for alias, canonical in KEY_ALIASES.items():
        if alias in out and canonical not in out:
            out[canonical] = out.pop(alias)
        elif alias in out:
            out.pop(alias)
    return out


def normalize_definition(payload: dict) -> dict:
    """Validate a submitted template definition and return a plain dict.

    Only the fields that affect receiving are kept.  Unknown fields are
    rejected so a typo cannot silently change the null convention.
    """
    if not isinstance(payload, dict):
        raise DomainError("INVALID_VALUE", "模板定义必须是对象")
    payload = _apply_aliases(payload)
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise DomainError("INVALID_VALUE", "模板定义含未登记字段：" + ",".join(unknown))
    version = str(payload.get("version") or "").strip()
    if not version:
        raise DomainError("INVALID_VALUE", "模板版本不能为空")

    preset = payload.get("preset")
    base = None
    if preset == "legacy":
        base = legacy_definition(version)
    elif preset == "split":
        base = split_definition(version)

    raw_columns = payload.get("columns", base["columns"] if base else None)
    if not raw_columns or not isinstance(raw_columns, list):
        raise DomainError("INVALID_VALUE", "模板必须包含至少一列")
    raw_names = [str(c.get("name") if isinstance(c, dict) else c).strip() for c in raw_columns]
    columns = [
        _normalize_column(column, index, raw_names)
        for index, column in enumerate(raw_columns)
    ]
    names = [column["name"] for column in columns]
    if len(set(names)) != len(names):
        raise DomainError("INVALID_VALUE", "模板列名重复")
    roles = [column["role"] for column in columns]
    business_roles = [role for role in roles if role != "other"]
    if len(set(business_roles)) != len(business_roles):
        raise DomainError("INVALID_VALUE", "模板业务列角色重复")
    if "archive_id" in roles and "archive_id_merged" in roles:
        raise DomainError("INVALID_VALUE", "档号列不能同时使用合并列与拆列角色")
    if "archive_id" not in roles and "archive_id_merged" not in roles:
        raise DomainError("INVALID_VALUE", "模板必须包含档号列")
    if "fonds_no" in roles and "catalog_no" not in roles:
        raise DomainError("INVALID_VALUE", "拆列模板缺少目录号列")
    if "catalog_no" in roles and "fonds_no" not in roles:
        raise DomainError("INVALID_VALUE", "拆列模板缺少全宗号列")

    null_tokens = payload.get("null_tokens")
    if null_tokens is None:
        null_tokens = base["null_tokens"] if base else [""]
    if not isinstance(null_tokens, list) or any(not isinstance(token, str) for token in null_tokens):
        raise DomainError("INVALID_VALUE", "空值约定必须是字符串列表")
    if "" not in null_tokens:
        raise DomainError("INVALID_VALUE", "空值约定必须包含空字符串")

    keep_leading_zeros = payload.get("keep_leading_zeros", True)
    if not keep_leading_zeros:
        raise DomainError("INVALID_VALUE", "模板必须保留前导零")

    allow_literal_na = payload.get(
        "allow_literal_na_archive_id",
        base["allow_literal_na_archive_id"] if base else any(c.get("literal_na") for c in columns),
    )
    # A literal-N/A exemption is per column.  Asking for it globally without a
    # column that can carry it would be an unsupported promise.
    if allow_literal_na and "archive_id" not in roles:
        raise DomainError("INVALID_VALUE", "字面 N/A 约定只能用于拆列档号")
    if allow_literal_na and "archive_id" in roles:
        for column in columns:
            if column["role"] == "archive_id":
                column["literal_na"] = True

    definition = {
        "version": version,
        "label": str(payload.get("label") or (base["label"] if base else "自定义接收模板")).strip(),
        "preset": preset or (base["preset"] if base else "custom"),
        "columns": columns,
        "null_tokens": list(dict.fromkeys(null_tokens)),
        "keep_leading_zeros": True,
        "allow_literal_na_archive_id": bool(allow_literal_na),
    }
    return definition


def receiving_content(definition: dict) -> dict:
    """The part of a definition that changes receiving behaviour."""
    return {
        "columns": definition.get("columns"),
        "null_tokens": definition.get("null_tokens"),
        "keep_leading_zeros": bool(definition.get("keep_leading_zeros", True)),
        "allow_literal_na_archive_id": bool(definition.get("allow_literal_na_archive_id", False)),
    }


def definition_digest(definition: dict) -> str:
    from app.manifest import canonical_digest

    return canonical_digest({"version": definition.get("version"), **receiving_content(definition)})


def response_view(definition: dict) -> dict:
    view = json.loads(json.dumps(definition, ensure_ascii=False))
    view["digest"] = definition_digest(definition)
    return view

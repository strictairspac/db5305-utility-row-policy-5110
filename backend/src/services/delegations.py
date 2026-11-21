"""Pure helpers for delegation snapshots and dispatch corrections.

A delegation snapshot is an immutable list of rows.  Corrections never mutate a
snapshot in place: they derive a new list which is then published as a new
version.  Keeping this module free of database imports makes the merge rules
straightforward to test.
"""

import hashlib
from datetime import datetime

from src.utils.encoding import canonical

UTILITIES = {"水表", "电表"}
KINDS = {"urgent_revoke", "handover"}


def parse_time(value, label):
    """Parse an ISO 8601 timestamp that must carry a UTC offset."""
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() is not None:
            return timestamp
    except (TypeError, ValueError):
        pass
    raise ValueError(f"{label}须使用含时区的 ISO 8601 日期时间。")


def _text(row, field, label):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label}的{field}须为非空文本。")
    return value


def _key(row):
    return (row["operator_id"], row["zone"], row["utility"])


def normalize_delegation(row, label="区域委派"):
    """Validate and normalize one delegation row, keeping timestamps as text."""
    if not isinstance(row, dict):
        raise ValueError(f"{label}的每一行须为对象。")
    operator_id = _text(row, "operator_id", label)
    zone = _text(row, "zone", label)
    utility = _text(row, "utility", label)
    if utility not in UTILITIES:
        raise ValueError("表计类型须为水表或电表。")
    start = parse_time(row.get("valid_from"), f"{label}的生效时间")
    end = parse_time(row.get("valid_until"), f"{label}的到期时间")
    if start >= end:
        raise ValueError(f"人员 {operator_id} 的委派到期时间须晚于生效时间。")
    return {
        "operator_id": operator_id,
        "zone": zone,
        "utility": utility,
        "valid_from": start.isoformat(),
        "valid_until": end.isoformat(),
    }


def normalize_snapshot(rows):
    if not isinstance(rows, list):
        raise ValueError("区域委派快照须为数组。")
    normalized = []
    for index, row in enumerate(rows, start=1):
        normalized.append(normalize_delegation(row, f"区域委派第 {index} 行"))
    normalized.sort(key=lambda row: (*_key(row), row["valid_from"], row["valid_until"]))
    return normalized


def _revoke_rows(rows):
    result = []
    if not isinstance(rows, list):
        raise ValueError("撤销清单须为数组。")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"撤销清单第 {index} 行须为对象。")
        label = f"撤销清单第 {index} 行"
        result.append({
            "operator_id": _text(row, "operator_id", label),
            "zone": _text(row, "zone", label),
            "utility": _text(row, "utility", label),
        })
    return result


def _reassign_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("续派清单须为数组。")
    return [normalize_delegation(row, f"续派清单第 {index} 行") for index, row in enumerate(rows, start=1)]


def prepare(kind, handover_ref, revoke, reassign):
    """Validate the shape of a dispatch correction.

    ``urgent_revoke`` carries only revocations and may be published on its own.
    ``handover`` groups one or more messages by ``handover_ref``; the group is
    published only after it contains both a revocation and a re-extension.
    """
    if kind not in KINDS:
        raise ValueError("更正类型须为 urgent_revoke 或 handover。")
    revoke_rows = _revoke_rows(revoke or [])
    reassign_rows = _reassign_rows(reassign or [])
    if kind == "urgent_revoke":
        if reassign_rows:
            raise ValueError("紧急撤销不能包含续派；换班交接请使用 handover。")
        if not revoke_rows:
            raise ValueError("紧急撤销至少需要一条撤销。")
        reference = None
    else:
        if not isinstance(handover_ref, str) or not handover_ref.strip():
            raise ValueError("换班交接须填写交接编号 handover_ref。")
        if len(handover_ref.strip()) > 64 or "\x00" in handover_ref:
            raise ValueError("交接编号须为不超过 64 个字符的文本。")
        if not revoke_rows and not reassign_rows:
            raise ValueError("换班交接至少需要一条撤销或续派。")
        reference = handover_ref.strip()
    return {
        "kind": kind,
        "handover_ref": reference,
        "revoke": revoke_rows,
        "reassign": reassign_rows,
    }


def apply_changes(snapshot, revoke, reassign):
    """Return a new snapshot after revoking then re-extending delegations.

    Revocation must match at least one published row; otherwise the correction
    is rejected so a mistyped person/zone pair cannot silently publish a
    no-op version.  Re-extension replaces every published row sharing the same
    person, zone and meter type, and may also add a new one.
    """
    base = normalize_snapshot(snapshot)
    revoke_keys = {_key(row) for row in _revoke_rows(revoke)}
    present = {_key(row) for row in base}
    missing = sorted(revoke_keys - present)
    if missing:
        operator_id, zone, utility = missing[0]
        raise ValueError(f"撤销未匹配到已发布委派（{operator_id} / {zone} / {utility}）。")
    reassign_rows = _reassign_rows(reassign)
    reassign_keys = {_key(row) for row in reassign_rows}
    rows = [row for row in base if _key(row) not in revoke_keys and _key(row) not in reassign_keys]
    rows.extend(reassign_rows)
    return normalize_snapshot(rows)


def digest(snapshot):
    return hashlib.sha256(canonical(normalize_snapshot(snapshot))).hexdigest()


def request_key(kind, handover_ref, revoke, reassign):
    """Derive a stable idempotency key for one submission.

    The key is content-based and deliberately omits the base version, so a
    retry after a failed publication maps to the same correction and can never
    publish a second time.
    """
    payload = {
        "kind": kind,
        "handover_ref": handover_ref,
        "revoke": _revoke_rows(revoke),
        "reassign": _reassign_rows(reassign),
    }
    return hashlib.sha256(canonical(payload)).hexdigest()[:64]


def reissue_key(base_key, attempt):
    """A distinct, fixed-length key for a repeated instruction."""
    return hashlib.sha256(f'{base_key}:{attempt}'.encode('utf-8')).hexdigest()

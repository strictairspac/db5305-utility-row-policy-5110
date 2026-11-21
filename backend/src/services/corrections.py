"""Publication of delegation versions and dispatch corrections.

Invariants
----------
* A delegation version is immutable.  Corrections and imports derive a new
  version and move the ledger pointer in one transaction; any failure rolls the
  whole thing back and the previously published version stays live.
* ``urgent_revoke`` is published on its own as soon as it arrives.
* ``handover`` fragments are grouped by ``handover_ref`` and only published
  once the group contains both a revocation and a re-extension.
* Submitting the same correction again is a retry, never a second publication.
* A verification pins the version published when it is queued; publishing a
  newer version later does not change what that verification evaluates.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import engine
from src.constants.workflow import CorrectionKind, CorrectionStatus
from src.models.tables import audit, corrections, delegation_ledger, delegation_versions
from src.services.delegations import (apply_changes, digest, normalize_delegation,
                                      normalize_snapshot, prepare, reissue_key,
                                      request_key)
from src.utils.time import utc_time

LEDGER_ID = 1


def _row(result):
    row = result.mappings().first()
    return dict(row) if row else None


def _version(conn, identity, lock=False):
    if identity is None:
        return None
    statement = select(delegation_versions).where(delegation_versions.c.id == identity)
    if lock:
        statement = statement.with_for_update()
    return _row(conn.execute(statement))


def version_by_id(conn, identity):
    return _version(conn, identity)


def list_versions(conn):
    return [dict(row) for row in conn.execute(
        select(delegation_versions).order_by(delegation_versions.c.version_no.desc())).mappings()]


def _version_payload(row):
    if not row:
        return None
    return {key: row[key] for key in
            ('id', 'version_no', 'digest', 'source', 'source_ref', 'published_at')}


def list_corrections(conn):
    versions = {row['id']: row for row in list_versions(conn)}
    result = []
    for row in conn.execute(select(corrections).order_by(corrections.c.id.desc()).limit(100)).mappings():
        item = dict(row)
        item.pop('request_key', None)
        version = versions.get(item.get('version_id'))
        item['version'] = _version_payload(version)
        result.append(item)
    return result


def _ledger(conn, lock=True):
    statement = select(delegation_ledger).where(delegation_ledger.c.id == LEDGER_ID)
    if lock:
        statement = statement.with_for_update()
    return _row(conn.execute(statement))


def _insert_version(conn, version_no, snapshot, source, source_ref=None):
    values = {
        'version_no': version_no,
        'snapshot': snapshot,
        'digest': digest(snapshot),
        'status': 'published',
        'source': source,
        'source_ref': source_ref,
        'created_at': utc_time(),
        'published_at': utc_time(),
    }
    identity = conn.execute(delegation_versions.insert().values(**values)).inserted_primary_key[0]
    return _version(conn, identity)


def baseline_snapshot():
    # Imported lazily so the pure delegation rules stay free of the domain module.
    from src.services.domain import SPEC

    return SPEC['sample']['delegations']


def ensure_baseline(conn, snapshot=None):
    """Create version 1 when nothing has ever been published.

    Safe from the API, the seeder and the worker: concurrent first callers are
    serialised by the unique version number and the loser reads the winner.
    """
    ledger = _ledger(conn, lock=False)
    if ledger:
        return _version(conn, ledger['current_version_id'])
    snapshot = normalize_snapshot(baseline_snapshot() if snapshot is None else snapshot)
    try:
        with conn.begin_nested():
            version = _insert_version(conn, 1, snapshot, 'baseline', 'exported-snapshot')
            conn.execute(delegation_ledger.insert().values(
                id=LEDGER_ID, current_version_id=version['id']))
    except IntegrityError:
        pass
    ledger = _ledger(conn, lock=False)
    if not ledger:
        raise RuntimeError('委派版本台账初始化失败。')
    return _version(conn, ledger['current_version_id'])


def published_version(conn, lock=False):
    """The version a verification queued right now must pin."""
    ledger = _ledger(conn, lock=lock)
    return _version(conn, ledger['current_version_id']) if ledger else None


def import_snapshot(rows, actor, reference=None, conn=None, expected_version_id=None):
    """Publish an externally exported delegation snapshot as a new version.

    Returns ``(version, created)``; an import equal to the live version is a
    no-op so the same export can be re-imported safely.  When
    ``expected_version_id`` is given and the ledger has since moved, the import
    is refused: a form opened before a correction was published must reload
    instead of rolling the correction back with its stale export.
    """
    snapshot = normalize_snapshot(rows)
    if conn is None:
        with engine.begin() as owned:
            return _import(owned, snapshot, actor, reference, expected_version_id)
    return _import(conn, snapshot, actor, reference, expected_version_id)


def _import(conn, snapshot, actor, reference, expected_version_id=None):
    ensure_baseline(conn)
    ledger = _ledger(conn)
    current = _version(conn, ledger['current_version_id'])
    if expected_version_id is not None and int(expected_version_id) != current['id']:
        raise ValueError('已发布的委派版本已更新，请刷新后重新核对再提交。')
    if digest(snapshot) == current['digest']:
        return current, False
    number = conn.execute(select(func.max(delegation_versions.c.version_no))).scalar_one() + 1
    version = _insert_version(conn, number, snapshot, 'import',
                              (reference or '').strip() or 'imported-snapshot')
    conn.execute(delegation_ledger.update().where(delegation_ledger.c.id == LEDGER_ID)
                 .values(current_version_id=version['id']))
    conn.execute(audit.insert().values(actor=actor, action='import_snapshot',
                                       reference=str(version['id']), created_at=utc_time()))
    return version, True


def _merge(rows, label):
    """Merge repeated rows, rejecting conflicting re-extension windows."""
    merged, order = {}, []
    for row in rows:
        key = (row['operator_id'], row['zone'], row['utility'])
        if key in merged:
            if merged[key] != row:
                raise ValueError(f'同一交接对 {key[0]} / {key[1]} / {key[2]} 给出了冲突的{label}。')
            continue
        merged[key] = row
        order.append(key)
    return [merged[key] for key in order]


def _publish(conn, ledger, group, actor):
    revoke, reassign = [], []
    for row in group:
        revoke.extend(row['revoke'] or [])
        reassign.extend(row['reassign'] or [])
    revoke = _merge([{key: row[key] for key in ('operator_id', 'zone', 'utility')} for row in revoke], '撤销')
    reassign = _merge([normalize_delegation(row, '续派清单') for row in reassign], '续派时间')
    current = _version(conn, ledger['current_version_id'], lock=True)
    snapshot = apply_changes(current['snapshot'], revoke, reassign)
    number = conn.execute(select(func.max(delegation_versions.c.version_no))).scalar_one() + 1
    reference = group[0]['handover_ref'] or f"correction:{group[0]['id']}"
    version = _insert_version(conn, number, snapshot, 'correction', reference)
    now = utc_time()
    conn.execute(corrections.update().where(corrections.c.id.in_([row['id'] for row in group]))
                 .values(status=CorrectionStatus.PUBLISHED, version_id=version['id'], published_at=now))
    conn.execute(delegation_ledger.update().where(delegation_ledger.c.id == LEDGER_ID)
                 .values(current_version_id=version['id']))
    conn.execute(audit.insert().values(actor=actor, action='publish_correction',
                                       reference=str(version['id']), created_at=now))
    return version


def _group(conn, correction):
    if correction['kind'] == CorrectionKind.URGENT_REVOKE:
        return [correction]
    return [dict(row) for row in conn.execute(select(corrections).where(
        corrections.c.kind == CorrectionKind.HANDOVER,
        corrections.c.handover_ref == correction['handover_ref'],
        corrections.c.status == CorrectionStatus.PENDING).order_by(corrections.c.id)).mappings()]


def _key(kind, handover_ref, revoke, reassign, request_id):
    # A client-supplied id wins so an explicit retry after a transport error
    # maps to the same correction even if the payload was re-serialised.
    if request_id:
        return 'request:' + request_id
    return request_key(kind, handover_ref, revoke, reassign)


def _reflected(snapshot, correction):
    """Whether a published correction is still visible in the live snapshot.

    Used to tell a retry (the publication already happened, so the effect is
    there) from a fresh instruction with identical text (the delegation has
    since been re-added, so the same text now means something new).
    """
    keyed = {(row['operator_id'], row['zone'], row['utility']): row for row in snapshot}
    for row in correction['revoke'] or []:
        if (row['operator_id'], row['zone'], row['utility']) in keyed:
            return False
    for row in correction['reassign'] or []:
        current = keyed.get((row['operator_id'], row['zone'], row['utility']))
        normalized = normalize_delegation(row, '续派清单')
        if not current or normalize_delegation(current, '已发布委派') != normalized:
            return False
    return True


def _store(conn, base_key, correction, actor, strict=False):
    """Insert a fragment, reusing ``base_key`` only if it is a true retry.

    ``strict`` is set when the client supplied a request id: such a key is
    always an idempotency key, so any existing row is the same request even if
    the live snapshot has since changed.
    """
    key = base_key
    existing = _row(conn.execute(select(corrections).where(corrections.c.request_key == key)))
    if existing is not None and existing['status'] == CorrectionStatus.PUBLISHED and not strict:
        live = _version(conn, _ledger(conn, lock=False)['current_version_id'])
        if not _reflected(live['snapshot'], existing):
            # Same text, different meaning: keep both records with distinct keys.
            suffix = 2
            while _row(conn.execute(select(corrections.c.id).where(
                    corrections.c.request_key == reissue_key(base_key, suffix)))):
                suffix += 1
            key, existing = reissue_key(base_key, suffix), None
    if existing is None:
        identity = conn.execute(corrections.insert().values(
            request_key=key, kind=correction['kind'], handover_ref=correction['handover_ref'],
            revoke=correction['revoke'], reassign=correction['reassign'],
            status=CorrectionStatus.PENDING, actor=actor, created_at=utc_time(),
        )).inserted_primary_key[0]
        existing = _row(conn.execute(select(corrections).where(corrections.c.id == identity)))
    return existing


def record(kind, handover_ref, revoke, reassign, actor, request_id=None):
    """Store a correction fragment and publish it when its group is complete.

    ``request_id`` is an optional idempotency key.  Without one the content of
    the submission is the key, so a retry is recognised even if the client did
    not send an explicit id.  A repeated submission whose effect is no longer
    present in the live version is treated as a new instruction instead.

    An incomplete handover stays pending and is returned with ``published_at``
    unset; nothing is published until the group has both parts.
    """
    prepared = prepare(kind, handover_ref, revoke, reassign)
    key = _key(kind, prepared['handover_ref'], revoke, reassign, request_id)
    with engine.begin() as conn:
        ensure_baseline(conn)
        # Serialise every submission on the ledger row; it also guards the
        # "one published version at a time" invariant.
        ledger = _ledger(conn)
        correction = _store(conn, key, prepared, actor, strict=bool(request_id))
        if correction['status'] == CorrectionStatus.PUBLISHED:
            return correction, _version(conn, correction['version_id'])
        if correction['kind'] == CorrectionKind.HANDOVER:
            later = conn.execute(select(corrections.c.id).where(
                corrections.c.kind == CorrectionKind.HANDOVER,
                corrections.c.handover_ref == correction['handover_ref'],
                corrections.c.status == CorrectionStatus.PUBLISHED).limit(1)).first()
            if later:
                raise ValueError(f"交接 {correction['handover_ref']} 已发布，未再收齐的片段不能并入。")
        group = _group(conn, correction)
        complete = any(row['revoke'] for row in group) and any(row['reassign'] for row in group)
        if correction['kind'] == CorrectionKind.HANDOVER and not complete:
            return correction, None
        version = _publish(conn, ledger, group, actor)
        correction = _row(conn.execute(select(corrections).where(corrections.c.id == correction['id'])))
    return correction, version


def submit(kind, handover_ref, revoke, reassign, actor, request_id=None):
    return record(kind, handover_ref, revoke, reassign, actor, request_id)

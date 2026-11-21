import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import cache, engine
from src.constants.workflow import Status
from src.models.tables import audit, cases, evidence, runs
from src.services import corrections, domain
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def make_case(title, payload, actor, base_version_id=None):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        slug = uuid.uuid4().hex
        # Import the delegation export first: a stale or malformed export
        # aborts the whole transaction, so no half-made case is left behind.
        if isinstance(payload.get('delegations'), list):
            try:
                corrections.import_snapshot(payload['delegations'], actor,
                                            reference=f'case:{slug}', conn=conn,
                                            expected_version_id=base_version_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        case_id = conn.execute(cases.insert().values(slug=slug, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def _wake():
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass


def enqueue(case_id, actor):
    with engine.begin() as conn:
        found(conn, cases, case_id)
        # Pin the version that is published at this instant, so a correction
        # published while the run waits in the queue does not change what it sees.
        corrections.ensure_baseline(conn)
        version = corrections.published_version(conn, lock=True)
        run_id = conn.execute(runs.insert().values(
            case_id=case_id, status=Status.QUEUED, owner=actor,
            delegation_version_id=version['id'], created_at=utc_time(),
        )).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    _wake()
    return result


def retry(identity, actor):
    """Re-run a failed verification against the very version it already pinned."""
    with engine.begin() as conn:
        previous = found(conn, runs, identity)
        if previous['status'] != Status.FAILED:
            raise HTTPException(409, '只有执行失败的验签可以重试')
        replay = conn.execute(select(runs.c.id).where(runs.c.previous_run_id == identity)).first()
        if replay:
            # A retry already exists; returning it keeps the retry idempotent.
            return found(conn, runs, replay.id)
        if previous['delegation_version_id'] is None:
            corrections.ensure_baseline(conn)
            version = corrections.published_version(conn, lock=True)
            version_id = version['id']
        else:
            version_id = previous['delegation_version_id']
        try:
            with conn.begin_nested():
                run_id = conn.execute(runs.insert().values(
                    case_id=previous['case_id'], status=Status.QUEUED, owner=actor,
                    delegation_version_id=version_id, previous_run_id=identity, created_at=utc_time(),
                )).inserted_primary_key[0]
        except IntegrityError:
            # Two retries raced; the unique previous_run_id keeps exactly one.
            replay = conn.execute(select(runs.c.id).where(runs.c.previous_run_id == identity)).first()
            if not replay:
                raise
            return found(conn, runs, replay.id)
        log(conn, actor, 'retry', run_id)
        result = found(conn, runs, run_id)
    _wake()
    return result


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id, runs.c.delegation_version_id).where(ready)
                           .order_by(runs.c.id).limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        payload = found(conn, cases, run.case_id)['payload']
        version = corrections.version_by_id(conn, run.delegation_version_id) if run.delegation_version_id else None
        return run.id, token, payload, version


def finish(run_id, token, result=None, error=None, version=None):
    if result is not None and version:
        result = {**result, 'delegation_version': {
            'version_no': version['version_no'],
            'digest': version['digest'],
            'source': version['source'],
            'source_ref': version['source_ref'],
            'published_at': version['published_at'],
        }}
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error)).rowcount
        if not count:
            return
        if result is not None:
            conn.execute(evidence.insert().values(
                run_id=run_id, report=result, digest=digest,
                delegation_version_id=version['id'] if version else None,
                delegation_version_no=version['version_no'] if version else None,
                delegation_digest=version['digest'] if version else None,
            ))
        log(conn, 'worker', status.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)

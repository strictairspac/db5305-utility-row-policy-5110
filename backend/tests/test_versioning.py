"""End-to-end checks for delegation versioning and correction publication.

Run against a PostgreSQL 16 database with:
    GB5305_TEST_DATABASE_URL=postgresql+psycopg://app@127.0.0.1:55432/workflow pytest
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from src.config.settings import engine
from src.models.tables import (audit, cases, corrections, delegation_ledger,
                               delegation_versions, evidence, runs, schema)
from src.services import corrections as service
from src.services import workflow
from src.services.domain import SPEC

pytestmark = pytest.mark.skipif(
    not __import__('os').getenv('GB5305_TEST_DATABASE_URL'),
    reason='GB5305_TEST_DATABASE_URL is not configured')


@pytest.fixture()
def db():
    schema.create_all(engine)
    with engine.begin() as conn:
        for table in (evidence, runs, corrections, cases, audit):
            conn.execute(table.delete())
        conn.execute(delegation_ledger.delete())
        conn.execute(delegation_versions.delete())
        service.ensure_baseline(conn, SPEC['sample']['delegations'])
    yield engine
    with engine.begin() as conn:
        for table in (evidence, runs, corrections, cases, audit):
            conn.execute(table.delete())
        conn.execute(delegation_ledger.delete())
        conn.execute(delegation_versions.delete())


def version_count():
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(delegation_versions)).scalar_one()


def current():
    with engine.connect() as conn:
        return service.published_version(conn)


def submit(kind, revoke=(), reassign=(), handover=None, request_id=None):
    return service.record(kind, handover, list(revoke), list(reassign), 'tester', request_id)


def revoke(zone='南堤', operator='BD-017'):
    return {'operator_id': operator, 'zone': zone, 'utility': '水表'}


def extension(zone='南堤', operator='BD-026', start='2026-11-21T08:00:00+08:00',
              end='2026-11-21T12:00:00+08:00'):
    return {'operator_id': operator, 'zone': zone, 'utility': '水表',
            'valid_from': start, 'valid_until': end}


def insert_case(slug):
    with engine.begin() as conn:
        return conn.execute(cases.insert().values(
            slug=slug, title=slug, payload=SPEC['sample'], author='tester',
            created_at='2026-11-21T08:00:00+00:00')).inserted_primary_key[0]


def test_urgent_revoke_publishes_on_its_own(db):
    before = version_count()
    correction, version = submit('urgent_revoke', revoke=[revoke()])
    assert correction['status'] == 'published'
    assert version['version_no'] == before + 1
    assert version_count() == before + 1
    assert all(not (row['operator_id'] == 'BD-017' and row['zone'] == '南堤')
               for row in version['snapshot'])


def test_handover_waits_for_both_parts(db):
    before = version_count()
    pending, version = submit('handover', revoke=[revoke()], handover='HO-1')
    assert pending['status'] == 'pending' and version is None
    assert version_count() == before
    # The pending revocation must not leak into the live version.
    assert any(row['operator_id'] == 'BD-017' and row['zone'] == '南堤'
               for row in current()['snapshot'])

    published, version = submit('handover', reassign=[extension()], handover='HO-1')
    assert published['status'] == 'published'
    assert version_count() == before + 1
    rows = version['snapshot']
    assert not any(row['operator_id'] == 'BD-017' and row['zone'] == '南堤' for row in rows)
    assert any(row['operator_id'] == 'BD-026' and row['zone'] == '南堤'
               and row['valid_until'].startswith('2026-11-21T12:00') for row in rows)


def test_handover_fragment_order_does_not_matter(db):
    before = version_count()
    submit('handover', reassign=[extension()], handover='HO-2')
    assert version_count() == before
    _, version = submit('handover', revoke=[revoke()], handover='HO-2')
    assert version_count() == before + 1
    assert version is not None


def test_urgent_revoke_cannot_carry_reassignment(db):
    before = version_count()
    with pytest.raises(ValueError):
        submit('urgent_revoke', revoke=[revoke()], reassign=[extension()])
    assert version_count() == before


def test_retry_of_published_correction_does_not_republish(db):
    before = version_count()
    first, version = submit('urgent_revoke', revoke=[revoke()])
    second, again = submit('urgent_revoke', revoke=[revoke()])
    assert second['id'] == first['id']
    assert again['id'] == version['id']
    assert version_count() == before + 1


def test_explicit_request_id_makes_retry_idempotent(db):
    before = version_count()
    first, _ = submit('urgent_revoke', revoke=[revoke()], request_id='msg-1')
    again, _ = submit('urgent_revoke', revoke=[revoke()], request_id='msg-1')
    assert again['id'] == first['id']
    assert version_count() == before + 1


def test_failed_publication_keeps_previous_version(db):
    before = current()
    count = version_count()
    with pytest.raises(ValueError):
        submit('urgent_revoke', revoke=[revoke(operator='BD-999')])
    assert version_count() == count
    now = current()
    assert now['id'] == before['id'] and now['digest'] == before['digest']
    assert now['snapshot'] == before['snapshot']


def test_unpublished_fragment_stays_pending(db):
    correction, version = submit('handover', reassign=[extension()], handover='HO-3')
    assert correction['status'] == 'pending' and version is None
    assert correction['published_at'] is None


def test_enqueue_pins_published_version(db):
    case_id = insert_case('pin')
    first = workflow.enqueue(case_id, 'tester')
    pinned = first['delegation_version_id']
    assert pinned == current()['id']
    # Publish a correction while the run is still queued: the queued run keeps
    # the version it pinned, and a verification queued afterwards pins the new one.
    submit('urgent_revoke', revoke=[revoke()])
    assert current()['id'] != pinned
    with engine.connect() as conn:
        still = conn.execute(select(runs.c.delegation_version_id).where(
            runs.c.id == first['id'])).scalar_one()
    assert still == pinned
    second = workflow.enqueue(case_id, 'tester')
    assert second['delegation_version_id'] == current()['id']


def test_retry_reuses_pinned_version_and_is_idempotent(db):
    case_id = insert_case('retry')
    run = workflow.enqueue(case_id, 'tester')
    with engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.id == run['id']).values(
            status='failed', error='boom', started_at='2026-11-21T08:00:00+00:00'))
    submit('urgent_revoke', revoke=[revoke()])
    first = workflow.retry(run['id'], 'tester')
    assert first['delegation_version_id'] == run['delegation_version_id']
    again = workflow.retry(run['id'], 'tester')
    assert again['id'] == first['id']


def claim_next():
    """Claim the oldest queued run, as the worker does."""
    job = workflow.claim()
    assert job is not None
    return job


def test_evidence_records_actual_version(db):
    case_id = insert_case('evidence')
    run = workflow.enqueue(case_id, 'tester')
    run_id, token, _, version = claim_next()
    workflow.finish(run_id, token,
                    result={'summary': 'ok', 'metrics': {}, 'sections': []}, version=version)
    with engine.connect() as conn:
        row = conn.execute(select(evidence)).mappings().one()
    assert row['delegation_version_id'] == version['id']
    assert row['delegation_version_no'] == version['version_no']
    assert row['delegation_digest'] == version['digest']
    assert row['report']['delegation_version']['version_no'] == version['version_no']
    assert run['delegation_version_id'] == version['id']


def test_worker_evaluates_the_pinned_snapshot(db):
    """A correction published after queueing must not change the run's input."""
    case_id = insert_case('snapshot')
    workflow.enqueue(case_id, 'tester')
    submit('urgent_revoke', revoke=[revoke()])
    _, _, _, version = claim_next()
    assert any(row['operator_id'] == 'BD-017' and row['zone'] == '南堤'
               for row in version['snapshot'])


def test_earlier_evidence_stays_readable(db):
    case_id = insert_case('old')
    workflow.enqueue(case_id, 'tester')
    run_id, token, _, version = claim_next()
    workflow.finish(run_id, token,
                    result={'summary': 'first', 'metrics': {}, 'sections': []}, version=version)
    submit('urgent_revoke', revoke=[revoke()])
    with engine.connect() as conn:
        row = conn.execute(select(evidence)).mappings().one()
        stored = service.version_by_id(conn, row['delegation_version_id'])
    assert stored['version_no'] == version['version_no']
    assert stored['snapshot'] == version['snapshot']


def test_import_snapshot_is_idempotent_and_keeps_history(db):
    before = current()
    snapshot = [row for row in before['snapshot'] if not (
        row['operator_id'] == 'BD-017' and row['zone'] == '南堤')]
    version, created = service.import_snapshot(snapshot, 'tester', 'corrected-export')
    assert created and version['version_no'] == before['version_no'] + 1

    again, created = service.import_snapshot(snapshot, 'tester', 'corrected-export')
    assert not created and again['id'] == version['id']
    assert version_count() == version['version_no']

    # The replaced version is still readable for evidence issued against it.
    with __import__('src.config.settings', fromlist=['engine']).engine.connect() as conn:
        old = service.version_by_id(conn, before['id'])
    assert old['digest'] == before['digest']
    assert old['snapshot'] == before['snapshot']


def test_conflicting_handover_fragments_are_rejected(db):
    before = version_count()
    submit('handover', revoke=[revoke()], handover='HO-X')
    submit('handover', reassign=[extension(end='2026-11-21T12:00:00+08:00')], handover='HO-X')
    with pytest.raises(ValueError):
        submit('handover', reassign=[extension(end='2026-11-21T13:00:00+08:00')], handover='HO-X')
    assert version_count() == before + 1  # only the completed handover published


def test_same_text_after_effect_expired_is_a_new_instruction(db):
    """A retry is recognised only while its effect is still live.

    Re-issuing the same revocation after the delegation was re-added is a new
    instruction, not a duplicate, so it must publish instead of being swallowed.
    """
    first, version = submit('urgent_revoke', revoke=[revoke()])
    # Re-add the delegation through a legitimate import, then revoke again.
    snapshot = list(version['snapshot']) + [{
        'operator_id': 'BD-017', 'zone': '南堤', 'utility': '水表',
        'valid_from': '2026-11-21T08:00:00+08:00', 'valid_until': '2026-11-21T10:00:00+08:00'}]
    service.import_snapshot(snapshot, 'tester', 're-added')
    second, version2 = submit('urgent_revoke', revoke=[revoke()])
    assert second['id'] != first['id']
    assert version2['version_no'] > version['version_no']
    assert all(not (row['operator_id'] == 'BD-017' and row['zone'] == '南堤')
               for row in version2['snapshot'])


def test_case_form_with_stale_base_version_is_refused(db):
    """A form opened before a correction was published must not roll it back."""
    base = current()
    submit('urgent_revoke', revoke=[revoke()])
    with pytest.raises(HTTPException) as failure:
        workflow.make_case('stale', {**SPEC['sample'], 'delegations': base['snapshot']},
                           'tester', base_version_id=base['id'])
    assert failure.value.status_code == 422
    assert current()['id'] != base['id']
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(cases)).scalar_one() == 0
    # Submitting against the live version with the live snapshot is a no-op.
    live = current()
    case = workflow.make_case('fresh', {**SPEC['sample'], 'delegations': live['snapshot']},
                              'tester', base_version_id=live['id'])
    assert case['id'] and current()['id'] == live['id']

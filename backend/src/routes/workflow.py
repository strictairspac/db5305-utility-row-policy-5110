from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download
from src.models.tables import audit, cases, evidence, runs
from src.schemas.workflow import (CaseForm, CorrectionForm, LoginForm, ReviewForm,
                                  SnapshotForm)
from src.services import corrections, domain, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


def _correction(correction, version):
    result = {**correction, 'version': version}
    result.pop('request_key', None)
    return result


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return domain.SPEC


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    try:
        return workflow.make_case(form.title, form.payload, actor, form.base_version_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.post('/runs/{identity}/retry', status_code=202)
def retry_run(identity: int, actor=Depends(operator)):
    return workflow.retry(identity, actor)


@router.get('/delegation-versions')
def list_delegation_versions(_=Depends(current_user)):
    with engine.connect() as conn:
        versions = corrections.list_versions(conn)
        current = corrections.published_version(conn)
        for version in versions:
            # The snapshot is large; the list only needs its identity.
            version.pop('snapshot', None)
        return {'current_version_id': current['id'] if current else None,
                'current_version_no': current['version_no'] if current else None,
                'versions': versions}


@router.get('/delegation-versions/current')
def current_delegation_version(_=Depends(current_user)):
    with engine.connect() as conn:
        version = corrections.published_version(conn)
    if not version:
        raise HTTPException(404, '尚无已发布的委派版本')
    return {key: version[key] for key in
            ('id', 'version_no', 'digest', 'source', 'source_ref', 'published_at', 'snapshot')}


@router.post('/delegation-versions')
def import_delegation_version(form: SnapshotForm, actor=Depends(operator)):
    try:
        version, created = corrections.import_snapshot(
            [row.model_dump() for row in form.delegations], actor, form.reference)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {'created': created, 'version': version}


@router.get('/corrections')
def list_corrections(_=Depends(current_user)):
    with engine.connect() as conn:
        return corrections.list_corrections(conn)


@router.post('/corrections', status_code=201)
def submit_correction(form: CorrectionForm, actor=Depends(operator)):
    try:
        correction, version = corrections.record(
            form.kind, form.handover_ref,
            [row.model_dump() for row in form.revoke],
            [row.model_dump() for row in form.reassign], actor, form.request_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _correction(correction, version)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)

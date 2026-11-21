from sqlalchemy import (JSON, Column, ForeignKey, Integer, MetaData, String,
                        Table, Text, UniqueConstraint)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('delegation_version_id', ForeignKey('delegation_versions.id'), index=True),
    Column('previous_run_id', ForeignKey('business_runs.id'), unique=True),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('delegation_version_id', ForeignKey('delegation_versions.id'), index=True),
    Column('delegation_version_no', Integer),
    Column('delegation_digest', String(64)),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))
# One row serialises every publication and records which version new
# verifications must pin.  Locking this row makes "pin the published version at
# queue time" and "publish a new version atomically" race-free.
delegation_ledger = Table('delegation_ledger', schema,
    Column('id', Integer, primary_key=True),
    Column('current_version_id', ForeignKey('delegation_versions.id'), nullable=False))
delegation_versions = Table('delegation_versions', schema,
    Column('id', Integer, primary_key=True),
    Column('version_no', Integer, nullable=False, unique=True),
    Column('snapshot', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('status', String(20), nullable=False, index=True),
    Column('source', String(20), nullable=False),
    Column('source_ref', String(80)),
    Column('created_at', String(32), nullable=False),
    Column('published_at', String(32)))
corrections = Table('dispatch_corrections', schema,
    Column('id', Integer, primary_key=True),
    Column('request_key', String(64), nullable=False, unique=True),
    Column('kind', String(20), nullable=False, index=True),
    Column('handover_ref', String(64), index=True),
    Column('revoke', JSON, nullable=False),
    Column('reassign', JSON, nullable=False),
    Column('status', String(20), nullable=False, index=True),
    Column('version_id', ForeignKey('delegation_versions.id')),
    Column('actor', String(40), nullable=False),
    Column('created_at', String(32), nullable=False),
    Column('published_at', String(32)))

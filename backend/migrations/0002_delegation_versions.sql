-- 0002: immutable delegation versions, the publication ledger, and dispatch
-- corrections.  Apply once to an existing deployment before rolling out the
-- matching application build; a fresh install gets the same shape from
-- `schema.create_all` and does not need this file.
--
-- Every statement is guarded so the migration is safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS delegation_versions (
    id           serial PRIMARY KEY,
    version_no   integer NOT NULL UNIQUE,
    snapshot     json    NOT NULL,
    digest       varchar(64) NOT NULL,
    status       varchar(20) NOT NULL,
    source       varchar(20) NOT NULL,
    source_ref   varchar(80),
    created_at   varchar(32) NOT NULL,
    published_at varchar(32)
);
CREATE INDEX IF NOT EXISTS ix_delegation_versions_status ON delegation_versions (status);

CREATE TABLE IF NOT EXISTS delegation_ledger (
    id                 serial PRIMARY KEY,
    current_version_id integer NOT NULL REFERENCES delegation_versions (id)
);

CREATE TABLE IF NOT EXISTS dispatch_corrections (
    id            serial PRIMARY KEY,
    request_key   varchar(64) NOT NULL UNIQUE,
    kind          varchar(20) NOT NULL,
    handover_ref  varchar(64),
    revoke        json NOT NULL,
    reassign      json NOT NULL,
    status        varchar(20) NOT NULL,
    version_id    integer REFERENCES delegation_versions (id),
    actor         varchar(40) NOT NULL,
    created_at    varchar(32) NOT NULL,
    published_at  varchar(32)
);
CREATE INDEX IF NOT EXISTS ix_dispatch_corrections_kind ON dispatch_corrections (kind);
CREATE INDEX IF NOT EXISTS ix_dispatch_corrections_handover_ref ON dispatch_corrections (handover_ref);
CREATE INDEX IF NOT EXISTS ix_dispatch_corrections_status ON dispatch_corrections (status);

ALTER TABLE business_runs
    ADD COLUMN IF NOT EXISTS delegation_version_id integer REFERENCES delegation_versions (id),
    ADD COLUMN IF NOT EXISTS previous_run_id integer UNIQUE REFERENCES business_runs (id);
CREATE INDEX IF NOT EXISTS ix_business_runs_delegation_version_id
    ON business_runs (delegation_version_id);

ALTER TABLE business_evidence
    ADD COLUMN IF NOT EXISTS delegation_version_id integer REFERENCES delegation_versions (id),
    ADD COLUMN IF NOT EXISTS delegation_version_no integer,
    ADD COLUMN IF NOT EXISTS delegation_digest varchar(64);
CREATE INDEX IF NOT EXISTS ix_business_evidence_delegation_version_id
    ON business_evidence (delegation_version_id);

COMMIT;

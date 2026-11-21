from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CaseForm(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]
    # The published version the operator's form was based on.  When it has
    # moved on, the server refuses instead of importing a stale export.
    base_version_id: int | None = None


class ReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class DelegationForm(BaseModel):
    operator_id: str
    zone: str
    utility: Literal['水表', '电表']
    valid_from: str
    valid_until: str


class RevokeForm(BaseModel):
    operator_id: str
    zone: str
    utility: Literal['水表', '电表']


class CorrectionForm(BaseModel):
    kind: Literal['urgent_revoke', 'handover']
    handover_ref: str | None = None
    revoke: list[RevokeForm] = Field(default_factory=list)
    reassign: list[DelegationForm] = Field(default_factory=list)
    request_id: str | None = Field(default=None, max_length=64)


class SnapshotForm(BaseModel):
    delegations: list[DelegationForm]
    reference: str | None = Field(default=None, max_length=80)

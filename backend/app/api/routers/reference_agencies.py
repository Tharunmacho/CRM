"""Reference agencies: agencies that refer candidates. Same card as a client (contact person, agency name, phone, email,
address, state) plus an optional list of people on the agency's own team, the sectors it specialises in and its preferred countries
(fixed sets, stored as lists; migration 0033).

Any signed-in user may add, change or remove one, exactly as any signed-in user may create a client; nothing else
points at an agency, so removing one cannot orphan a record. Every change is written to the audit trail
(entity type `reference_agency`) and announced on the live topic `reference_agencies`.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.data import MAX_STAFF_CONTACTS, MasterInput, StaffContactIn, _master_row, _master_save
from app.api.schemas import ReferenceAgencyOut
from app.core.deps import current_user
from app.db.models import AuditEntry, ReferenceAgency, User
from app.db.session import get_db

router = APIRouter(prefix="/api/reference-agencies", tags=["reference agencies"])


ALL_SECTORS = "All sectors"
SPECIALISATIONS = ("Oil and gas", "Hospitality", "Construction", "General worker", "Medical", ALL_SECTORS)
PREFERRED_COUNTRIES = ("Singapore", "Malaysia", "Europe", "Gulf", "Others")
Specialisation = Literal["Oil and gas", "Hospitality", "Construction", "General worker", "Medical", "All sectors"]
PreferredCountry = Literal["Singapore", "Malaysia", "Europe", "Gulf", "Others"]


class ReferenceAgencyIn(MasterInput):
    # The contact person.
    name: str = Field(min_length=1, max_length=200)
    agencyName: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=2000)
    state: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20000)
    staffContacts: list[StaffContactIn] = Field(default_factory=list, max_length=MAX_STAFF_CONTACTS)
    specialisations: list[Specialisation] = Field(default_factory=list, max_length=len(SPECIALISATIONS) * 2)
    preferredCountries: list[PreferredCountry] = Field(default_factory=list, max_length=len(PREFERRED_COUNTRIES) * 2)

    @field_validator("specialisations")
    @classmethod
    def sectors_in_order(cls, values):
        """Distinct, in the order the form lists them. "All sectors" means every sector, so it stands alone."""
        if ALL_SECTORS in values:
            return [ALL_SECTORS]
        return [name for name in SPECIALISATIONS if name in values]

    @field_validator("preferredCountries")
    @classmethod
    def countries_in_order(cls, values):
        return [name for name in PREFERRED_COUNTRIES if name in values]


@router.get("")
def list_reference_agencies(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Every reference agency, newest first."""
    rows = db.scalars(select(ReferenceAgency).order_by(ReferenceAgency.created_at.desc(), ReferenceAgency.id.desc())).all()
    return {"items": [ReferenceAgencyOut.model_validate(row).model_dump(by_alias=True, mode="json") for row in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_reference_agency(body: ReferenceAgencyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _master_save(db, user, ReferenceAgency(), body, "reference_agency", ReferenceAgencyOut, new=True)


@router.put("/{agency_id}")
def update_reference_agency(agency_id: str, body: ReferenceAgencyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _master_save(db, user, _master_row(db, ReferenceAgency, agency_id), body, "reference_agency", ReferenceAgencyOut)


@router.delete("/{agency_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference_agency(agency_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _master_row(db, ReferenceAgency, agency_id)
    db.add(AuditEntry(actor_id=user.id, action="Deleted reference agency", target=row.agency_name,
                      entity_type="reference_agency", entity_id=row.id))
    db.delete(row)
    db.commit()

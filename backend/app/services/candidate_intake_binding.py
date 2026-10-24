"""Bind a saved document intake to one candidate in the candidate transaction."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Candidate, DocumentExtractionJob, Role


def lock_intake_group(db, group_id, user_id):
    if group_id is None:
        return []
    jobs = list(db.scalars(select(DocumentExtractionJob).where(
        DocumentExtractionJob.group_id == str(group_id), DocumentExtractionJob.owner_id == user_id,
    ).order_by(DocumentExtractionJob.id).with_for_update()))
    if not jobs:
        raise HTTPException(404, "We could not find the saved upload. Open it again before you save.")
    from app.services.document_person_split import ensure_single_person_source
    ensure_single_person_source(db, "manual", group_id)
    return jobs


def saved_candidate_id(jobs):
    identifiers = {identifier for job in jobs for identifier in (
        job.candidate_id, (job.draft or {}).get("savedCandidateId"),
    ) if identifier}
    if len(identifiers) > 1:
        raise HTTPException(409, "This upload points to different candidates. Check the saved files before you continue.")
    return next(iter(identifiers), None)


def ensure_intake_candidate_access(candidate, user):
    if user.role == Role.staff and candidate.owner_id != user.id and candidate.created_by != user.id:
        raise HTTPException(403, "Only the person who added the candidate, the assigned recruiter, a manager or an admin can open this saved candidate.")


def saved_candidate(db, jobs, user):
    identifier = saved_candidate_id(jobs)
    if not identifier:
        return None
    candidate = db.get(Candidate, identifier)
    if candidate is None:
        raise HTTPException(409, "The saved candidate is no longer there. Ask a manager or admin to check this upload.")
    ensure_intake_candidate_access(candidate, user)
    return candidate


def bind_intake(jobs, candidate_id):
    previous = saved_candidate_id(jobs)
    if previous and previous != candidate_id:
        raise HTTPException(409, "These documents already belong to another saved candidate.")
    for job in jobs:
        job.draft = {**deepcopy(job.draft or {}), "savedCandidateId": candidate_id}

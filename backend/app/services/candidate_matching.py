"""Order matching exposes recorded evidence; it never guesses candidate suitability."""
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import Designation, Order
from app.services.candidate_identity import normalized_labels
from app.services.candidate_search import Search, search_candidates


def match_candidates(db, order_id, filters: Search, page, page_size):
    found = db.execute(select(Order, Designation).join(Designation, Designation.id == Order.designation_id).where(Order.id == order_id)).first()  # one statement
    if found is None:
        raise HTTPException(404, "Order not found.")
    order, designation = found
    required = normalized_labels(designation.required_skills)
    # These conditions mirror the application mapping transaction. Eligibility is
    # rechecked under row locks when a user actually maps a candidate.
    filters.matchable = True
    result = search_candidates(db, filters, page, page_size)
    for candidate in result["items"]:
        recorded = {label.lower() for label in normalized_labels(candidate["skills"])}
        candidate["matchEvidence"] = {
            "matchedSkills": [skill for skill in required if skill.lower() in recorded],
            "missingSkills": [skill for skill in required if skill.lower() not in recorded],
            "meetsExperience": candidate["totalExperience"] >= designation.min_experience,
        }
    result["requirements"] = {"title": designation.title, "skills": required,
                              "minExperience": designation.min_experience, "orderStatus": order.status.value}
    return result

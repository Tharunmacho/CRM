"""Agency names shared by reports and candidate search, without intake provenance."""
from sqlalchemy import and_, case, exists, func, select

from app.db.models import Candidate, ReferenceAgency, Source

MACHINE_SOURCE_DETAIL_PATTERN = r"^(email_message|whatsapp_message|person_review|lead_sheet):"


def agency_display_expression(candidate_model=Candidate):
    """The assignment form's agency value as one correlated SQL expression.

    A candidate may arrive by email and later acquire a referring agency, so the
    intake channel does not decide whether a real agency name is displayed.
    "Bulk import" is provenance unless explicitly entered as a referral or as a
    registered agency name. The EXISTS lookup keeps the surrounding query bounded.
    """
    whitespace = " \t\n\r\f\v"
    detail = func.btrim(func.coalesce(candidate_model.source_detail, ""), whitespace)
    registered_name = func.btrim(func.coalesce(
        func.nullif(ReferenceAgency.agency_name, ""), ReferenceAgency.name, ""), whitespace)
    registered = exists(select(1).select_from(ReferenceAgency).where(
        func.lower(registered_name) == func.lower(detail),
    ).correlate(candidate_model))
    import_marker = and_(
        candidate_model.source != Source.referral,
        func.lower(func.regexp_replace(detail, r"\s+", " ", "g")) == "bulk import",
        ~registered,
    )
    return case(
        (detail.op("~")(MACHINE_SOURCE_DETAIL_PATTERN), None),
        (import_marker, None),
        else_=func.nullif(detail, ""),
    )

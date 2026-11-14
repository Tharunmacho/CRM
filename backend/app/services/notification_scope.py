"""Which stored notifications the bell shows.

The table keeps everything every producer ever wrote; nothing is deleted or rewritten. The bell (the list, the
unread badge and the workspace snapshot) reads only the notices a person has to look at:

  shown
    * ``candidate_request``     a staff member asks the admins for a candidate (links to the candidate)
    * WhatsApp intake           "Candidate received by WhatsApp" (kind ``assignment``), shown to everyone it was sent to
    * email intake              "Candidate received by email" (kind ``candidate_assignment``), and the
                                ownerless "needs assignment" notices, which are the admin's or manager's to act on
    * ``lead_assignment``       leads newly handed to the recipient
    * ``assignment``            a candidate or task newly assigned to the recipient. An administrator also receives a
                                copy of every assignment made to somebody else; that copy is information only and
                                stays hidden unless the administrator is the one it was assigned to
    * ``candidate_assignment``  "Candidate assigned" written for a hand-created candidate: shown to its owner (any
                                non-admin recipient), hidden from administrators, who got a copy of every creation
    * ``todo``                  the person's own to-do reminder when it falls due

  hidden (still stored, never listed or counted)
    ``attendance``, ``job``, ``daily_work``, ``feedback``, ``escalation``, ``reminder``, ``missed_update`` and any
    kind added later: a new producer is quiet until it is added here on purpose.
"""
from __future__ import annotations

from sqlalchemy import and_, literal, or_, select

from app.db.models import Notification, Role, User
from app.services.candidate_review import WAITING_TITLE

REQUEST_KIND = "candidate_request"
EMAIL_INTAKE_TITLE = "Candidate received by email"
WHATSAPP_INTAKE_TITLE = "Candidate received by WhatsApp"  # written with kind ``assignment``; the admin sees it like the e-mail one
NEEDS_ASSIGNMENT_TITLES = (WAITING_TITLE, "Candidate needs assignment")
ASSIGNMENT_KINDS = ("assignment", "candidate_assignment")
ALWAYS_SHOWN_KINDS = (REQUEST_KIND, "lead_assignment", "todo")


def visible_notifications(user_id: str):
    """SQL condition (for ``system_notifications`` rows addressed to ``user_id``) matching what the bell shows."""
    role = select(User.role).where(User.id == user_id).scalar_subquery()
    name = select(User.name).where(User.id == user_id).scalar_subquery()
    is_admin = role == Role.admin
    assigned_to_me = and_(
        Notification.kind == "assignment",
        Notification.detail.contains(literal("Assigned to ") + name + literal(".")),
    )
    return or_(
        Notification.kind.in_(ALWAYS_SHOWN_KINDS),
        and_(Notification.kind.in_(ASSIGNMENT_KINDS), Notification.title.in_(NEEDS_ASSIGNMENT_TITLES)),
        and_(Notification.kind == "candidate_assignment", Notification.title == EMAIL_INTAKE_TITLE),
        and_(Notification.kind == "assignment", Notification.title == WHATSAPP_INTAKE_TITLE),
        and_(Notification.kind == "assignment", ~is_admin),
        and_(Notification.kind == "candidate_assignment", Notification.title == "Candidate assigned", ~is_admin),
        and_(is_admin, assigned_to_me),
    )

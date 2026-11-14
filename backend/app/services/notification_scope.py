"""Which stored notifications the bell shows.

The table keeps everything every producer ever wrote; nothing is deleted or rewritten. The bell (the list, the
unread badge and the workspace snapshot) reads only the kinds below, each of which is addressed to the one person
who has to act on it (see ``assignment_notifications``): there are no copies of other people's events.

  shown
    * ``candidate_assigned``  a candidate newly handed to the recipient (by an admin, by intake, by a transfer)
    * ``candidate_removed``   a candidate moved away from the recipient to somebody else
    * ``candidate_waiting``   a candidate nobody could take: for the admins and managers to assign
    * ``candidate_request``   a recruiter asks the admins for a candidate
    * ``task_assigned`` / ``task_removed``   the same for a task that moved on its own
    * ``lead_assignment`` / ``lead_removed`` leads handed to / taken from the recipient
    * ``todo``                the person's own to-do reminder when it falls due

  hidden (still stored, never listed or counted)
    everything else, in particular the retired kinds: ``assignment`` and ``candidate_assignment`` (the old
    candidate notices, which also went to every admin), ``daily_work`` ("Daily task overdue", daily work no longer
    has deadlines), and the internal ``attendance``, ``job``, ``feedback``, ``escalation``, ``reminder``,
    ``missed_update``. A new producer is quiet until it is added here on purpose.
"""
from __future__ import annotations

from app.db.models import Notification

REQUEST_KIND = "candidate_request"
SHOWN_KINDS = (
    "candidate_assigned", "candidate_removed", "candidate_waiting", REQUEST_KIND,
    "task_assigned", "task_removed", "lead_assignment", "lead_removed", "todo",
)
RETIRED_KINDS = ("assignment", "candidate_assignment", "daily_work")


def visible_notifications(user_id: str):
    """SQL condition (for ``system_notifications`` rows addressed to ``user_id``) matching what the bell shows."""
    return Notification.kind.in_(SHOWN_KINDS)

"""One rule for money on a candidate: only the admin sees or changes the service fee, CGST/SGST, invoices and receipts.

Recruiters and managers never receive these figures, the invoice number or the invoice PDF. The server is the source of
truth; the screen only mirrors it (capability `view_invoices`). Issuing an invoice after an air-ticket upload is a
server-side automation that runs whoever uploaded the ticket, so it does not use these checks.
"""
from fastapi import Depends, HTTPException, status

from app.core.deps import current_user
from app.db.models import Role, User

INVOICE_DENIED = "Only the admin can see or change invoices."
INVOICE_SLOT = "invoice"


def can_see_invoices(user: User | None) -> bool:
    return user is not None and user.role == Role.admin


def ensure_can_see_invoices(user: User | None) -> None:
    if not can_see_invoices(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, INVOICE_DENIED)


def invoice_admin(user: User = Depends(current_user)) -> User:
    """Route dependency: signed in as the admin, otherwise a calm 403."""
    ensure_can_see_invoices(user)
    return user

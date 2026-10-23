"""Order-level guards shared by stage workflows; pure so they can be tested without a database."""


def reopen_blocker(order_status) -> str | None:
    """Why a candidate's stage cannot be reopened on an order, or None when it can.

    Closing an order requires that no application is active, so reopening a stage
    (which makes the application active again) on a closed order would break that rule."""
    value = getattr(order_status, "value", order_status)
    if value == "closed":
        return "This order is closed. Reopen the order before reopening a candidate's stage."
    return None

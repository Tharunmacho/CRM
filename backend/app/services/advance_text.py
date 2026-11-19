"""What the stage-8 advance reads like in the client's tracker sheet ("60k Collected", "10k collected/50k", "Pending/45k").

Pure functions, no database: they turn the facts of a stage-8 record into the words of the sheet's Advance cell and read
the record's status. The owner's style is kept exactly: no spaces around the slash, `k` for round thousands.

Stage 8 stores (in the stage record's JSON, `crm_application_stages.data`):
  advanceStatus       "collected" | "pending"   (records saved before it existed have none, see `advance_status`)
  amount              the advance collected, or the advance expected while the status is pending
  amountCurrency      its currency (the agency's default advance currency when none was chosen)
  serviceChargeTotal  the total service charge, optional, in the same currency
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

AdvanceStatus = Literal["collected", "pending"]


def _positive(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def advance_status(data: Mapping[str, Any] | None, amount: Any = None) -> AdvanceStatus | None:
    """'collected', 'pending' or None for a stage-8 record's data.

    The explicit `advanceStatus` wins. Older records have none: `collected` 'yes' (or any positive amount, the
    only thing the old stage kept) reads as Collected, `collected` 'no' with no amount as Pending, and a record with nothing is None.
    A record that ended with a stopping exit (the advance was not paid, the candidate declined ...) has no status.
    `amount` is the lifted amount column when the caller has it (it is the same figure as data['amount'])."""
    data = data or {}
    if data.get("stop"):
        return None
    explicit = data.get("advanceStatus")
    if explicit in ("collected", "pending"):
        return explicit
    if data.get("collected") == "yes" or _positive(amount if amount is not None else data.get("amount")) is not None:
        return "collected"   # an amount alone was always read as the advance collected (the Tasks screen and the reports still do)
    return "pending" if data.get("collected") == "no" else None


def short_amount(value: Any) -> str:
    """10000 -> '10k', 12500 -> '12.5k', 60000 -> '60k'; below 1000, or not a multiple of 100, the plain number ('500', '12345')."""
    number = _positive(value)
    if number is None:
        return ""
    if number >= 1000 and number % 100 == 0:
        thousands = (number / 1000).normalize()
        return f"{thousands:f}k"
    plain = number.quantize(Decimal("0.01")).normalize()
    return f"{plain:f}"


def format_advance(status: str | None, amount: Any, total: Any = None, currency: str | None = None,
                   default_currency: str | None = None) -> str | None:
    """The Advance cell.

    `amount` is the advance collected, or, when the status is pending, the advance expected. `total` is the total service
    charge. None when there is nothing to say (no status and no amount).

      collected, amount, total   '10k collected/50k'      pending, total            'Pending/45k'
      collected, amount          '60k Collected'          pending, expected only    'Pending/10k'
      collected, nothing known   'Collected'              pending, nothing known    'Pending'

    A status-less amount is read as collected (records from before the status existed). The currency code follows the whole
    text when it is not the agency's default advance currency ('60k Collected USD')."""
    paid = _positive(amount)
    whole = _positive(total)
    if status not in ("collected", "pending"):
        status = "collected" if paid is not None else None
    if status is None:
        return None
    if status == "pending":
        shown = whole if whole is not None else paid
        text, known = ("Pending", False) if shown is None else (f"Pending/{short_amount(shown)}", True)
    elif paid is not None and whole is not None:
        text, known = f"{short_amount(paid)} collected/{short_amount(whole)}", True
    elif paid is not None:
        text, known = f"{short_amount(paid)} Collected", True
    elif whole is not None:
        text, known = f"Collected/{short_amount(whole)}", True
    else:
        text, known = "Collected", False
    code = (currency or "").strip().upper()
    if known and code and code != (default_currency or "").strip().upper():
        text += f" {code}"
    return text


def advance_cell(fact: Mapping[str, Any] | None, default_currency: str | None = None) -> str | None:
    """The Advance cell from one application's entry of `daily_work.stage_facts` (None when it has no stage-8 record)."""
    if not fact:
        return None
    collected = fact.get("advance")
    status = fact.get("status")
    if collected:
        return format_advance(status or "collected", collected[0], fact.get("total"), collected[1], default_currency)
    return format_advance(status, fact.get("expected"), fact.get("total"), fact.get("currency"), default_currency)

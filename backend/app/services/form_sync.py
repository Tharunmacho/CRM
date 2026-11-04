"""Keep the application-form columns in step with the OCR output stored on a candidate.

Whatever path stores or changes `crm_candidates.ocr_data` (the Add Candidate save, a merge into a duplicate, the
e-mail and WhatsApp intake, the multi-person split), this hook reads the document(s) just stored and fills the form
columns the code that wrote them does not know about (father's name, address, education, work history, ...).
It follows the same rule as every other read: it fills only empty fields or ones still marked "extracted" and never
touches what a person entered (see `form_extraction`).

This module imports nothing heavy so `app.db.session` can install it at start-up like the change feed; the real work
is imported on first use. It must never fail a save, so every error is swallowed and logged.
"""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import PASSIVE_NO_INITIALIZE, get_history

log = logging.getLogger("form_sync")
_installed = False


def _before_flush(session: Session, flush_context, instances) -> None:
    try:
        candidates = [obj for obj in (*session.new, *session.dirty) if getattr(obj, "__tablename__", None) == "crm_candidates"]
        if not candidates:
            return
        from app.services import form_extraction
        for candidate in candidates:
            if not get_history(candidate, "ocr_data", passive=PASSIVE_NO_INITIALIZE).has_changes():
                continue
            if form_extraction.is_explicit(candidate):
                continue  # the caller already applied this document itself (form upload)
            with session.no_autoflush:
                form_extraction.apply_stored_ocr(candidate, session)
    except Exception:  # never fail the save that carried the document
        log.warning("Application-form sync skipped", exc_info=True)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    event.listen(Session, "before_flush", _before_flush)

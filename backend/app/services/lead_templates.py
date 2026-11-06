"""WhatsApp message templates of the Lead sheet.

The green WhatsApp action opens wa.me/<number>?text=<message>. The message starts from one of three templates (first contact,
request resume, follow-up) that the person can edit before sending. Placeholders: {name}, {position}, {company_name}; the screen
fills them for the lead (this module only stores and validates the text).

Storage needs no table of its own: the three defaults are constants below, and an administrator's wording is kept in the
admin-managed option table (`crm_form_options`, category `lead_wa_template`; value = template key, label = title, hint = message).
A template that was never edited, or whose row was removed ("Reset"), is the default. The category is deliberately not one of
`form_options.CATEGORIES`, so the candidate form never offers it and the migration-seed test is unaffected.
"""
from __future__ import annotations

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import FormOption

CATEGORY = "lead_wa_template"
PLACEHOLDERS = ("{name}", "{position}", "{company_name}")
TITLE_MAX, BODY_MAX = 120, 255  # crm_form_options.label / .hint

DEFAULTS: list[dict] = [
    {"key": "first_contact", "title": "First contact",
     "body": "Hello {name}, this is {company_name}. You showed interest in the {position} job abroad. Can we talk today about your details?"},
    {"key": "request_resume", "title": "Request resume",
     "body": "Hello {name}, thank you for speaking with us. Please send your updated resume (PDF or Word) for the {position} job to this number. - {company_name}"},
    {"key": "follow_up", "title": "Follow-up",
     "body": "Hello {name}, this is {company_name} following up about the {position} job. Are you still interested? Please reply and we will guide you."},
]
KEYS = tuple(item["key"] for item in DEFAULTS)
_UNKNOWN = re.compile(r"\{(?!name\}|position\}|company_name\})[^{}]*\}")


def clean(value) -> str:
    return " ".join(str(value or "").split())


def stored(db: Session) -> dict[str, FormOption]:
    return {row.value: row for row in db.scalars(select(FormOption).where(FormOption.category == CATEGORY, FormOption.value.in_(KEYS)))}


def templates(db: Session) -> list[dict]:
    """The three templates in order: the administrator's wording where there is one, else the default."""
    rows = stored(db)
    out = []
    for item in DEFAULTS:
        row = rows.get(item["key"])
        out.append({"key": item["key"], "title": row.label if row else item["title"], "body": (row.hint if row and row.hint else item["body"]),
                    "isDefault": row is None, "defaultBody": item["body"], "defaultTitle": item["title"]})
    return out


def validate(entry: dict) -> tuple[str, str, str]:
    key = entry.get("key")
    if key not in KEYS:
        raise HTTPException(422, f"Unknown template. Use one of: {', '.join(KEYS)}.")
    title, body = clean(entry.get("title")), clean(entry.get("body"))
    if not title or len(title) > TITLE_MAX:
        raise HTTPException(422, f"Give the template a title of up to {TITLE_MAX} characters.")
    if not body or len(body) > BODY_MAX:
        raise HTTPException(422, f"The message must be 1 to {BODY_MAX} characters.")
    unknown = _UNKNOWN.findall(body)
    if unknown:
        raise HTTPException(422, f"Unknown placeholder {unknown[0]}. You can use {', '.join(PLACEHOLDERS)}.")
    return key, title, body


def save(db: Session, entries: list[dict]) -> list[dict]:
    """Store the administrator's wording. An entry equal to the default, or `{"key": ..., "reset": true}`, removes the row. Does not commit."""
    rows = stored(db)
    defaults = {item["key"]: item for item in DEFAULTS}
    for entry in entries:
        if entry.get("reset") and entry.get("key") in KEYS:
            if entry["key"] in rows:
                db.execute(delete(FormOption).where(FormOption.id == rows[entry["key"]].id))
            continue
        key, title, body = validate(entry)
        default = defaults[key]
        row = rows.get(key)
        if title == default["title"] and body == default["body"]:
            if row is not None:
                db.execute(delete(FormOption).where(FormOption.id == row.id))
        elif row is None:
            db.add(FormOption(id=str(uuid.uuid4()), category=CATEGORY, value=key, label=title, hint=body, sort_order=KEYS.index(key) * 10 + 10,
                              is_active=True, is_system=False))
        else:
            row.label, row.hint = title, body
    db.flush()
    return templates(db)

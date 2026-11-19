"""Role names and the one rule for "admin level".

The owner's rule: the role that used to be shown as "Manager" is called "MD", and an MD has exactly the same
access as an Admin. The stored enum value stays `manager` (no data migration, every existing user keeps working,
the API keeps returning it); only the label changes. So:

- `ROLE_LABELS` is the ONE place that turns a stored role into the words a person reads (exports, emails,
  notifications, audit text). The frontend has the same table in `frontend/src/shared/roles.ts`.
- `is_admin_level(...)` is the ONE check behind every privilege an Admin has: true for admin and manager.
- `is_real_admin(...)` is only for "the system must always keep one real Admin" protections (last admin,
  self-demotion). It is deliberately NOT admin-level: an MD cannot be that last admin.
- `parse_role(...)` accepts the legacy value `manager` and the new word `md` (any case) and returns the stored enum.
"""

from __future__ import annotations

from app.db.models import Role

ROLE_LABELS: dict[str, str] = {
    Role.admin.value: "Admin",
    Role.manager.value: "MD",
    Role.staff.value: "Recruiter",
}

# What a role picker offers, in order.
ROLE_ORDER: tuple[Role, ...] = (Role.admin, Role.manager, Role.staff)

ADMIN_LEVEL_ROLES: tuple[Role, ...] = (Role.admin, Role.manager)
ADMIN_LEVEL_VALUES: frozenset[str] = frozenset(role.value for role in ADMIN_LEVEL_ROLES)


def role_value(user_or_role) -> str:
    """The stored string for a User, a Role, or a plain string ('' when there is none); `md` reads as `manager`."""
    role = getattr(user_or_role, "role", user_or_role)
    value = getattr(role, "value", role)
    text = str(value or "").strip().lower()
    return Role.manager.value if text == "md" else text


def role_label(user_or_role) -> str:
    value = role_value(user_or_role)
    return ROLE_LABELS.get(value, value.title())


def is_admin_level(user_or_role) -> bool:
    """True for an Admin and for an MD (stored as `manager`): everything an Admin may do, an MD may do."""
    return role_value(user_or_role) in ADMIN_LEVEL_VALUES


def is_real_admin(user_or_role) -> bool:
    """True only for role `admin`. Used solely to keep at least one real Admin in the system."""
    return role_value(user_or_role) == Role.admin.value


def parse_role(value) -> Role:
    """Accept `admin`, `manager`, `md` (any case) or `staff`; `md` is stored as `manager`."""
    if isinstance(value, Role):
        return value
    text = str(value or "").strip().lower()
    if text == "md":
        text = Role.manager.value
    return Role(text)

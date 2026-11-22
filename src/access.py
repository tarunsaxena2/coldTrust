"""Who may see what: role groups, site scoping and farmer phone matching (pure functions, easy to test)."""
from __future__ import annotations

ROLES = ("owner", "manager", "site_manager", "technician", "accountant", "viewer", "farmer")

# role groups used by the API guards
STAFF = ("owner", "manager", "site_manager")                                    # day-to-day work incl. adding maintenance tasks
VIEW = ("owner", "manager", "site_manager", "technician", "viewer")             # monitoring data: chambers, readings, alerts
LIST = VIEW + ("accountant",)                                                   # chamber / site lists (accountant needs them to register lots)
ACK = ("owner", "manager", "site_manager", "technician")                        # acknowledge alerts, mark maintenance done
READ = ("owner", "manager", "site_manager", "viewer", "accountant")             # lots, certificates, weekly report (read)
LOTS_WRITE = ("owner", "manager", "site_manager", "accountant")                 # register / withdraw lots, message farmers
HQ = ("owner", "manager")                                                       # head office: send the weekly WhatsApp summary
OWNER = ("owner",)
FARMER = ("farmer",)


def phone_key(phone: str | None) -> str:
    """Last 10 digits of a phone number, so '+91 98765 43210' and '9876543210' match. '' if fewer than 10 digits."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def visible_chamber_ids(chambers: dict, user: dict) -> list[int]:
    """Chamber ids this user may see. A user with a site_id only sees that site; everybody else sees all."""
    sid = user.get("site_id")
    return [cid for cid, c in chambers.items() if sid is None or c.get("site_id") == sid]


def chamber_allowed(chambers: dict, user: dict, cid: int) -> bool:
    """True if the chamber is unknown (the caller answers 404) or belongs to the user's scope."""
    if cid not in chambers:
        return True
    return cid in visible_chamber_ids(chambers, user)

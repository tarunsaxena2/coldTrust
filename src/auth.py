"""Minimal login + roles (no extra dependencies): PBKDF2 password hashes and opaque bearer tokens kept in SQLite.

Roles: owner (everything, incl. users, sites, simulator), manager (monitor, acknowledge, lots, maintenance, reports),
site_manager (same as manager but only for ONE site), technician (monitor, acknowledge, mark maintenance done),
accountant (lots, certificates, weekly report), viewer (read-only) and farmer (own lots only, matched by phone number).
The role rules live in src/access.py. Farmers can also skip the login: each lot has a long random link (/f/<token>) that shows only that lot.
Sensors authenticate with a shared device key (X-Device-Key header).
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import time

from src.access import ROLES, phone_key

ITER = 120_000


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITER).hex()


class Auth:
    def __init__(self, store, session_hours: float = 12.0):
        self.store, self.session_s = store, session_hours * 3600
        self.default_passwords_in_use = False
        if not self.store.query("SELECT id FROM users LIMIT 1"):
            op, mp = os.getenv("OWNER_PASSWORD"), os.getenv("MANAGER_PASSWORD")
            self.default_passwords_in_use = not (op and mp)
            self.create_user("owner", op or "owner123", "owner")
            self.create_user("manager", mp or "manager123", "manager")

    def create_user(self, username: str, password: str, role: str, site_id: int | None = None, phone: str | None = None) -> int:
        """Raises ValueError for invalid input (a duplicate username raises sqlite3.IntegrityError)."""
        if role not in ROLES:
            raise ValueError("role must be one of: " + ", ".join(ROLES))
        if len(password) < 6:
            raise ValueError("password must be at least 6 characters")
        if role == "site_manager":
            if site_id is None or not self.store.one("SELECT id FROM sites WHERE id=?", (site_id,)):
                raise ValueError("a site_manager needs a valid site_id")
        elif site_id is not None:
            raise ValueError("site_id is only for the site_manager role")
        if role == "farmer":
            key = phone_key(phone)
            if not key:
                raise ValueError("a farmer needs a phone number with at least 10 digits")
            if any(phone_key(u["phone"]) == key for u in self.store.query("SELECT phone FROM users WHERE role='farmer'")):
                raise ValueError("a farmer login already exists for this phone number")
            phone = key
        elif phone:
            raise ValueError("phone is only for the farmer role")
        salt = secrets.token_hex(16)
        return self.store.execute("INSERT INTO users(username,salt,pw_hash,role,created_ts,site_id,phone) VALUES(?,?,?,?,?,?,?)",
                                  (username.strip().lower(), salt, _hash(password, salt), role, time.time(), site_id, phone))

    def login(self, username: str, password: str):
        u = self.store.one("SELECT * FROM users WHERE username=?", (username.strip().lower(),))
        ok = bool(u) and hmac.compare_digest(_hash(password, u["salt"]), u["pw_hash"])
        if not ok:
            return None
        token = secrets.token_urlsafe(32)
        self.store.execute("INSERT INTO sessions(token,user_id,expires_ts) VALUES(?,?,?)", (token, u["id"], time.time() + self.session_s))
        return {"token": token, "username": u["username"], "role": u["role"], "site_id": u["site_id"]}

    def user_for_token(self, token: str):
        row = self.store.one("SELECT u.id,u.username,u.role,u.site_id,u.phone,s.expires_ts FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,))
        if not row or row["expires_ts"] < time.time():
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"], "site_id": row["site_id"], "phone": row["phone"]}

    def logout(self, token: str):
        self.store.execute("DELETE FROM sessions WHERE token=?", (token,))

    def change_password(self, user_id: int, old: str, new: str) -> bool:
        u = self.store.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not u or not hmac.compare_digest(_hash(old, u["salt"]), u["pw_hash"]) or len(new) < 6:
            return False
        salt = secrets.token_hex(16)
        self.store.execute("UPDATE users SET salt=?, pw_hash=? WHERE id=?", (salt, _hash(new, salt), user_id))
        self.store.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return True

    def list_users(self):
        return self.store.query("SELECT id,username,role,site_id,phone,created_ts FROM users ORDER BY id")

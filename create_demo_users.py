"""Create one demo login for every role.   python create_demo_users.py

Safe to run many times: existing usernames are skipped. Works on coldtrust.db (or COLDTRUST_DB).
Demo passwords only - change them before any real use (Admin page, or set strong passwords here)."""
import sqlite3
import config
from src.engine import Engine
from src.auth import Auth

# username, password, role, extra
DEMO = [
    ("owner",      "owner123",   "owner",        {}),
    ("manager",    "manager123", "manager",      {}),
    ("sitemgr",    "sitemgr123", "site_manager", {"site_id": 1}),            # sees only site 1 (Agra Cold Storage)
    ("tech",       "tech123",    "technician",   {}),
    ("accountant", "account123", "accountant",   {}),
    ("viewer",     "viewer123",  "viewer",       {}),
    ("farmer",     "farmer123",  "farmer",       {"phone": "+91 90000 55555"}),
]


def main():
    eng = Engine(db_path=config.DB_PATH, sim=False, backfill=0)      # creates the tables, demo sites and chambers if the database is new
    try:
        auth = Auth(eng.store)
        for username, password, role, extra in DEMO:
            if eng.store.one("SELECT id FROM users WHERE username=?", (username,)):
                print(f"exists   {username:<11} {role} (skipped)")
                continue
            try:
                auth.create_user(username, password, role, **extra)
                print(f"created  {username:<11} {role}")
            except sqlite3.IntegrityError:
                print(f"exists   {username:<11} {role} (skipped)")
            except ValueError as ex:
                print(f"skipped  {username:<11} {role}: {ex}")
        # give the demo farmer something to look at: one lot under the same phone number
        key = "9000055555"
        if not any("".join(ch for ch in l["farmer_phone"] if ch.isdigit())[-10:] == key for l in eng.store.query("SELECT farmer_phone FROM lots")):
            eng.add_lot(1, "Ramesh Kumar", "+91 90000 55555", 100)
            print("created  demo lot for farmer 'farmer' (Chamber 1, 100 bags)")
    finally:
        eng.stop()
    print("\nUsername     Password     Role")
    for username, password, role, _ in DEMO:
        print(f"{username:<12} {password:<12} {role}")


if __name__ == "__main__":
    main()

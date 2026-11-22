"""Role rules that need no web server: phone matching, site scoping, user validation, site-limited weekly report."""
import sqlite3
import pytest
from src import access
from src.auth import Auth
from src.db import Store
from src.engine import Engine


def test_phone_key_matches_different_formats():
    assert access.phone_key("+91 98765 43210") == access.phone_key("9876543210") == "9876543210"
    assert access.phone_key("098765-43210") == "9876543210"
    assert access.phone_key("12345") == ""
    assert access.phone_key(None) == ""


def test_visible_chambers_by_site():
    ch = {1: {"site_id": 1}, 2: {"site_id": 1}, 3: {"site_id": 2}}
    assert access.visible_chamber_ids(ch, {"role": "owner"}) == [1, 2, 3]
    assert access.visible_chamber_ids(ch, {"role": "site_manager", "site_id": 2}) == [3]
    assert access.chamber_allowed(ch, {"site_id": 1}, 2) is True
    assert access.chamber_allowed(ch, {"site_id": 1}, 3) is False
    assert access.chamber_allowed(ch, {"site_id": 1}, 99) is True      # unknown chamber: the endpoint answers 404


def test_role_groups_are_consistent():
    for g in (access.STAFF, access.VIEW, access.LIST, access.ACK, access.READ, access.LOTS_WRITE, access.HQ, access.OWNER, access.FARMER):
        assert set(g) <= set(access.ROLES)
    assert "farmer" not in access.LIST + access.READ + access.ACK + access.LOTS_WRITE        # farmers use /my/* only
    assert "accountant" not in access.VIEW + access.ACK                                       # accountants never touch alerts
    assert "viewer" not in access.ACK + access.LOTS_WRITE + access.STAFF                      # viewers never write


@pytest.fixture()
def auth(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    store.execute("INSERT INTO sites(id,name,location) VALUES(1,'Agra','Agra')")
    return Auth(store)


def test_create_users_for_every_role(auth):
    auth.create_user("sm", "secret1", "site_manager", site_id=1)
    auth.create_user("acc", "secret1", "accountant")
    auth.create_user("ram", "secret1", "farmer", phone="+91 98765 43210")
    login = auth.login("sm", "secret1")
    assert login["role"] == "site_manager" and login["site_id"] == 1
    u = auth.user_for_token(login["token"])
    assert u["site_id"] == 1 and u["phone"] is None
    f = auth.user_for_token(auth.login("ram", "secret1")["token"])
    assert f["role"] == "farmer" and f["phone"] == "9876543210" and f["site_id"] is None


@pytest.mark.parametrize("kwargs", [
    dict(role="site_manager"),                                   # needs a site
    dict(role="site_manager", site_id=99),                       # unknown site
    dict(role="technician", site_id=1),                          # site only for site_manager
    dict(role="farmer"),                                         # needs a phone
    dict(role="farmer", phone="12345"),                          # phone too short
    dict(role="viewer", phone="9876543210"),                     # phone only for farmer
    dict(role="boss"),                                           # unknown role
])
def test_invalid_user_combinations_rejected(auth, kwargs):
    with pytest.raises(ValueError):
        auth.create_user("someone", "secret1", **kwargs)


def test_duplicate_farmer_phone_and_username(auth):
    auth.create_user("ram", "secret1", "farmer", phone="9876543210")
    with pytest.raises(ValueError):
        auth.create_user("ram2", "secret1", "farmer", phone="+91 98765-43210")
    with pytest.raises(sqlite3.IntegrityError):
        auth.create_user("ram", "secret1", "viewer")


def test_old_database_without_new_user_columns_is_migrated(tmp_path):
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, salt TEXT, pw_hash TEXT, role TEXT, created_ts REAL)")
    con.commit()
    con.close()
    store = Store(p)
    cols = [r["name"] for r in store.query("PRAGMA table_info(users)")]
    assert "site_id" in cols and "phone" in cols


def test_weekly_report_can_be_limited_to_one_site(tmp_path):
    e = Engine(db_path=str(tmp_path / "r.db"), sim=False, backfill=0)
    try:
        everything = e.weekly_report_data(7)
        one_site = e.weekly_report_data(7, site_id=1)
        assert {c["id"] for c in everything["chambers"]} == {1, 2, 3}
        assert {c["id"] for c in one_site["chambers"]} == {1, 2}
        assert {m["chamber_id"] for m in one_site["maintenance"]} <= {1, 2}
        assert all(a["chamber_id"] in (1, 2) for a in one_site["alerts"])
    finally:
        e.stop()

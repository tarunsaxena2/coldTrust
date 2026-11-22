"""Technician and viewer roles: what each may and may not do."""
import pytest
from fastapi.testclient import TestClient
from api import create_app

T0 = 1_800_000_000


@pytest.fixture()
def secure(tmp_path):
    app = create_app(auth=True, db_path=str(tmp_path / "r.db"), sim=False, backfill=0, esc_wait=0.3)
    with TestClient(app) as c:
        yield c


def login(c, user, pw):
    r = c.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture()
def users(secure):
    c = secure
    owner, manager = login(c, "owner", "owner123"), login(c, "manager", "manager123")
    for name, role in (("tech1", "technician"), ("view1", "viewer")):
        assert c.post("/users", json={"username": name, "password": "secret1", "role": role}, headers=owner).status_code == 200
    # a lot, a maintenance task and a reading so there is something to read
    lot = c.post("/lots", json={"chamber_id": 1, "farmer_name": "Ramesh Kumar", "farmer_phone": "+91 90000 55555", "bags": 10}, headers=manager).json()["id"]
    c.post("/ingest", json={"chamber_id": 1, "temp_c": 3.0, "ts": T0}, headers={"X-Device-Key": "demo-device-key"})
    return {"owner": owner, "manager": manager, "tech": login(c, "tech1", "secret1"), "view": login(c, "view1", "secret1"), "lot": lot}


def test_invalid_role_rejected(secure):
    owner = login(secure, "owner", "owner123")
    r = secure.post("/users", json={"username": "x", "password": "secret1", "role": "boss"}, headers=owner)
    assert r.status_code == 422


def test_technician_permissions(secure, users):
    c, t, lot = secure, users["tech"], users["lot"]
    # allowed: monitoring, alerts, notifications, maintenance list and marking done
    for path in ("/chambers", "/chambers/1/readings", "/sites", "/alerts", "/notifications", "/maintenance", "/sim/status"):
        assert c.get(path, headers=t).status_code == 200, path
    mid = c.get("/maintenance", headers=t).json()[0]["id"]
    assert c.post(f"/maintenance/{mid}/done", headers=t).status_code == 200
    # not allowed: lots, certificates, reports, adding tasks, admin, simulator
    for path in ("/lots", f"/lots/{lot}/summary", f"/lots/{lot}/certificate", "/reports/weekly", "/users"):
        assert c.get(path, headers=t).status_code == 403, path
    assert c.post("/maintenance", json={"chamber_id": 1, "task": "x", "every_days": 5}, headers=t).status_code == 403
    assert c.post("/lots", json={"chamber_id": 1, "farmer_name": "A", "farmer_phone": "1", "bags": 1}, headers=t).status_code == 403
    assert c.post("/reports/weekly/notify", headers=t).status_code == 403
    assert c.post("/sim/scenario", json={"chamber_id": 1, "scenario": "reset"}, headers=t).status_code == 403
    assert c.post("/admin/tamper/1", headers=t).status_code == 403


def test_viewer_is_read_only(secure, users):
    c, v, lot = secure, users["view"], users["lot"]
    for path in ("/chambers", "/chambers/1/readings", "/sites", "/alerts", "/notifications", "/maintenance", "/lots",
                 f"/lots/{lot}/summary", f"/lots/{lot}/certificate", "/reports/weekly"):
        assert c.get(path, headers=v).status_code == 200, path
    # every write is refused
    mid = c.get("/maintenance", headers=v).json()[0]["id"]
    assert c.post(f"/maintenance/{mid}/done", headers=v).status_code == 403
    assert c.post("/alerts/1/ack", json={"who": "viewer"}, headers=v).status_code == 403
    assert c.post("/maintenance", json={"chamber_id": 1, "task": "x", "every_days": 5}, headers=v).status_code == 403
    assert c.post("/lots", json={"chamber_id": 1, "farmer_name": "A", "farmer_phone": "1", "bags": 1}, headers=v).status_code == 403
    assert c.post(f"/lots/{lot}/withdraw", headers=v).status_code == 403
    assert c.post(f"/lots/{lot}/farmer-update", headers=v).status_code == 403
    assert c.post("/reports/weekly/notify", headers=v).status_code == 403
    assert c.post("/sim/scenario", json={"chamber_id": 1, "scenario": "reset"}, headers=v).status_code == 403
    assert c.get("/users", headers=v).status_code == 403
    # sensors still need the device key, not a login
    assert c.post("/ingest", json={"chamber_id": 1, "temp_c": 3.0, "ts": T0 + 60}, headers=v).status_code == 401


def test_new_roles_can_change_own_password(secure, users):
    r = secure.post("/auth/change-password", json={"old_password": "secret1", "new_password": "newsecret1"}, headers=users["tech"])
    assert r.status_code == 200


# ---------------- accountant, site_manager, farmer ----------------
def feed(c, start, temps, chamber):
    for i, t in enumerate(temps):
        r = c.post("/ingest", json={"chamber_id": chamber, "temp_c": t, "humidity_pct": 90, "door_open": False, "compressor_on": True,
                                    "ts": start + 60 * i}, headers={"X-Device-Key": "demo-device-key"})
        assert r.status_code == 200


def normal(n=150, base=3.0):
    return [base + 0.4 * ((i % 24) / 12 - 1 if i % 24 < 12 else 1 - (i % 24 - 12) / 12) for i in range(n)]


@pytest.fixture()
def world(secure):
    """Sites: 1 (chambers 1,2) and 2 (chamber 3). One lot per site, one farmer login, an alert in chamber 3."""
    c = secure
    owner, manager = login(c, "owner", "owner123"), login(c, "manager", "manager123")
    mk = lambda name, role, **kw: c.post("/users", json={"username": name, "password": "secret1", "role": role, **kw}, headers=owner)
    assert mk("sm1", "site_manager", site_id=1).status_code == 200
    assert mk("acc1", "accountant").status_code == 200
    assert mk("ramesh", "farmer", phone="+91 90000 55555").status_code == 200
    lot1 = c.post("/lots", json={"chamber_id": 1, "farmer_name": "Ramesh Kumar", "farmer_phone": "+91 90000 55555", "bags": 10}, headers=manager).json()["id"]
    lot2 = c.post("/lots", json={"chamber_id": 1, "farmer_name": "Suresh Singh", "farmer_phone": "+91 98765 43210", "bags": 20}, headers=manager).json()["id"]
    lot3 = c.post("/lots", json={"chamber_id": 3, "farmer_name": "Mohan Lal", "farmer_phone": "+91 91111 22222", "bags": 30}, headers=manager).json()["id"]
    t0 = 1_800_000_000
    feed(c, t0, normal(base=5.0), 3)                                           # chamber 3 runs at 5 C (limit 7 C)
    feed(c, t0 + 150 * 60, [5.2 + 0.06 * k for k in range(60)], 3)            # temperature creeps past the limit
    task3 = c.post("/maintenance", json={"chamber_id": 3, "task": "Check gasket", "every_days": 30}, headers=owner).json()["id"]
    return {"owner": owner, "manager": manager, "sm": login(c, "sm1", "secret1"), "acc": login(c, "acc1", "secret1"),
            "farmer": login(c, "ramesh", "secret1"), "lot1": lot1, "lot2": lot2, "lot3": lot3, "task3": task3}


def test_create_user_validation_over_api(secure, world):
    c, o = secure, world["owner"]
    bad = [{"role": "site_manager"}, {"role": "site_manager", "site_id": 99}, {"role": "technician", "site_id": 1},
           {"role": "farmer"}, {"role": "farmer", "phone": "123"}, {"role": "farmer", "phone": "90000 55555"}]
    for i, extra in enumerate(bad):
        r = c.post("/users", json={"username": f"bad{i}", "password": "secret1", **extra}, headers=o)
        assert r.status_code == 422, (extra, r.text)


def test_site_manager_sees_only_own_site(secure, world):
    c, sm, mgr = secure, world["sm"], world["manager"]
    assert {x["id"] for x in c.get("/chambers", headers=sm).json()} == {1, 2}
    assert {x["id"] for x in c.get("/chambers", headers=mgr).json()} == {1, 2, 3}
    assert [s["id"] for s in c.get("/sites", headers=sm).json()] == [1]
    assert c.get("/chambers/1/readings", headers=sm).status_code == 200
    assert c.get("/chambers/3/readings", headers=sm).status_code == 403
    assert c.get("/chambers/3/spoilage", headers=sm).status_code == 403
    # alerts and notifications of the other site are hidden
    all_alerts = c.get("/alerts", headers=mgr).json()
    a3 = [a for a in all_alerts if a["chamber_id"] == 3]
    assert a3, "the fault in chamber 3 should have raised an alert"
    assert all(a["chamber_id"] in (1, 2) for a in c.get("/alerts", headers=sm).json())
    assert all(n["chamber_id"] in (1, 2) for n in c.get("/notifications", headers=sm).json())
    assert c.post(f"/alerts/{a3[0]['id']}/ack", json={"who": "sm1"}, headers=sm).status_code == 403
    assert c.post(f"/alerts/{a3[0]['id']}/ack", json={"who": "manager"}, headers=mgr).status_code == 200
    # lots
    assert {l["id"] for l in c.get("/lots", headers=sm).json()} == {world["lot1"], world["lot2"]}
    assert c.get(f"/lots/{world['lot1']}/summary", headers=sm).status_code == 200
    for method, path in (("get", f"/lots/{world['lot3']}/summary"), ("get", f"/lots/{world['lot3']}/certificate"),
                         ("post", f"/lots/{world['lot3']}/withdraw"), ("post", f"/lots/{world['lot3']}/farmer-update")):
        assert getattr(c, method)(path, headers=sm).status_code == 403, path
    assert c.post("/lots", json={"chamber_id": 3, "farmer_name": "X", "farmer_phone": "+91 90000 99999", "bags": 1}, headers=sm).status_code == 403
    assert c.post("/lots", json={"chamber_id": 2, "farmer_name": "X", "farmer_phone": "+91 90000 99999", "bags": 1}, headers=sm).status_code == 200
    # maintenance
    assert {m["chamber_id"] for m in c.get("/maintenance", headers=sm).json()} <= {1, 2}
    assert c.get("/maintenance?chamber_id=3", headers=sm).status_code == 403
    assert c.post(f"/maintenance/{world['task3']}/done", headers=sm).status_code == 403
    assert c.post("/maintenance", json={"chamber_id": 3, "task": "x y z", "every_days": 5}, headers=sm).status_code == 403
    assert c.post("/maintenance", json={"chamber_id": 1, "task": "Clean coil", "every_days": 5}, headers=sm).status_code == 201
    assert c.get("/reports/weekly", headers=sm).status_code == 200


def test_site_manager_has_no_owner_powers(secure, world):
    c, sm = secure, world["sm"]
    assert c.get("/users", headers=sm).status_code == 403
    assert c.post("/sites", json={"name": "New site"}, headers=sm).status_code == 403
    assert c.post("/sim/scenario", json={"chamber_id": 1, "scenario": "reset"}, headers=sm).status_code == 403
    assert c.post("/admin/tamper/1", headers=sm).status_code == 403
    assert c.post("/reports/weekly/notify", headers=sm).status_code == 403          # head-office only
    assert c.post("/reports/weekly/notify", headers=world["manager"]).status_code == 200


def test_accountant_permissions(secure, world):
    c, a, lot = secure, world["acc"], world["lot1"]
    for path in ("/chambers", "/sites", "/lots", f"/lots/{lot}/summary", f"/lots/{lot}/certificate", "/reports/weekly"):
        assert c.get(path, headers=a).status_code == 200, path
    new = c.post("/lots", json={"chamber_id": 1, "farmer_name": "Geeta Devi", "farmer_phone": "+91 90000 77777", "bags": 5}, headers=a)
    assert new.status_code == 200
    assert c.post(f"/lots/{new.json()['id']}/farmer-update", headers=a).status_code == 200
    assert c.post(f"/lots/{new.json()['id']}/withdraw", headers=a).status_code == 200
    # never alerts, maintenance, simulator, users, live readings
    for path in ("/alerts", "/notifications", "/maintenance", "/chambers/1/readings", "/chambers/1/spoilage", "/users", "/sim/status", "/evaluation"):
        assert c.get(path, headers=a).status_code == 403, path
    assert c.post("/alerts/1/ack", json={"who": "acc"}, headers=a).status_code == 403
    assert c.post(f"/maintenance/{world['task3']}/done", headers=a).status_code == 403
    assert c.post("/maintenance", json={"chamber_id": 1, "task": "x y z", "every_days": 5}, headers=a).status_code == 403
    assert c.post("/reports/weekly/notify", headers=a).status_code == 403
    assert c.post("/sim/scenario", json={"chamber_id": 1, "scenario": "reset"}, headers=a).status_code == 403


def test_farmer_sees_only_own_lots(secure, world):
    c, f = secure, world["farmer"]
    mine = c.get("/my/lots", headers=f)
    assert mine.status_code == 200
    assert [l["id"] for l in mine.json()] == [world["lot1"]]            # not Suresh's lot in the same chamber, not Mohan's in chamber 3
    assert all("farmer_phone" not in l for l in mine.json())
    s = c.get(f"/my/lots/{world['lot1']}/summary", headers=f)
    assert s.status_code == 200
    body = s.json()
    assert "call_list" not in body["chamber"] and "+91 90000 00001" not in s.text        # no owner / manager phone numbers
    assert "last_24h" in body and body["chain"]["ok"] is True
    assert c.get(f"/my/lots/{world['lot1']}/certificate", headers=f).status_code == 200
    for lot in (world["lot2"], world["lot3"]):
        assert c.get(f"/my/lots/{lot}/summary", headers=f).status_code == 404
        assert c.get(f"/my/lots/{lot}/certificate", headers=f).status_code == 404


def test_farmer_cannot_use_staff_endpoints(secure, world):
    c, f = secure, world["farmer"]
    for path in ("/chambers", "/sites", "/alerts", "/notifications", "/maintenance", "/lots", f"/lots/{world['lot1']}/summary",
                 f"/lots/{world['lot1']}/certificate", "/reports/weekly", "/users", "/sim/status", "/evaluation", "/chambers/1/readings"):
        assert c.get(path, headers=f).status_code == 403, path
    assert c.post("/lots", json={"chamber_id": 1, "farmer_name": "X", "farmer_phone": "+91 90000 99999", "bags": 1}, headers=f).status_code == 403
    assert c.post("/alerts/1/ack", json={"who": "farmer"}, headers=f).status_code == 403
    # staff roles have no farmer view
    assert c.get("/my/lots", headers=world["manager"]).status_code == 403
    assert c.get("/my/lots", headers=world["acc"]).status_code == 403
    assert c.get("/my/lots").status_code == 401

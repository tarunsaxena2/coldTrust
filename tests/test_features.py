import sqlite3
import pytest
from fastapi.testclient import TestClient
from api import create_app
from src.db import Store
from src.spoilage import spoilage_risk, degree_hours

T0 = 1_800_000_000


def normal(n=60):
    return [3.0 + 0.3 * ((i % 20) / 10 - 1 if i % 20 < 10 else 1 - (i % 20 - 10) / 10) for i in range(n)]


def feed(c, temps, chamber=1, start=T0, comp=True, headers=None, **extra):
    for i, t in enumerate(temps):
        r = c.post("/ingest", json={"chamber_id": chamber, "temp_c": t, "humidity_pct": 90, "door_open": False,
                                    "compressor_on": comp, "ts": start + 60 * i, **extra}, headers=headers or {})
        assert r.status_code == 200, r.text


@pytest.fixture()
def client(tmp_path):
    app = create_app(auth=False, db_path=str(tmp_path / "f.db"), sim=False, backfill=0, esc_wait=0.3)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def secure(tmp_path):
    app = create_app(auth=True, db_path=str(tmp_path / "s.db"), sim=False, backfill=0, esc_wait=0.3)
    with TestClient(app) as c:
        yield c


def login(c, user, pw):
    r = c.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


# ---------- sensor offline ----------
def test_sensor_offline_alert_escalates_and_clears(client):
    eng = client.app.state.engine
    feed(client, normal(), chamber=1)
    last = T0 + 59 * 60
    assert eng.check_offline(now=last + 4 * 60) == []                 # 4 min of silence: still fine
    assert eng.check_offline(now=last + 6 * 60) == [1]                # 6 min: warning
    al = [a for a in client.get("/alerts").json() if a["code"] == "offline"]
    assert len(al) == 1 and al[0]["level"] == "WARNING" and "सेंसर" in al[0]["message_hi"]
    assert not client.get("/chambers").json()[0]["online"]
    eng.check_offline(now=last + 16 * 60)                              # 16 min (> 3x limit): critical + voice calls
    assert any(a["level"] == "CRITICAL" and a["code"] == "offline" for a in client.get("/alerts").json())
    import time; time.sleep(0.5)
    assert any(n["channel"] == "voice" for n in client.get("/notifications").json())
    feed(client, [3.0], chamber=1, start=last + 17 * 60)               # data is back
    assert client.get("/chambers").json()[0]["online"]
    assert all(a["resolved_ts"] for a in client.get("/alerts").json() if a["code"] == "offline")


# ---------- offline buffer flush ----------
def test_batch_ingest_flushes_buffered_readings(client):
    readings = [{"ts": T0 + 60 * i, "temp_c": t, "humidity_pct": 90, "door_open": False} for i, t in reversed(list(enumerate(normal(40))))]
    r = client.post("/ingest/batch", json={"chamber_id": 1, "readings": readings})
    assert r.status_code == 200 and r.json()["ingested"] == 40
    rows = client.get("/chambers/1/readings?minutes=600").json()
    assert len(rows) == 40 and rows[0]["ts"] < rows[-1]["ts"]          # stored in time order even though sent reversed
    assert client.post("/ingest/batch", json={"chamber_id": 99, "readings": []}).status_code == 404


# ---------- compressor current ----------
def test_compressor_current_decides_state_and_keeps_chain_valid(client):
    lot = client.post("/lots", json={"chamber_id": 1, "farmer_name": "A B", "farmer_phone": "+91 90000 33333", "bags": 5}).json()["id"]
    for i in range(30):
        amps = 6.4 if i % 2 == 0 else 0.1
        r = client.post("/ingest", json={"chamber_id": 1, "temp_c": 3.0, "ts": T0 + 60 * i, "compressor_current_a": amps}).json()
        assert r["comp"] is (amps > 1.0) and r["current_a"] == amps
    assert client.get(f"/lots/{lot}/summary").json()["chain"]["ok"]
    assert client.get("/chambers").json()[0]["run_hours"] > 0
    assert client.post("/ingest", json={"chamber_id": 1, "temp_c": 3.0, "compressor_current_a": -1}).status_code == 422


# ---------- spoilage ----------
def test_spoilage_score_math():
    ts = [i * 60 for i in range(121)]
    assert degree_hours(ts, [3.0] * 121, 5.0, 1.0) == 0
    r = spoilage_risk(ts, [8.0] * 121, 5.0, 1.0, k=6.0)             # 3 degC above limit for 2 h = 6 degC-h
    assert r["degree_hours"] == pytest.approx(6.0, abs=0.1) and 60 < r["score"] < 66 and r["level"] == "HIGH"
    assert spoilage_risk(ts, [3.0] * 121, 5.0, 1.0)["level"] == "LOW"


def test_lot_summary_and_chamber_spoilage(client):
    lot = client.post("/lots", json={"chamber_id": 1, "farmer_name": "Test", "farmer_phone": "+91 90000 44444", "bags": 5}).json()["id"]
    feed(client, [6.5] * 120)
    s = client.get(f"/lots/{lot}/summary").json()
    assert s["spoilage"]["level"] in ("MODERATE", "HIGH") and s["spoilage"]["degree_hours"] > 1
    assert client.get("/chambers/1/spoilage").json()["score"] > 0


# ---------- sites / multi-site ----------
def test_sites_and_adding_chamber(client):
    sites = client.get("/sites").json()
    assert len(sites) == 2 and all(s["lat"] for s in sites)
    sid = client.post("/sites", json={"name": "Farrukhabad Store", "location": "Farrukhabad", "lat": 27.39, "lon": 79.58}).json()["id"]
    cid = client.post("/chambers", json={"site_id": sid, "name": "Chamber A", "crop": "Potato", "setpoint": 3.0, "temp_max": 5.0, "temp_min": 1.0}).json()["id"]
    feed(client, normal(10), chamber=cid)
    assert any(s["id"] == sid and s["chambers"] == [cid] for s in client.get("/sites").json())
    assert client.post("/chambers", json={"site_id": 999, "name": "x", "setpoint": 3, "temp_max": 5, "temp_min": 1}).status_code == 404
    assert client.post("/chambers", json={"site_id": sid, "name": "x", "setpoint": 3, "temp_max": 1, "temp_min": 5}).status_code == 422


# ---------- maintenance ----------
def test_maintenance_due_notifies_and_done_resets(client):
    eng = client.app.state.engine
    assert len(client.get("/maintenance?chamber_id=1").json()) == 3
    mid = client.post("/maintenance", json={"chamber_id": 1, "task": "Test task", "every_run_hours": 0.5}).json()["id"]
    feed(client, [3.0] * 60, comp=True)                                  # ~1 h of compressor running
    due = eng.check_maintenance(now=T0 + 3600)
    assert mid in due
    msgs = [n for n in client.get("/notifications").json() if "Test task" in n["message"]]
    assert msgs and "चैंबर 1" in msgs[0]["message"]
    n_before = len(client.get("/notifications").json())
    eng.check_maintenance(now=T0 + 3700)                                 # already notified: no repeat message
    assert len(client.get("/notifications").json()) == n_before
    assert client.post(f"/maintenance/{mid}/done").status_code == 200
    assert mid not in eng.check_maintenance(now=T0 + 3800)
    assert client.post("/maintenance", json={"chamber_id": 1, "task": "No interval"}).status_code == 422


# ---------- reports ----------
def test_weekly_report_pdf_and_hindi_summary(client):
    feed(client, normal(120))
    r = client.get("/reports/weekly")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "चैंबर 1" in client.post("/reports/weekly/notify").json()["message"]


# ---------- IVR ----------
def test_voice_menu_key2_reads_temperature_key1_acks(client):
    feed(client, [3.2 + 0.06 * k for k in range(150)])                   # drives a critical alert
    alert = next(a for a in client.get("/alerts").json() if a["level"] == "CRITICAL")
    x = client.post(f"/voice/gather/{alert['id']}?who=%2B91", data={"Digits": "2"}).text
    assert "डिग्री" in x and "Gather" in x
    assert "धन्यवाद" in client.post(f"/voice/gather/{alert['id']}?who=%2B91", data={"Digits": "1"}).text
    assert next(a for a in client.get("/alerts").json() if a["id"] == alert["id"])["acked_ts"]


# ---------- login / roles / farmer link ----------
def test_auth_roles_and_device_key(secure):
    c = secure
    assert c.get("/health").status_code == 200                           # public
    assert c.get("/chambers").status_code == 401
    assert c.post("/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    owner, manager = login(c, "owner", "owner123"), login(c, "manager", "manager123")
    assert c.get("/chambers", headers=manager).status_code == 200
    assert c.post("/users", json={"username": "ramu", "password": "secret1", "role": "manager"}, headers=manager).status_code == 403
    assert c.post("/users", json={"username": "ramu", "password": "secret1", "role": "manager"}, headers=owner).status_code == 200
    assert c.post("/users", json={"username": "ramu", "password": "secret1", "role": "manager"}, headers=owner).status_code == 409
    assert c.post("/sim/scenario", json={"chamber_id": 1, "scenario": "reset"}, headers=manager).status_code == 403
    assert c.post("/admin/tamper/1", headers=manager).status_code == 403
    # sensors need the device key, not a user login
    body = {"chamber_id": 1, "temp_c": 3.0, "ts": T0}
    assert c.post("/ingest", json=body).status_code == 401
    assert c.post("/ingest", json=body, headers=manager).status_code == 401
    assert c.post("/ingest", json=body, headers={"X-Device-Key": "demo-device-key"}).status_code == 200
    # password change invalidates old sessions
    assert c.post("/auth/change-password", json={"old_password": "owner123", "new_password": "newpass1"}, headers=owner).status_code == 200
    assert c.get("/chambers", headers=owner).status_code == 401
    login(c, "owner", "newpass1")
    c.post("/auth/logout", headers=manager)
    assert c.get("/chambers", headers=manager).status_code == 401


def test_farmer_link_page_is_public_but_only_for_that_lot(secure):
    c = secure
    mgr = login(c, "manager", "manager123")
    dev = {"X-Device-Key": "demo-device-key"}
    lot = c.post("/lots", json={"chamber_id": 1, "farmer_name": "Ramesh Kumar", "farmer_phone": "+91 90000 55555", "bags": 120}, headers=mgr).json()["id"]
    feed(c, normal(90), headers=dev)
    token = next(l for l in c.get("/lots", headers=mgr).json() if l["id"] == lot)["token"]
    assert len(token) >= 12
    page = c.get(f"/f/{token}")                                          # no login needed
    assert page.status_code == 200 and "Ramesh Kumar" in page.text and "सब ठीक है" in page.text and "<svg" in page.text
    assert c.get(f"/f/{token}/certificate").content.startswith(b"%PDF")
    assert c.get("/f/not-a-real-token").status_code == 404
    assert c.get(f"/lots/{lot}/summary").status_code == 401              # staff API still protected
    v = c.get(f"/verify/{lot}").text
    assert "Ramesh K." in v and "Ramesh Kumar" not in v                  # public verify page masks the surname
    msg = c.post(f"/lots/{lot}/farmer-update", headers=mgr).json()["message"]
    assert f"/f/{token}" in msg


# ---------- old databases keep working ----------
def test_migration_adds_columns_to_old_database(tmp_path):
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.executescript("""CREATE TABLE chambers(id INTEGER PRIMARY KEY, name TEXT, crop TEXT, setpoint REAL, temp_max REAL, temp_min REAL, call_list TEXT);
    CREATE TABLE readings(id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, ts REAL, temp REAL, humidity REAL, door INTEGER, comp INTEGER, level TEXT, score REAL, prev_hash TEXT, hash TEXT);
    CREATE TABLE lots(id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, farmer_name TEXT, farmer_phone TEXT, bags INTEGER, loaded_ts REAL, withdrawn_ts REAL);
    INSERT INTO lots(chamber_id,farmer_name,farmer_phone,bags,loaded_ts) VALUES(1,'Old Farmer','+91 1',5,1.0);""")
    con.commit(); con.close()
    s = Store(p)
    for table, col in (("readings", "current_a"), ("chambers", "site_id"), ("chambers", "run_hours"), ("lots", "token")):
        assert col in [r["name"] for r in s.query(f"PRAGMA table_info({table})")]
    app = create_app(auth=False, db_path=p, sim=False, backfill=0)
    with TestClient(app) as c:                                           # engine backfills tokens for old lots
        assert c.get("/lots").json()[0]["token"]


# ---------- retrain on stored (here: simulated) readings ----------
def test_retrain_from_stored_readings(tmp_path):
    from src.engine import Engine
    from src.retrain_real import retrain
    eng = Engine(db_path=str(tmp_path / "r.db"), sim=False, backfill=0)
    import numpy as np
    from src.simulator import ChamberSim
    sim = ChamberSim(1, 3.0, seed=5)
    for m in range(1800):                                              # 30 h of normal operation for chamber 1
        r = sim.step(m)
        eng.ingest(1, r["temp_c"], r["humidity_pct"], r["door_open"], r["compressor_on"], ts=T0 + 60 * m, quiet=True, current_a=r["compressor_current_a"])
    model, shape, used = retrain(eng.store, str(tmp_path / "m.joblib"), chamber_ids=[1], min_hours=24)
    assert shape[0] > 1000 and shape[1] == 7 and "windows" in used[1]
    assert np.isfinite(model["threshold"])
    with pytest.raises(SystemExit):                                    # too little data -> refuses instead of training a bad model
        retrain(eng.store, str(tmp_path / "m2.joblib"), chamber_ids=[2], min_hours=24)

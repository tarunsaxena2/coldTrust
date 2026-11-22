import time
import pytest
from fastapi.testclient import TestClient
from api import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(auth=False, db_path=str(tmp_path / "t.db"), sim=False, backfill=0, esc_wait=1.5)
    with TestClient(app) as c:
        yield c


def feed(client, start_ts, temps, chamber=1):
    last = None
    for i, t in enumerate(temps):
        last = client.post("/ingest", json={"chamber_id": chamber, "temp_c": t, "humidity_pct": 90, "door_open": False,
                                            "compressor_on": True, "ts": start_ts + 60 * i})
        assert last.status_code == 200
    return last.json()


def normal(n=150):
    return [3.0 + 0.4 * ((i % 24) / 12 - 1 if i % 24 < 12 else 1 - (i % 24 - 12) / 12) for i in range(n)]


def test_health_and_chambers(client):
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/chambers").json()) == 3


def test_ingest_validation(client):
    assert client.post("/ingest", json={"chamber_id": 1, "temp_c": 999}).status_code == 422
    assert client.post("/ingest", json={"chamber_id": 99, "temp_c": 3.0}).status_code == 404
    assert client.post("/ingest", json={"chamber_id": 1}).status_code == 422


def test_normal_data_gives_no_alert(client):
    feed(client, 1_800_000_000, normal())
    assert client.get("/alerts").json() == []


def test_fault_raises_alerts_in_hindi_and_escalation_stops_on_ack(client):
    t0 = 1_800_000_000
    feed(client, t0, normal())
    ramp = [3.2 + 0.06 * k for k in range(45)]
    feed(client, t0 + 150 * 60, ramp)
    alerts = client.get("/alerts").json()
    levels = {a["level"] for a in alerts}
    assert "CRITICAL" in levels
    assert any("चैंबर" in a["message_hi"] for a in alerts)
    crit = next(a for a in alerts if a["level"] == "CRITICAL")
    time.sleep(0.4)                                   # the first voice call (owner) is ringing
    calls = [n for n in client.get("/notifications").json() if n["channel"] == "voice"]
    assert len(calls) == 1 and calls[0]["status"] == "calling"
    assert client.post(f"/alerts/{crit['id']}/ack", json={"who": "Owner"}).status_code == 200
    time.sleep(2.0)
    calls_after = [n for n in client.get("/notifications").json() if n["channel"] == "voice"]
    assert len(calls_after) == 1                      # escalation stopped: manager and technician never called
    assert calls_after[0]["status"].startswith("answered")


def test_lot_certificate_and_tamper_detection(client):
    t0 = 1_800_000_000
    lot = client.post("/lots", json={"chamber_id": 1, "farmer_name": "Test Farmer", "farmer_phone": "+91 90000 11111", "bags": 50}).json()["id"]
    feed(client, t0, normal(80))
    s = client.get(f"/lots/{lot}/summary").json()
    assert s["chain"]["ok"] and s["readings"] == 80
    pdf = client.get(f"/lots/{lot}/certificate")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "RECORDS VERIFIED" in client.get(f"/verify/{lot}").text
    assert client.post("/admin/tamper/1").status_code == 200
    assert not client.get(f"/lots/{lot}/summary").json()["chain"]["ok"]
    assert "RECORDS TAMPERED" in client.get(f"/verify/{lot}").text
    assert client.post("/admin/restore/1").status_code == 200
    assert client.get(f"/lots/{lot}/summary").json()["chain"]["ok"]


def test_farmer_update_is_hindi(client):
    lot = client.post("/lots", json={"chamber_id": 1, "farmer_name": "Ramesh", "farmer_phone": "+91 90000 22222", "bags": 10}).json()["id"]
    feed(client, 1_800_000_000, normal(20))
    assert "चैंबर" in client.post(f"/lots/{lot}/farmer-update").json()["message"]


def test_unacknowledged_critical_calls_owner_manager_technician(tmp_path):
    app = create_app(auth=False, db_path=str(tmp_path / "e.db"), sim=False, backfill=0, esc_wait=0.3)
    with TestClient(app) as c:
        t0 = 1_800_000_000
        feed(c, t0, normal())
        feed(c, t0 + 150 * 60, [3.2 + 0.06 * k for k in range(45)])
        time.sleep(1.6)
        calls = sorted([n for n in c.get("/notifications").json() if n["channel"] == "voice"], key=lambda n: n["id"])
        assert [n["to_name"].split(" ")[0] for n in calls] == ["Owner", "Manager", "Technician"]
        assert all(n["status"] == "no answer" for n in calls)

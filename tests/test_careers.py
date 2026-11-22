"""Job applications: public form -> owner/manager only."""
import pytest
from fastapi.testclient import TestClient
from api import create_app

APPLY = {"job_title": "Intern – IoT and Embedded (ESP32)", "department": "Engineering", "name": "Asha Verma",
         "phone": "+91 98765 43210", "email": "asha@example.com", "org": "HCST Mathura", "link": "https://github.com/asha", "message": "I like IoT."}


@pytest.fixture()
def secure(tmp_path):
    app = create_app(auth=True, db_path=str(tmp_path / "c.db"), sim=False, backfill=0, esc_wait=0.3)
    with TestClient(app) as c:
        yield c


def login(c, user, pw):
    r = c.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_public_can_apply_and_owner_manager_see_it(secure):
    c = secure
    assert c.post("/careers/apply", json=APPLY).status_code == 200            # no login needed
    for u, pw in (("owner", "owner123"), ("manager", "manager123")):
        rows = c.get("/careers/applications", headers=login(c, u, pw)).json()
        assert len(rows) == 1 and rows[0]["name"] == "Asha Verma" and rows[0]["status"] == "new"
        assert c.get("/careers/applications/summary", headers=login(c, u, pw)).json()["new"] == 1


def test_other_roles_and_anonymous_cannot_read(secure):
    c = secure
    c.post("/careers/apply", json=APPLY)
    assert c.get("/careers/applications").status_code == 401
    owner = login(c, "owner", "owner123")
    for name, role in (("tech1", "technician"), ("acc1", "accountant"), ("sm1", "site_manager"), ("view1", "viewer")):
        assert c.post("/users", json={"username": name, "password": "secret1", "role": role, "site_id": 1 if role == "site_manager" else None}, headers=owner).status_code == 200
        h = login(c, name, "secret1")
        assert c.get("/careers/applications", headers=h).status_code == 403
        assert c.post("/careers/applications/1/status", json={"status": "hired"}, headers=h).status_code == 403


def test_manager_can_update_status_and_note(secure):
    c = secure
    aid = c.post("/careers/apply", json=APPLY).json()["id"]
    m = login(c, "manager", "manager123")
    assert c.post(f"/careers/applications/{aid}/status", json={"status": "shortlisted", "note": "call Monday"}, headers=m).status_code == 200
    row = c.get("/careers/applications", headers=m).json()[0]
    assert row["status"] == "shortlisted" and row["note"] == "call Monday" and row["updated_by"] == "manager"
    assert c.post(f"/careers/applications/{aid}/status", json={"status": "boss"}, headers=m).status_code == 422
    assert c.post("/careers/applications/999/status", json={"status": "hired"}, headers=m).status_code == 404


def test_validation_duplicate_and_honeypot(secure):
    c = secure
    assert c.post("/careers/apply", json={**APPLY, "email": "not-an-email"}).status_code == 422
    assert c.post("/careers/apply", json={**APPLY, "name": ""}).status_code == 422
    assert c.post("/careers/apply", json=APPLY).status_code == 200
    assert c.post("/careers/apply", json=APPLY).status_code == 409                    # same email + role within a day
    assert c.post("/careers/apply", json={**APPLY, "email": "bot@example.com", "website": "http://spam"}).status_code == 200
    rows = c.get("/careers/applications", headers=login(c, "owner", "owner123")).json()
    assert len(rows) == 1                                                             # honeypot entry was not stored


def test_careers_page_is_served(secure):
    r = secure.get("/careers")
    assert r.status_code == 200 and "/careers/apply" in r.text and "Apply now" in r.text


# ---------------- suggestions / contact messages ----------------
SUG = {"kind": "suggestion", "name": "Ravi", "contact": "9876543210", "topic": "Product idea", "message": "Please add a Punjabi alert option."}


def test_public_suggestion_reaches_owner_and_manager(secure):
    c = secure
    assert c.post("/suggestions", json=SUG).status_code == 200                      # no login needed
    assert c.post("/suggestions", json={"kind": "contact", "message": "Call me about a demo", "name": "Meena"}).status_code == 200
    for u, pw in (("owner", "owner123"), ("manager", "manager123")):
        h = login(c, u, pw)
        rows = c.get("/suggestions", headers=h).json()
        assert {r["kind"] for r in rows} == {"suggestion", "contact"} and all(r["status"] == "new" for r in rows)
        assert c.get("/suggestions/summary", headers=h).json()["new"] == 2


def test_suggestions_hidden_from_other_roles_and_validated(secure):
    c = secure
    c.post("/suggestions", json=SUG)
    assert c.get("/suggestions").status_code == 401
    owner = login(c, "owner", "owner123")
    assert c.post("/users", json={"username": "tech9", "password": "secret1", "role": "technician"}, headers=owner).status_code == 200
    h = login(c, "tech9", "secret1")
    assert c.get("/suggestions", headers=h).status_code == 403
    assert c.post("/suggestions/1/status", json={"status": "done"}, headers=h).status_code == 403
    assert c.post("/suggestions", json={**SUG, "message": "x"}).status_code == 422                        # too short
    assert c.post("/suggestions", json={**SUG, "kind": "spam"}).status_code == 422
    assert c.post("/suggestions", json={**SUG, "message": "bot text", "website": "http://spam"}).status_code == 200
    assert len(c.get("/suggestions", headers=owner).json()) == 1                                           # honeypot not stored
    assert c.post("/suggestions", json=SUG).status_code == 200                                              # repeat is ignored
    assert len(c.get("/suggestions", headers=owner).json()) == 1


def test_manager_can_mark_suggestion_reviewed(secure):
    c = secure
    sid = c.post("/suggestions", json=SUG).json()["id"]
    m = login(c, "manager", "manager123")
    assert c.post(f"/suggestions/{sid}/status", json={"status": "reviewed", "note": "good idea"}, headers=m).status_code == 200
    row = c.get("/suggestions", headers=m).json()[0]
    assert row["status"] == "reviewed" and row["note"] == "good idea" and row["updated_by"] == "manager"
    assert c.post(f"/suggestions/{sid}/status", json={"status": "boss"}, headers=m).status_code == 422
    assert c.post("/suggestions/999/status", json={"status": "done"}, headers=m).status_code == 404


def test_website_forms_post_to_dashboard(secure):
    assert "/suggestions" in secure.get("/careers").text and "id=\"sform\"" in secure.get("/careers").text
    assert "kind:'contact'" in secure.get("/").text

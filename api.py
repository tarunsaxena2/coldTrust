"""ColdTrust API (FastAPI). Run:  uvicorn api:app --port 8000"""
from __future__ import annotations
import hmac
import html
import json
import time
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Form
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import config
from src.engine import Engine
from src.certificate import build_certificate, fmt_ts
from src.simulator import SCENARIOS
from src.auth import Auth, ROLES
from src import access
from src import farmer_page, report


class ReadingIn(BaseModel):
    chamber_id: int
    temp_c: float = Field(ge=-40, le=80)
    humidity_pct: float = Field(default=90.0, ge=0, le=100)
    door_open: bool = False
    compressor_on: bool | None = None
    compressor_current_a: float | None = Field(default=None, ge=0, le=500)
    ts: float | None = None


class BatchReading(BaseModel):
    ts: float
    temp_c: float = Field(ge=-40, le=80)
    humidity_pct: float = Field(default=90.0, ge=0, le=100)
    door_open: bool = False
    compressor_on: bool | None = None
    compressor_current_a: float | None = Field(default=None, ge=0, le=500)


class BatchIn(BaseModel):
    chamber_id: int
    readings: list[BatchReading] = Field(max_length=5000)


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=100)
    role: str
    site_id: int | None = None          # required for site_manager
    phone: str | None = None            # required for farmer (matches the phone number on the farmer's lots)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=100)


class OfflineIn(BaseModel):
    chamber_id: int
    offline: bool


class SiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    location: str = ""
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class ChamberIn(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=60)
    crop: str = "Potato"
    setpoint: float = Field(ge=-5, le=25)
    temp_max: float = Field(ge=-5, le=30)
    temp_min: float = Field(ge=-10, le=25)


class MaintIn(BaseModel):
    chamber_id: int
    task: str = Field(min_length=3, max_length=120)
    every_run_hours: float | None = Field(default=None, gt=0)
    every_days: float | None = Field(default=None, gt=0)


class AckIn(BaseModel):
    who: str = "Owner"


class ScenarioIn(BaseModel):
    chamber_id: int
    scenario: str


class SpeedIn(BaseModel):
    speed: float = Field(ge=0.5, le=20)


class LotIn(BaseModel):
    chamber_id: int
    farmer_name: str = Field(min_length=1, max_length=80)
    farmer_phone: str = Field(min_length=5, max_length=20)
    bags: int = Field(gt=0, le=100000)


APPLICATION_STATUS = ("new", "shortlisted", "interview", "hired", "rejected")
SUGGESTION_STATUS = ("new", "reviewed", "done")


class ApplicationIn(BaseModel):
    """Job application sent from the public careers page."""
    job_title: str = Field(min_length=1, max_length=120)
    department: str = Field(default="", max_length=60)
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=5, max_length=20)
    email: str = Field(min_length=5, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    org: str = Field(default="", max_length=120)
    link: str = Field(default="", max_length=300)
    message: str = Field(default="", max_length=1000)
    website: str = Field(default="", max_length=200)      # honeypot: real people leave it empty


class SuggestionIn(BaseModel):
    """Suggestion box / contact message from the public website (name and contact are optional)."""
    kind: str = Field(default="suggestion", pattern=r"^(suggestion|contact)$")
    name: str = Field(default="", max_length=80)
    contact: str = Field(default="", max_length=120)
    topic: str = Field(default="", max_length=60)
    message: str = Field(min_length=3, max_length=1500)
    website: str = Field(default="", max_length=200)      # honeypot


class ApplicationStatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=500)


def create_app(auth: bool | None = None, **engine_kwargs) -> FastAPI:
    auth_on = config.AUTH_ENABLED if auth is None else auth

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = Engine(**engine_kwargs)
        eng.start()
        app.state.engine = eng
        app.state.auth = Auth(eng.store, config.SESSION_HOURS)
        if auth_on and app.state.auth.default_passwords_in_use:
            print("WARNING: default logins owner/owner123 and manager/manager123 are active. Set OWNER_PASSWORD / MANAGER_PASSWORD.")
        yield
        eng.stop()

    app = FastAPI(title="ColdTrust API", version="1.0", lifespan=lifespan)

    def E() -> Engine:
        return app.state.engine

    # ---------------- auth helpers ----------------
    def current_user(authorization: str | None = Header(default=None)):
        if not auth_on:
            return {"id": 0, "username": "anonymous", "role": "owner"}
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "login required")
        u = app.state.auth.user_for_token(authorization[7:].strip())
        if not u:
            raise HTTPException(401, "session expired, please log in again")
        return u

    def needs(*roles):
        def dep(u=Depends(current_user)):
            if u["role"] not in roles:
                raise HTTPException(403, "not allowed for your role")
            return u
        return dep

    def device_key(x_device_key: str | None = Header(default=None)):
        if auth_on and not (x_device_key and hmac.compare_digest(x_device_key, config.DEVICE_KEY)):
            raise HTTPException(401, "invalid device key")

    # role groups are defined in src/access.py
    STAFF = Depends(needs(*access.STAFF))            # owner, manager, site_manager: add maintenance tasks
    OWNER = Depends(needs(*access.OWNER))
    VIEW = Depends(needs(*access.VIEW))              # monitoring data
    LIST = Depends(needs(*access.LIST))              # chamber / site lists (also accountant)
    ACK = Depends(needs(*access.ACK))                # acknowledge alerts, mark maintenance done
    READ = Depends(needs(*access.READ))              # lots, certificates, weekly report (read)
    LOTS_WRITE = Depends(needs(*access.LOTS_WRITE))  # register / withdraw lots, message farmers
    HQ = Depends(needs(*access.HQ))                  # owner, manager: weekly WhatsApp summary
    FARMER = Depends(needs(*access.FARMER))

    # ---- site scoping: a user with a site_id (site_manager) only sees that site ----
    def visible_ids(u) -> list[int]:
        return access.visible_chamber_ids(E().chambers, u)

    def guard_chamber(u, cid):
        if not access.chamber_allowed(E().chambers, u, cid):
            raise HTTPException(403, "this chamber belongs to another site")

    def guard_lot(u, lot_id):
        row = E().store.one("SELECT chamber_id FROM lots WHERE id=?", (lot_id,))
        if row:
            guard_chamber(u, row["chamber_id"])

    def in_clause(ids) -> str:
        return ",".join(str(int(i)) for i in ids) or "0"

    @app.post("/auth/login")
    def login(b: LoginIn):
        r = app.state.auth.login(b.username, b.password)
        if not r:
            raise HTTPException(401, "wrong username or password")
        return r

    @app.get("/auth/me")
    def me(u=Depends(current_user)):
        return u

    @app.post("/auth/logout")
    def logout(authorization: str | None = Header(default=None)):
        if authorization and authorization.lower().startswith("bearer "):
            app.state.auth.logout(authorization[7:].strip())
        return {"ok": True}

    @app.post("/auth/change-password")
    def change_password(b: PasswordIn, u=Depends(current_user)):
        if not app.state.auth.change_password(u["id"], b.old_password, b.new_password):
            raise HTTPException(400, "old password is wrong")
        return {"ok": True}

    @app.get("/users")
    def users(_=OWNER):
        return app.state.auth.list_users()

    @app.post("/users")
    def add_user(b: UserIn, _=OWNER):
        if b.role not in ROLES:
            raise HTTPException(422, "role must be one of: " + ", ".join(ROLES))
        try:
            return {"id": app.state.auth.create_user(b.username, b.password, b.role, b.site_id, b.phone)}
        except ValueError as ex:
            raise HTTPException(422, str(ex))
        except Exception as ex:
            raise HTTPException(409, f"cannot create user: {ex}")

    @app.get("/health")
    def health():
        e = E()
        return {"status": "ok", "sim": e.sim_enabled, "notify_mode": e.notifier.mode, "model_loaded": e.model is not None,
                "chambers": len(e.chambers), "latest_ts": e.latest_ts, "auth": auth_on}

    # ---------------- sensor ingest (ESP32 posts here) ----------------
    @app.post("/ingest", dependencies=[Depends(device_key)])
    def ingest(r: ReadingIn):
        try:
            res = E().ingest(r.chamber_id, r.temp_c, r.humidity_pct, r.door_open, r.compressor_on, r.ts, current_a=r.compressor_current_a)
        except KeyError:
            raise HTTPException(404, "unknown chamber")
        return res

    @app.post("/ingest/batch", dependencies=[Depends(device_key)])
    def ingest_batch(b: BatchIn):
        """Offline buffer flush: the ESP32 stores readings while the network is down and sends them here afterwards."""
        e = E()
        if b.chamber_id not in e.chambers:
            raise HTTPException(404, "unknown chamber")
        for r in sorted(b.readings, key=lambda x: x.ts):
            e.ingest(b.chamber_id, r.temp_c, r.humidity_pct, r.door_open, r.compressor_on, r.ts, current_a=r.compressor_current_a)
        return {"ingested": len(b.readings)}

    @app.get("/chambers")
    def chambers(u=LIST):
        e = E()
        out = []
        for cid in visible_ids(u):
            ch = e.chambers[cid]
            open_alert = e.store.one("SELECT id FROM alerts WHERE chamber_id=? AND resolved_ts IS NULL ORDER BY id DESC LIMIT 1", (cid,))
            out.append({**{k: ch[k] for k in ("id", "name", "crop", "setpoint", "temp_max", "temp_min", "site_id")},
                        "run_hours": round(ch["run_hours"], 2), "online": cid not in e.offline,
                        "latest": e.latest.get(cid), "open_alert_id": open_alert["id"] if open_alert else None,
                        "scenario": e.scenarios().get(cid)})
        return out

    @app.get("/chambers/{cid}/readings")
    def readings(cid: int, minutes: int = 180, u=VIEW):
        e = E()
        guard_chamber(u, cid)
        if cid not in e.chambers:
            raise HTTPException(404, "unknown chamber")
        since = (e.latest_ts or 0) - minutes * 60
        rows = e.store.query("SELECT id,ts,temp,humidity,door,comp,current_a,level,score FROM readings WHERE chamber_id=? AND ts>=? ORDER BY id", (cid, since))
        return rows

    @app.get("/chambers/{cid}/spoilage")
    def spoilage(cid: int, hours: int = 24, u=VIEW):
        guard_chamber(u, cid)
        if cid not in E().chambers:
            raise HTTPException(404, "unknown chamber")
        return E().chamber_spoilage(cid, hours)

    @app.post("/chambers", status_code=201)
    def add_chamber(b: ChamberIn, _=OWNER):
        if b.temp_min >= b.temp_max:
            raise HTTPException(422, "temp_min must be below temp_max")
        try:
            return {"id": E().add_chamber(b.site_id, b.name, b.crop, b.setpoint, b.temp_max, b.temp_min)}
        except KeyError:
            raise HTTPException(404, "unknown site")

    # ---------------- sites (multi-site) ----------------
    @app.get("/sites")
    def sites(u=LIST):
        e = E()
        rank = {"OK": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        out = []
        for s in e.store.query("SELECT * FROM sites ORDER BY id"):
            if u.get("site_id") is not None and s["id"] != u["site_id"]:
                continue
            chs = [c for c in e.chambers.values() if c.get("site_id") == s["id"]]
            levels = [(e.latest.get(c["id"]) or {}).get("level", "OK") for c in chs]
            off = [c["id"] for c in chs if c["id"] in e.offline]
            worst = max(levels, key=lambda l: rank[l]) if levels else "OK"
            out.append({**s, "chambers": [c["id"] for c in chs], "worst_level": "CRITICAL" if off and rank[worst] < 3 else worst,
                        "offline_chambers": off, "open_alerts": e.store.one(
                            "SELECT COUNT(*) AS c FROM alerts WHERE resolved_ts IS NULL AND chamber_id IN (%s)" % (",".join(str(c["id"]) for c in chs) or "0"))["c"]})
        return out

    @app.post("/sites", status_code=201)
    def add_site(b: SiteIn, _=OWNER):
        return {"id": E().add_site(b.name, b.location, b.lat, b.lon)}

    # ---------------- maintenance ----------------
    @app.get("/maintenance")
    def maintenance(chamber_id: int | None = None, u=VIEW):
        if chamber_id:
            guard_chamber(u, chamber_id)
        keep = set(visible_ids(u))
        return [m for m in E().maintenance_list(chamber_id) if m["chamber_id"] in keep]

    @app.post("/maintenance", status_code=201)
    def add_maint(b: MaintIn, u=STAFF):
        guard_chamber(u, b.chamber_id)
        if not b.every_run_hours and not b.every_days:
            raise HTTPException(422, "give every_run_hours or every_days")
        try:
            return {"id": E().add_maintenance(b.chamber_id, b.task, b.every_run_hours, b.every_days)}
        except KeyError:
            raise HTTPException(404, "unknown chamber")

    @app.post("/maintenance/{mid}/done")
    def maint_done(mid: int, u=ACK):
        row = E().store.one("SELECT chamber_id FROM maintenance WHERE id=?", (mid,))
        if row:
            guard_chamber(u, row["chamber_id"])
        if not E().maintenance_done(mid):
            raise HTTPException(404, "unknown task")
        return {"done": True}

    # ---------------- weekly report ----------------
    @app.get("/reports/weekly")
    def weekly_report(days: int = 7, u=READ):
        pdf = report.build_weekly_report(E().weekly_report_data(max(1, min(days, 31)), site_id=u.get("site_id")))
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="coldtrust_weekly_report.pdf"'})

    @app.post("/reports/weekly/notify")
    def weekly_notify(days: int = 7, _=HQ):
        e = E()
        msg = e.weekly_summary_hi(days)
        owner = e.chambers[min(e.chambers)]["call_list"][0]
        e.notifier.send("whatsapp", owner["name"], owner["phone"], msg, None, None)
        return {"message": msg}

    # ---------------- alerts ----------------
    # ---------------- alerts & notifications ----------------
    @app.get("/alerts")
    def alerts(limit: int = 50, u=VIEW):
        return E().store.query(f"SELECT * FROM alerts WHERE chamber_id IN ({in_clause(visible_ids(u))}) ORDER BY id DESC LIMIT ?", (limit,))

    @app.post("/alerts/{alert_id}/ack")
    def ack(alert_id: int, body: AckIn, u=ACK):
        row = E().store.one("SELECT chamber_id FROM alerts WHERE id=?", (alert_id,))
        if row:
            guard_chamber(u, row["chamber_id"])
        if not E().ack(alert_id, body.who):
            raise HTTPException(404, "unknown alert")
        return {"acknowledged": True}

    @app.get("/notifications")
    def notifications(limit: int = 60, u=VIEW):
        if u.get("site_id") is None:
            return E().store.query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))
        return E().store.query(f"SELECT * FROM notifications WHERE chamber_id IN ({in_clause(visible_ids(u))}) ORDER BY id DESC LIMIT ?", (limit,))

    # voice-call webhooks (used only in Twilio mode)
    @app.api_route("/twiml/{alert_id}", methods=["GET", "POST"])
    def twiml(alert_id: int, who: str = ""):
        from src.messages import VOICE_HI
        a = E().store.one("SELECT chamber_id FROM alerts WHERE id=?", (alert_id,))
        if not a:
            raise HTTPException(404)
        text = VOICE_HI.format(n=a["chamber_id"])
        xml = (f'<Response><Gather numDigits="1" action="{config.PUBLIC_URL}/voice/gather/{alert_id}?who={who}">'
               f'<Say language="hi-IN">{text} तापमान सुनने के लिए 2 दबाएँ।</Say></Gather></Response>')
        return Response(xml, media_type="application/xml")

    @app.post("/voice/gather/{alert_id}")
    def gather(alert_id: int, who: str = "", Digits: str = Form("")):
        if Digits == "1":
            E().ack(alert_id, f"voice call {who}")
            return Response('<Response><Say language="hi-IN">धन्यवाद।</Say></Response>', media_type="application/xml")
        if Digits == "2":      # key 2: read the current temperature, then ask again
            a = E().store.one("SELECT chamber_id FROM alerts WHERE id=?", (alert_id,))
            lt = E().latest.get(a["chamber_id"]) if a else None
            txt = f"चैंबर {a['chamber_id']} का तापमान अभी {lt['temp']:.1f} डिग्री है।" if lt else "अभी तापमान उपलब्ध नहीं है।"
            return Response(f'<Response><Gather numDigits="1" action="{config.PUBLIC_URL}/voice/gather/{alert_id}?who={who}">'
                            f'<Say language="hi-IN">{txt} स्वीकार करने के लिए 1 दबाएँ।</Say></Gather></Response>', media_type="application/xml")
        return Response('<Response><Say language="hi-IN">कृपया 1 दबाएँ।</Say></Response>', media_type="application/xml")

    # ---------------- simulator controls (demo) ----------------
    @app.get("/sim/status")
    def sim_status(_=VIEW):
        e = E()
        return {"enabled": e.sim_enabled, "speed": e.speed, "scenarios": e.scenarios(), "available": list(SCENARIOS) + ["reset"],
                "offline": sorted(e.sim_offline)}

    @app.post("/sim/scenario")
    def sim_scenario(s: ScenarioIn, _=OWNER):
        e = E()
        if not e.sim_enabled:
            raise HTTPException(400, "simulator is disabled")
        if s.chamber_id not in e.chambers:
            raise HTTPException(404, "unknown chamber")
        if s.scenario not in list(SCENARIOS) + ["reset"]:
            raise HTTPException(422, "unknown scenario")
        e.set_scenario(s.chamber_id, s.scenario)
        return {"chamber_id": s.chamber_id, "scenario": None if s.scenario == "reset" else s.scenario}

    @app.post("/sim/speed")
    def sim_speed(s: SpeedIn, _=OWNER):
        E().speed = s.speed
        return {"speed": s.speed}

    @app.post("/sim/offline")
    def sim_offline(b: OfflineIn, _=OWNER):
        e = E()
        if not e.sim_enabled:
            raise HTTPException(400, "simulator is disabled")
        if b.chamber_id not in e.chambers:
            raise HTTPException(404, "unknown chamber")
        e.set_sim_offline(b.chamber_id, b.offline)
        return {"chamber_id": b.chamber_id, "offline": b.offline}

    @app.post("/admin/check-offline")
    def check_offline(minutes: float | None = None, _=OWNER):
        return {"newly_offline": E().check_offline(minutes=minutes)}

    # ---------------- lots, certificate, verification ----------------
    @app.post("/lots")
    def add_lot(l: LotIn, u=LOTS_WRITE):
        guard_chamber(u, l.chamber_id)
        try:
            return {"id": E().add_lot(l.chamber_id, l.farmer_name, l.farmer_phone, l.bags)}
        except KeyError:
            raise HTTPException(404, "unknown chamber")

    @app.get("/lots")
    def lots(u=READ):
        e = E()
        rows = e.store.query(f"SELECT * FROM lots WHERE chamber_id IN ({in_clause(visible_ids(u))}) ORDER BY id DESC")
        for r in rows:
            r["chamber_name"] = e.chambers[r["chamber_id"]]["name"]
        return rows

    @app.post("/lots/{lot_id}/withdraw")
    def withdraw(lot_id: int, u=LOTS_WRITE):
        guard_lot(u, lot_id)
        if not E().withdraw_lot(lot_id):
            raise HTTPException(404, "lot not found or already withdrawn")
        return {"withdrawn": True}

    @app.get("/lots/{lot_id}/summary")
    def lot_summary(lot_id: int, u=READ):
        guard_lot(u, lot_id)
        s = E().lot_summary(lot_id)
        if not s:
            raise HTTPException(404, "unknown lot")
        return s

    @app.post("/lots/{lot_id}/farmer-update")
    def farmer_update(lot_id: int, u=LOTS_WRITE):
        guard_lot(u, lot_id)
        msg = E().farmer_update(lot_id)
        if msg is None:
            raise HTTPException(404, "unknown lot")
        return {"message": msg}

    @app.get("/lots/{lot_id}/certificate")
    def certificate(lot_id: int, u=READ):
        guard_lot(u, lot_id)
        s = E().lot_summary(lot_id)
        if not s:
            raise HTTPException(404, "unknown lot")
        pdf = build_certificate(s["lot"], s["chamber"], s, f"{config.PUBLIC_URL}/verify/{lot_id}")
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="coldtrust_lot_{lot_id}.pdf"'})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/site", response_class=HTMLResponse, include_in_schema=False)
    def website():
        """Public ColdTrust marketing website (website/index.html); its login button opens /dashboard."""
        page = (config.BASE_DIR / "website" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("__DASHBOARD_URL__", html.escape(config.DASHBOARD_URL, quote=True)))

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        """Staff dashboard (website/dashboard.html) - same server, same link as the website and API."""
        return HTMLResponse((config.BASE_DIR / "website" / "dashboard.html").read_text(encoding="utf-8"))

    # ---------------- job applications (public form -> owner/manager dashboard) ----------------
    @app.post("/careers/apply")
    def apply_job(b: ApplicationIn):
        if b.website:                                    # honeypot filled in -> a bot; pretend it worked
            return {"ok": True}
        s = E().store
        now = time.time()
        if s.one("SELECT id FROM applications WHERE lower(email)=? AND job_title=? AND ts>?", (b.email.lower(), b.job_title, now - 86400)):
            raise HTTPException(409, "You have already applied for this role today.")
        if (s.one("SELECT COUNT(*) AS n FROM applications WHERE ts>?", (now - 3600,)) or {"n": 0})["n"] >= 60:
            raise HTTPException(429, "Too many applications right now, please try again later.")
        aid = s.execute("INSERT INTO applications(ts,job_title,department,name,phone,email,org,link,message,status) VALUES(?,?,?,?,?,?,?,?,?,'new')",
                        (now, b.job_title.strip(), b.department.strip(), b.name.strip(), b.phone.strip(), b.email.strip(),
                         b.org.strip(), b.link.strip(), b.message.strip()))
        return {"ok": True, "id": aid}

    @app.get("/careers/applications")
    def list_applications(status: str | None = None, limit: int = 200, _=HQ):
        limit = max(1, min(limit, 500))
        if status:
            return E().store.query("SELECT * FROM applications WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return E().store.query("SELECT * FROM applications ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/careers/applications/summary")
    def applications_summary(_=HQ):
        rows = E().store.query("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
        out = {s: 0 for s in APPLICATION_STATUS}
        out.update({r["status"]: r["n"] for r in rows})
        out["total"] = sum(out[s] for s in APPLICATION_STATUS)
        return out

    @app.post("/careers/applications/{aid}/status")
    def set_application_status(aid: int, b: ApplicationStatusIn, u=HQ):
        if b.status not in APPLICATION_STATUS:
            raise HTTPException(422, f"status must be one of {', '.join(APPLICATION_STATUS)}")
        if not E().store.one("SELECT id FROM applications WHERE id=?", (aid,)):
            raise HTTPException(404, "application not found")
        E().store.execute("UPDATE applications SET status=?, note=?, updated_by=?, updated_ts=? WHERE id=?",
                          (b.status, b.note.strip(), u["username"], time.time(), aid))
        return {"ok": True}

    # ---------------- suggestions / contact messages (public form -> owner/manager dashboard) ----------------
    @app.post("/suggestions")
    def add_suggestion(b: SuggestionIn):
        if b.website:                                    # honeypot
            return {"ok": True}
        s = E().store
        now = time.time()
        msg = b.message.strip()
        if s.one("SELECT id FROM suggestions WHERE message=? AND ts>?", (msg, now - 86400)):
            return {"ok": True}                          # same text again within a day: quietly ignore the repeat
        if (s.one("SELECT COUNT(*) AS n FROM suggestions WHERE ts>?", (now - 3600,)) or {"n": 0})["n"] >= 60:
            raise HTTPException(429, "Too many messages right now, please try again later.")
        sid = s.execute("INSERT INTO suggestions(ts,kind,name,contact,topic,message,status) VALUES(?,?,?,?,?,?,'new')",
                        (now, b.kind, b.name.strip(), b.contact.strip(), b.topic.strip(), msg))
        return {"ok": True, "id": sid}

    @app.get("/suggestions")
    def list_suggestions(status: str | None = None, limit: int = 200, _=HQ):
        limit = max(1, min(limit, 500))
        if status:
            return E().store.query("SELECT * FROM suggestions WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return E().store.query("SELECT * FROM suggestions ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/suggestions/summary")
    def suggestions_summary(_=HQ):
        rows = E().store.query("SELECT status, COUNT(*) AS n FROM suggestions GROUP BY status")
        out = {s: 0 for s in SUGGESTION_STATUS}
        out.update({r["status"]: r["n"] for r in rows})
        out["total"] = sum(out[s] for s in SUGGESTION_STATUS)
        return out

    @app.post("/suggestions/{sid}/status")
    def set_suggestion_status(sid: int, b: ApplicationStatusIn, u=HQ):
        if b.status not in SUGGESTION_STATUS:
            raise HTTPException(422, f"status must be one of {', '.join(SUGGESTION_STATUS)}")
        if not E().store.one("SELECT id FROM suggestions WHERE id=?", (sid,)):
            raise HTTPException(404, "suggestion not found")
        E().store.execute("UPDATE suggestions SET status=?, note=?, updated_by=?, updated_ts=? WHERE id=?",
                          (b.status, b.note.strip(), u["username"], time.time(), sid))
        return {"ok": True}

    @app.get("/careers", response_class=HTMLResponse, include_in_schema=False)
    def careers_page():
        """Careers page (website/careers.html): edit the JOBS list at the bottom of that file to change the open roles."""
        page = (config.BASE_DIR / "website" / "careers.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("__DASHBOARD_URL__", html.escape(config.DASHBOARD_URL, quote=True)))

    @app.get("/verify/{lot_id}", response_class=HTMLResponse)
    def verify(lot_id: int):
        s = E().lot_summary(lot_id)
        if not s:
            raise HTTPException(404, "unknown lot")
        lot, ch, chain = s["lot"], s["chamber"], s["chain"]
        ok = chain["ok"]
        parts = lot['farmer_name'].split()
        masked = html.escape(parts[0] + (' ' + parts[-1][0] + '.' if len(parts) > 1 else ''))     # public page: first name + initial only
        colour, title = ("#1b7f5c", "RECORDS VERIFIED") if ok else ("#c62828", "RECORDS TAMPERED")
        detail = (f"{chain['checked']} readings checked; every link in the chain matches." if ok else
                  f"Reading #{chain['first_bad_id']} does not match its fingerprint: {chain['reason']}.")
        return f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>ColdTrust verify lot {lot_id}</title><style>body{{font-family:system-ui;max-width:560px;margin:24px auto;padding:0 16px;background:#faf8f1;color:#1c2b33}}
h2{{color:#14532d;margin:0 0 12px}}
.b{{background:{colour};color:#fff;padding:14px 18px;border-radius:10px;font-size:20px;font-weight:600}}
td{{padding:6px 10px;border-bottom:1px solid #ddd}}</style></head><body><div style="height:120px;border-radius:16px;background:linear-gradient(transparent 30%,#0a2a19cc),url(/site-assets/fruitroom.webp) center/cover;margin-bottom:12px"></div>
<h2>🌾 ColdTrust storage record</h2><div class=b>{title}</div><p>{detail}</p>
<table><tr><td>Lot</td><td>#{lot_id} - {masked} ({lot['bags']} bags)</td></tr>
<tr><td>Chamber</td><td>{ch['name']} ({ch['crop']})</td></tr>
<tr><td>Period</td><td>{fmt_ts(lot['loaded_ts'])} to {fmt_ts(lot['withdrawn_ts'] or s['now'])}</td></tr>
<tr><td>Avg / min / max</td><td>{s['avg_temp']:.2f} / {s['min_temp']:.2f} / {s['max_temp']:.2f} C</td></tr>
<tr><td>Minutes outside safe range</td><td>{s['minutes_outside']}</td></tr>
<tr><td>Fingerprint</td><td style="word-break:break-all;font-size:12px">{s['head_hash']}</td></tr></table>
<p style="color:#777;font-size:12px">Prototype with simulated data.</p></body></html>"""

    # ---------------- farmer login: only the lots whose phone number matches the farmer's ----------------
    def _my_lots(u) -> list[dict]:
        key = access.phone_key(u.get("phone"))
        if not key:
            return []
        e = E()
        rows = [r for r in e.store.query("SELECT * FROM lots ORDER BY id DESC") if access.phone_key(r["farmer_phone"]) == key]
        for r in rows:
            r["chamber_name"] = e.chambers[r["chamber_id"]]["name"]
            r.pop("farmer_phone", None)
        return rows

    def _my_lot(u, lot_id: int) -> dict:
        lot = next((r for r in _my_lots(u) if r["id"] == lot_id), None)
        if not lot:
            raise HTTPException(404, "lot not found")
        return lot

    @app.get("/my/lots")
    def my_lots(u=FARMER):
        return _my_lots(u)

    @app.get("/my/lots/{lot_id}/summary")
    def my_lot_summary(lot_id: int, u=FARMER):
        _my_lot(u, lot_id)
        e = E()
        s = e.lot_summary(lot_id)
        lot, ch = s["lot"], s["chamber"]
        recent = e.store.query("SELECT ts,temp FROM readings WHERE chamber_id=? AND ts>=? ORDER BY id", (lot["chamber_id"], e.now() - 86400))
        # only what a farmer needs: no phone numbers (call list), no other lots
        return {"lot": {k: lot[k] for k in ("id", "chamber_id", "farmer_name", "bags", "loaded_ts", "withdrawn_ts", "token")},
                "chamber": {k: ch[k] for k in ("name", "crop", "setpoint", "temp_max", "temp_min")},
                "now": s["now"], "readings": s["readings"], "spoilage": s["spoilage"], "avg_temp": s["avg_temp"], "min_temp": s["min_temp"],
                "max_temp": s["max_temp"], "minutes_outside": s["minutes_outside"], "alerts": s["alerts"],
                "chain": {k: s["chain"][k] for k in ("ok", "checked", "first_bad_id")}, "last_24h": recent}

    @app.get("/my/lots/{lot_id}/certificate")
    def my_lot_certificate(lot_id: int, u=FARMER):
        _my_lot(u, lot_id)
        s = E().lot_summary(lot_id)
        pdf = build_certificate(s["lot"], s["chamber"], s, f"{config.PUBLIC_URL}/verify/{lot_id}")
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="coldtrust_lot_{lot_id}.pdf"'})

    # ---------------- farmer link (no login; the long random token is the key) ----------------
    def _lot_by_token(token: str) -> int:
        row = E().store.one("SELECT id FROM lots WHERE token=?", (token,))
        if not row:
            raise HTTPException(404, "link not found")
        return row["id"]

    @app.get("/f/{token}", response_class=HTMLResponse)
    def farmer_view(token: str):
        lot_id = _lot_by_token(token)
        e = E()
        s = e.lot_summary(lot_id)
        rows = e.store.query("SELECT ts,temp FROM readings WHERE chamber_id=? AND ts>=? ORDER BY id", (s["lot"]["chamber_id"], e.now() - 86400))
        return farmer_page.render(s, rows, token, f"/f/{token}/certificate", f"/verify/{lot_id}")

    @app.get("/f/{token}/certificate")
    def farmer_certificate(token: str):
        lot_id = _lot_by_token(token)
        s = E().lot_summary(lot_id)
        pdf = build_certificate(s["lot"], s["chamber"], s, f"{config.PUBLIC_URL}/verify/{lot_id}")
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="coldtrust_lot_{lot_id}.pdf"'})

    # ---------------- demo: tamper test ----------------
    @app.post("/admin/tamper/{cid}")
    def tamper(cid: int, _=OWNER):
        rid = E().tamper(cid)
        if rid is None:
            raise HTTPException(409, "nothing to tamper or already tampered")
        return {"tampered_reading_id": rid}

    @app.post("/admin/restore/{cid}")
    def restore(cid: int, _=OWNER):
        if not E().restore(cid):
            raise HTTPException(409, "nothing to restore")
        return {"restored": True}

    @app.get("/evaluation")
    def evaluation(_=VIEW):
        p = config.OUTPUT_DIR / "evaluation.json"
        if not p.exists():
            raise HTTPException(404, "run `python evaluate.py` first")
        return json.loads(p.read_text())

    app.mount("/site-assets", StaticFiles(directory=str(config.BASE_DIR / "website" / "images")), name="site-assets")   # website photos

    return app


app = create_app()

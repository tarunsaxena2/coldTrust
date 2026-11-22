"""ColdTrust engine: ties together storage, anomaly detection, alerts, simulator, lots and audit chain."""
from __future__ import annotations
import json
import secrets
import threading
import time
import numpy as np
import config
from src.db import Store
from src.anomaly import ChamberMonitor, RANK
from src.audit_chain import GENESIS, compute_hash, payload, verify_rows
from src.messages import build_alert, FARMER_WEEKLY_HI, OFFLINE_HI, OFFLINE_EN, MAINT_HI
from src.spoilage import spoilage_risk
from src.notifier import Notifier
from src.simulator import ChamberSim
from src.train_model import load_model

IST_OFFSET = 19800


class Engine:
    def __init__(self, db_path=None, model_path=None, sim=None, tick_seconds=None, backfill=None,
                 esc_wait=None, notify_mode=None):
        self.store = Store(db_path or config.DB_PATH)
        self.model = load_model(model_path)
        if self.model is None:          # missing file or incompatible scikit-learn version -> retrain (takes a few seconds)
            try:
                from src.train_model import train
                self.model, _ = train(model_path)
            except Exception as e:
                print("model unavailable, running rules-only:", e)
        self.sim_enabled = config.SIM_ENABLED if sim is None else sim
        self.tick_seconds = config.TICK_SECONDS if tick_seconds is None else tick_seconds
        self.backfill = config.BACKFILL_MINUTES if backfill is None else backfill
        self.speed = 1.0
        self.lock = threading.RLock()
        self.sim_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.latest_ts = 0.0
        self.latest: dict[int, dict] = {}
        self.cur_level: dict[int, str] = {}
        self.last_hash: dict[int, str] = {}
        self.tampered: dict[int, tuple] = {}
        self.chambers: dict[int, dict] = {}
        self.offline: dict[int, dict] = {}          # chamber id -> {"since": ts, "alert_ids": [...], "escalated": bool}
        self.sim_offline: set[int] = set()          # demo: chambers whose sensor is "unplugged"
        self._watch = None
        self.monitors: dict[int, ChamberMonitor] = {}
        self.sims: dict[int, ChamberSim] = {}
        self.notifier = Notifier(self.store, notify_mode or config.NOTIFY_MODE,
                                 config.ESCALATION_WAIT if esc_wait is None else esc_wait, self.now, config)
        self._init_chambers()

    # ---- clock: the timestamp of the latest reading (simulated clock in demo, wall clock in real use)
    def now(self) -> float:
        return self.latest_ts or time.time()

    # ---- setup ----------------------------------------------------------------
    def _init_chambers(self):
        if not self.store.query("SELECT id FROM sites LIMIT 1"):
            for s in config.DEMO_SITES:
                self.store.execute("INSERT INTO sites(id,name,location,lat,lon) VALUES(?,?,?,?,?)",
                                   (s["id"], s["name"], s["location"], s["lat"], s["lon"]))
        if not self.store.query("SELECT id FROM chambers LIMIT 1"):
            for c in config.DEMO_CHAMBERS:
                self.store.execute("INSERT INTO chambers(id,name,crop,setpoint,temp_max,temp_min,call_list,site_id,run_hours) VALUES(?,?,?,?,?,?,?,?,0)",
                                   (c["id"], c["name"], c["crop"], c["setpoint"], c["temp_max"], c["temp_min"],
                                    json.dumps(config.DEMO_CONTACTS), c["site_id"]))
        for row in self.store.query("SELECT * FROM chambers ORDER BY id"):
            self._register_chamber(row)
        for lot in self.store.query("SELECT id FROM lots WHERE token IS NULL"):     # lots created by older versions
            self.store.execute("UPDATE lots SET token=? WHERE id=?", (secrets.token_urlsafe(12), lot["id"]))

    def _register_chamber(self, row):
        row["call_list"] = json.loads(row["call_list"])
        row["run_hours"] = row.get("run_hours") or 0.0
        cid = row["id"]
        self.chambers[cid] = row
        self.monitors[cid] = ChamberMonitor(row, self.model)
        self.sims[cid] = ChamberSim(cid, row["setpoint"], seed=cid)
        self.cur_level[cid] = "OK"
        last = self.store.one("SELECT hash FROM readings WHERE chamber_id=? ORDER BY id DESC LIMIT 1", (cid,))
        self.last_hash[cid] = last["hash"] if last else GENESIS
        if not self.store.one("SELECT id FROM maintenance WHERE chamber_id=? LIMIT 1", (cid,)):
            for task, rh, days in config.DEFAULT_MAINTENANCE:
                self.store.execute("INSERT INTO maintenance(chamber_id,task,interval_run_hours,interval_days,last_done_ts,last_done_run_hours) VALUES(?,?,?,?,?,?)",
                                   (cid, task, rh, days, self.now(), row["run_hours"]))

    def add_site(self, name, location="", lat=None, lon=None) -> int:
        return self.store.execute("INSERT INTO sites(name,location,lat,lon) VALUES(?,?,?,?)", (name, location, lat, lon))

    def add_chamber(self, site_id, name, crop, setpoint, temp_max, temp_min, contacts=None) -> int:
        if not self.store.one("SELECT id FROM sites WHERE id=?", (site_id,)):
            raise KeyError(site_id)
        with self.lock:
            cid = self.store.execute("INSERT INTO chambers(name,crop,setpoint,temp_max,temp_min,call_list,site_id,run_hours) VALUES(?,?,?,?,?,?,?,0)",
                                     (name, crop, setpoint, temp_max, temp_min, json.dumps(contacts or config.DEMO_CONTACTS), site_id))
            self._register_chamber(self.store.one("SELECT * FROM chambers WHERE id=?", (cid,)))
        return cid

    def start(self):
        existing = self.store.one("SELECT MAX(ts) AS m FROM readings")
        if existing and existing["m"]:
            self._restore(existing["m"])
            self.sim_ts = existing["m"]
        elif self.sim_enabled and self.backfill > 0:
            self._backfill()
        else:
            self.sim_ts = time.time()
        if self.sim_enabled:
            self._thread = threading.Thread(target=self._sim_loop, daemon=True)
            self._thread.start()
        if config.WATCHDOG_SECONDS > 0:
            self._watch = threading.Thread(target=self._watchdog_loop, daemon=True)
            self._watch.start()

    def stop(self):
        self._stop.set()

    def _restore(self, latest_ts):
        self.latest_ts = latest_ts
        for cid, mon in self.monitors.items():
            rows = self.store.query("SELECT * FROM readings WHERE chamber_id=? ORDER BY id DESC LIMIT 400", (cid,))[::-1]
            for r in rows:
                a = mon.update({"temp_c": r["temp"], "humidity_pct": r["humidity"], "door_open": bool(r["door"]),
                                "compressor_on": None if r["comp"] is None else bool(r["comp"])})
            if rows:
                self.cur_level[cid] = a.level
                self._set_latest(cid, rows[-1]["ts"], rows[-1]["temp"], rows[-1]["humidity"], bool(rows[-1]["door"]),
                                 None if rows[-1]["comp"] is None else bool(rows[-1]["comp"]), a, rows[-1].get("current_a"))

    def _backfill(self):
        base = (time.time() // 60) * 60 - self.backfill * 60
        for i in range(self.backfill):
            ts = base + 60 * i
            mod = int(((ts + IST_OFFSET) // 60) % 1440)
            for cid, sim in self.sims.items():
                r = sim.step(mod)
                self.ingest(cid, r["temp_c"], r["humidity_pct"], r["door_open"], r["compressor_on"], ts=ts, quiet=True, current_a=r["compressor_current_a"])
        self.sim_ts = base + 60 * (self.backfill - 1)
        # demo lots, loaded at the start of the history
        for cid, name, bags in [(1, "Ramesh Kumar", 400), (1, "Sunita Devi", 250), (2, "Anil Yadav", 600)]:
            self.add_lot(cid, name, "+91 90000 1000%d" % cid, bags, loaded_ts=base)

    # ---- simulator loop -----------------------------------------------------
    def _sim_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            with self.sim_lock:
                self.sim_ts += 60
                mod = int(((self.sim_ts + IST_OFFSET) // 60) % 1440)
                readings = {cid: sim.step(mod) for cid, sim in self.sims.items()}
            self.latest_ts = max(self.latest_ts, self.sim_ts)         # the simulated clock keeps running even if a sensor is "unplugged"
            for cid, r in readings.items():
                if cid in self.sim_offline:
                    continue
                try:
                    self.ingest(cid, r["temp_c"], r["humidity_pct"], r["door_open"], r["compressor_on"], ts=self.sim_ts,
                                current_a=r["compressor_current_a"])
                except Exception as e:      # keep the demo alive
                    print("ingest error:", e)
            self._stop.wait(max(0.05, self.tick_seconds / self.speed - (time.time() - t0)))

    def set_scenario(self, chamber_id: int, scenario: str):
        with self.sim_lock:
            self.sims[chamber_id].set_scenario(scenario)

    def scenarios(self) -> dict:
        return {cid: s.scenario for cid, s in self.sims.items()}

    # ---- core: one reading -----------------------------------------------------
    def _set_latest(self, cid, ts, temp, hum, door, comp, a, current=None):
        code, hi, en = build_alert(a.reasons, cid, temp) if a.level != "OK" else ("none", "", "")
        self.latest[cid] = {"ts": ts, "temp": temp, "humidity": hum, "door": door, "comp": comp, "current_a": current, "level": a.level,
                            "score": a.score, "slope_per_min": a.slope, "eta_min": a.eta_min, "baseline": a.baseline,
                            "upper": a.upper, "learned": a.learned, "reason_code": code, "message_hi": hi, "message_en": en,
                            "reasons": [c for c, _ in a.reasons]}

    def ingest(self, chamber_id, temp_c, humidity_pct=90.0, door_open=False, compressor_on=None, ts=None, quiet=False,
               current_a=None) -> dict:
        with self.lock:
            if chamber_id not in self.chambers:
                raise KeyError(chamber_id)
            ch = self.chambers[chamber_id]
            ts = float(ts if ts is not None else time.time())
            if compressor_on is None and current_a is not None:      # optional current sensor decides if the compressor runs
                compressor_on = current_a >= config.COMP_ON_AMPS
            prev_seen = self.latest.get(chamber_id, {}).get("ts")
            a = self.monitors[chamber_id].update({"temp_c": temp_c, "humidity_pct": humidity_pct,
                                                  "door_open": door_open, "compressor_on": compressor_on})
            prev = self.last_hash[chamber_id]
            h = compute_hash(prev, payload(chamber_id, ts, temp_c, humidity_pct, door_open, compressor_on, current_a))
            self.store.execute(
                "INSERT INTO readings(chamber_id,ts,temp,humidity,door,comp,level,score,prev_hash,hash,current_a) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (chamber_id, ts, temp_c, humidity_pct, int(door_open), None if compressor_on is None else int(compressor_on),
                 a.level, a.score, prev, h, current_a))
            if compressor_on and prev_seen is not None and ts > prev_seen:      # compressor run-hours drive maintenance reminders
                dh = min(ts - prev_seen, 300.0) / 3600.0
                ch["run_hours"] = (ch.get("run_hours") or 0.0) + dh
                self.store.execute("UPDATE chambers SET run_hours=? WHERE id=?", (ch["run_hours"], chamber_id))
            self.last_hash[chamber_id] = h
            self.latest_ts = max(self.latest_ts, ts)
            self._set_latest(chamber_id, ts, temp_c, humidity_pct, door_open, compressor_on, a, current_a)
            if chamber_id in self.offline:                              # data is flowing again
                self._clear_offline(chamber_id, ts)

            old = self.cur_level[chamber_id]
            self.cur_level[chamber_id] = a.level
            if not quiet:
                if RANK[a.level] > RANK[old]:
                    info = self.latest[chamber_id]
                    alert_id = self.store.execute(
                        "INSERT INTO alerts(chamber_id,ts,level,code,message_hi,message_en,temp) VALUES(?,?,?,?,?,?,?)",
                        (chamber_id, ts, a.level, info["reason_code"], info["message_hi"], info["message_en"], temp_c))
                    lots = self.store.query("SELECT * FROM lots WHERE chamber_id=? AND withdrawn_ts IS NULL", (chamber_id,))
                    self.notifier.notify_alert(alert_id, ch, a.level, info["message_hi"], lots)
                elif a.level == "OK" and old != "OK":
                    self.store.execute("UPDATE alerts SET resolved_ts=? WHERE chamber_id=? AND resolved_ts IS NULL", (ts, chamber_id))
            return self.latest[chamber_id]

    # ---- watchdog: sensor offline + maintenance ----------------------------------
    def _watchdog_loop(self):
        while not self._stop.wait(config.WATCHDOG_SECONDS):
            try:
                self.check_offline()
                self.check_maintenance()
            except Exception as ex:
                print("watchdog error:", ex)

    def _clock(self) -> float:
        return self.latest_ts if self.sim_enabled else time.time()

    def check_offline(self, now=None, minutes=None) -> list[int]:
        """Raise an alert for chambers that stopped sending data. Returns the ids newly marked offline."""
        now = self._clock() if now is None else now
        limit = (config.OFFLINE_MINUTES if minutes is None else minutes) * 60
        newly = []
        with self.lock:
            for cid, lt in list(self.latest.items()):
                gap = now - lt["ts"]
                ch = self.chambers[cid]
                if gap < limit:
                    continue
                mins = int(gap // 60)
                if cid not in self.offline:
                    hi, en = OFFLINE_HI.format(n=cid, minutes=mins), OFFLINE_EN.format(n=cid, minutes=mins)
                    aid = self.store.execute("INSERT INTO alerts(chamber_id,ts,level,code,message_hi,message_en,temp) VALUES(?,?,?,?,?,?,?)",
                                             (cid, now, "WARNING", "offline", hi, en, lt["temp"]))
                    self.offline[cid] = {"since": lt["ts"], "alert_ids": [aid], "escalated": False}
                    self.notifier.notify_alert(aid, ch, "WARNING", hi, [])
                    newly.append(cid)
                elif not self.offline[cid]["escalated"] and gap >= 3 * limit:
                    # still silent after 3x the limit (e.g. a night power cut): escalate to the voice-call chain
                    hi, en = OFFLINE_HI.format(n=cid, minutes=mins), OFFLINE_EN.format(n=cid, minutes=mins)
                    aid = self.store.execute("INSERT INTO alerts(chamber_id,ts,level,code,message_hi,message_en,temp) VALUES(?,?,?,?,?,?,?)",
                                             (cid, now, "CRITICAL", "offline", hi, en, lt["temp"]))
                    self.offline[cid]["alert_ids"].append(aid)
                    self.offline[cid]["escalated"] = True
                    self.notifier.notify_alert(aid, ch, "CRITICAL", hi, [])
        return newly

    def _clear_offline(self, cid, ts):
        info = self.offline.pop(cid, None)
        if info:
            for aid in info["alert_ids"]:
                self.store.execute("UPDATE alerts SET resolved_ts=? WHERE id=? AND resolved_ts IS NULL", (ts, aid))

    def set_sim_offline(self, cid: int, offline: bool):
        (self.sim_offline.add if offline else self.sim_offline.discard)(cid)

    def check_maintenance(self, now=None) -> list[int]:
        now = self._clock() if now is None else now
        due_ids = []
        for t in self.store.query("SELECT * FROM maintenance"):
            ch = self.chambers.get(t["chamber_id"])
            if not ch:
                continue
            st = self.maintenance_status(t, now)
            if st["due"]:
                due_ids.append(t["id"])
                if not t["notified_ts"] or now - t["notified_ts"] > 7 * 86400:
                    contacts = ch["call_list"]
                    for p in {c["phone"]: c for c in contacts[:2]}.values():
                        self.notifier.send("whatsapp", p["name"], p["phone"], MAINT_HI.format(n=ch["id"], task=t["task"]), None, ch["id"])
                    self.store.execute("UPDATE maintenance SET notified_ts=? WHERE id=?", (now, t["id"]))
        return due_ids

    def maintenance_status(self, t, now=None) -> dict:
        now = self._clock() if now is None else now
        ch = self.chambers[t["chamber_id"]]
        reasons, left_h, left_d = [], None, None
        if t["interval_run_hours"]:
            left_h = t["interval_run_hours"] - (ch["run_hours"] - (t["last_done_run_hours"] or 0))
            if left_h <= 0:
                reasons.append("compressor run-hours")
        if t["interval_days"]:
            left_d = t["interval_days"] - (now - (t["last_done_ts"] or now)) / 86400
            if left_d <= 0:
                reasons.append("days")
        return {"due": bool(reasons), "due_by": reasons, "run_hours_left": left_h, "days_left": left_d}

    def maintenance_list(self, chamber_id=None) -> list[dict]:
        rows = self.store.query("SELECT * FROM maintenance" + (" WHERE chamber_id=?" if chamber_id else "") + " ORDER BY chamber_id,id",
                                (chamber_id,) if chamber_id else ())
        for r in rows:
            r.update(self.maintenance_status(r))
            r["chamber_name"] = self.chambers[r["chamber_id"]]["name"]
            r["run_hours_now"] = self.chambers[r["chamber_id"]]["run_hours"]
        return rows

    def add_maintenance(self, chamber_id, task, run_hours=None, days=None) -> int:
        if chamber_id not in self.chambers:
            raise KeyError(chamber_id)
        return self.store.execute("INSERT INTO maintenance(chamber_id,task,interval_run_hours,interval_days,last_done_ts,last_done_run_hours) VALUES(?,?,?,?,?,?)",
                                  (chamber_id, task, run_hours, days, self._clock() or time.time(), self.chambers[chamber_id]["run_hours"]))

    def maintenance_done(self, mid) -> bool:
        t = self.store.one("SELECT * FROM maintenance WHERE id=?", (mid,))
        if not t:
            return False
        self.store.execute("UPDATE maintenance SET last_done_ts=?, last_done_run_hours=?, notified_ts=NULL WHERE id=?",
                           (self._clock() or time.time(), self.chambers[t["chamber_id"]]["run_hours"], mid))
        return True

    # ---- alerts ------------------------------------------------------------------
    def ack(self, alert_id: int, who: str) -> bool:
        a = self.store.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        if not a:
            return False
        self.store.execute("UPDATE alerts SET acked_by=?, acked_ts=? WHERE chamber_id=? AND resolved_ts IS NULL AND acked_ts IS NULL",
                           (who, self.now(), a["chamber_id"]))
        self.store.execute("UPDATE alerts SET acked_by=COALESCE(acked_by,?), acked_ts=COALESCE(acked_ts,?) WHERE id=?",
                           (who, self.now(), alert_id))
        return True

    # ---- lots ---------------------------------------------------------------------
    def add_lot(self, chamber_id, farmer_name, farmer_phone, bags, loaded_ts=None) -> int:
        if chamber_id not in self.chambers:
            raise KeyError(chamber_id)
        return self.store.execute("INSERT INTO lots(chamber_id,farmer_name,farmer_phone,bags,loaded_ts,token) VALUES(?,?,?,?,?,?)",
                                  (chamber_id, farmer_name, farmer_phone, bags, loaded_ts or self.now(), secrets.token_urlsafe(12)))

    def withdraw_lot(self, lot_id) -> bool:
        lot = self.store.one("SELECT * FROM lots WHERE id=?", (lot_id,))
        if not lot or lot["withdrawn_ts"]:
            return False
        self.store.execute("UPDATE lots SET withdrawn_ts=? WHERE id=?", (self.now(), lot_id))
        return True

    def lot_summary(self, lot_id) -> dict | None:
        lot = self.store.one("SELECT * FROM lots WHERE id=?", (lot_id,))
        if not lot:
            return None
        ch = self.chambers[lot["chamber_id"]]
        end = lot["withdrawn_ts"] or self.now()
        rows = self.store.query("SELECT * FROM readings WHERE chamber_id=? AND ts>=? AND ts<=? ORDER BY id",
                                (lot["chamber_id"], lot["loaded_ts"], end))
        temps = np.array([r["temp"] for r in rows]) if rows else np.array([0.0])
        hums = np.array([r["humidity"] for r in rows]) if rows else np.array([0.0])
        outside = int(np.sum((temps > ch["temp_max"]) | (temps < ch["temp_min"]))) if rows else 0
        n_alerts = self.store.one("SELECT COUNT(*) AS c FROM alerts WHERE chamber_id=? AND ts>=? AND ts<=? AND level IN ('WARNING','CRITICAL')",
                                  (lot["chamber_id"], lot["loaded_ts"], end))["c"]
        risk = spoilage_risk([r["ts"] for r in rows], [r["temp"] for r in rows], ch["temp_max"], ch["temp_min"], config.SPOIL_K) if rows \
            else {"score": 0.0, "level": "LOW", "degree_hours": 0.0}
        return {"lot": lot, "chamber": ch, "now": self.now(), "readings": len(rows), "spoilage": risk,
                "avg_temp": float(temps.mean()), "min_temp": float(temps.min()), "max_temp": float(temps.max()),
                "avg_hum": float(hums.mean()), "minutes_outside": outside, "alerts": n_alerts,
                "chain": verify_rows(rows) if rows else {"ok": True, "checked": 0, "first_bad_id": None, "reason": ""},
                "head_hash": rows[-1]["hash"] if rows else GENESIS}

    def farmer_update(self, lot_id) -> str | None:
        s = self.lot_summary(lot_id)
        if not s:
            return None
        lot = s["lot"]
        msg = FARMER_WEEKLY_HI.format(name=lot["farmer_name"], bags=lot["bags"], n=lot["chamber_id"], avg=round(s["avg_temp"], 1),
                                      url=f"{config.PUBLIC_URL}/f/{lot['token']}")
        self.notifier.send("whatsapp", lot["farmer_name"] + " (farmer)", lot["farmer_phone"], msg, None, lot["chamber_id"])
        return msg

    # ---- spoilage + weekly report --------------------------------------------------
    def chamber_spoilage(self, cid, hours=24) -> dict:
        ch = self.chambers[cid]
        rows = self.store.query("SELECT ts,temp FROM readings WHERE chamber_id=? AND ts>=? ORDER BY id", (cid, self.now() - hours * 3600))
        return spoilage_risk([r["ts"] for r in rows], [r["temp"] for r in rows], ch["temp_max"], ch["temp_min"], config.SPOIL_K)

    def weekly_report_data(self, days=7, site_id=None) -> dict:
        end = self.now()
        start = end - days * 86400
        sites = {s["id"]: s["name"] for s in self.store.query("SELECT id,name FROM sites")}
        out = []
        for cid, ch in self.chambers.items():
            if site_id is not None and ch.get("site_id") != site_id:
                continue
            rows = self.store.query("SELECT ts,temp FROM readings WHERE chamber_id=? AND ts>=? ORDER BY id", (cid, start))
            temps = np.array([r["temp"] for r in rows]) if rows else np.array([0.0])
            span_min = max(1.0, (rows[-1]["ts"] - rows[0]["ts"]) / 60 + 1) if rows else 1.0
            al = self.store.query("SELECT * FROM alerts WHERE chamber_id=? AND ts>=?", (cid, start))
            out.append({"id": cid, "name": ch["name"], "site": sites.get(ch.get("site_id"), "-"),
                        "uptime_pct": min(100.0, 100.0 * len(rows) / span_min) if rows else 0.0,
                        "avg": float(temps.mean()), "min": float(temps.min()), "max": float(temps.max()),
                        "minutes_outside": int(np.sum((temps > ch["temp_max"]) | (temps < ch["temp_min"]))) if rows else 0,
                        "warnings": sum(1 for a in al if a["level"] == "WARNING"), "criticals": sum(1 for a in al if a["level"] == "CRITICAL"),
                        "offline_events": sum(1 for a in al if a["code"] == "offline" and a["level"] == "WARNING"),
                        "spoilage": spoilage_risk([r["ts"] for r in rows], [r["temp"] for r in rows], ch["temp_max"], ch["temp_min"], config.SPOIL_K)})
        alerts = self.store.query("SELECT * FROM alerts WHERE ts>=? AND level IN ('WARNING','CRITICAL') ORDER BY id DESC", (start,))
        maintenance = self.maintenance_list()
        if site_id is not None:
            keep = {c["id"] for c in out}
            alerts = [a for a in alerts if a["chamber_id"] in keep]
            maintenance = [m for m in maintenance if m["chamber_id"] in keep]
        return {"start": start, "end": end, "chambers": out, "alerts": alerts, "maintenance": maintenance}

    def weekly_summary_hi(self, days=7) -> str:
        d = self.weekly_report_data(days)
        lines = [f"चैंबर {c['id']}: सेंसर चालू {c['uptime_pct']:.0f}%, औसत {c['avg']:.1f}°C, सीमा से बाहर {c['minutes_outside']} मिनट" for c in d["chambers"]]
        return "साप्ताहिक रिपोर्ट:\n" + "\n".join(lines)

    # ---- tamper demo ----------------------------------------------------------------
    def tamper(self, chamber_id: int) -> int | None:
        with self.lock:
            row = self.store.one("SELECT id,temp FROM readings WHERE chamber_id=? ORDER BY id DESC LIMIT 1 OFFSET 20", (chamber_id,))
            if not row or chamber_id in self.tampered:
                return None
            self.tampered[chamber_id] = (row["id"], row["temp"])
            self.store.execute("UPDATE readings SET temp=temp+4.0 WHERE id=?", (row["id"],))
            return row["id"]

    def restore(self, chamber_id: int) -> bool:
        with self.lock:
            if chamber_id not in self.tampered:
                return False
            rid, orig = self.tampered.pop(chamber_id)
            self.store.execute("UPDATE readings SET temp=? WHERE id=?", (orig, rid))
            return True

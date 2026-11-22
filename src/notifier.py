"""Notifications: WhatsApp / SMS / automated Hindi voice call with escalation.

MOCK mode (default): every message is stored in the `notifications` table and shown in the dashboard,
nothing is sent. TWILIO mode (set TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM): sends for real through
Twilio's REST API. The Twilio path has not been exercised against a live account in this build.
"""
from __future__ import annotations
import threading
import time
import requests
from src import messages


def _normalize(phone: str) -> str:
    return "+" + "".join(ch for ch in phone if ch.isdigit())


class Notifier:
    def __init__(self, store, mode: str, wait_seconds: float, now_fn, cfg=None):
        self.store, self.mode, self.wait = store, mode, wait_seconds
        self.now = now_fn
        self.cfg = cfg
        self._escalations: dict[int, threading.Thread] = {}

    # ---- low-level send -------------------------------------------------
    def send(self, channel, to_name, to_phone, message, alert_id=None, chamber_id=None, status=None) -> int:
        st = status or ("mock-sent" if self.mode == "mock" else self._twilio_send(channel, to_phone, message, alert_id))
        return self.store.execute(
            "INSERT INTO notifications(ts,alert_id,chamber_id,channel,to_name,to_phone,message,status,mode) VALUES(?,?,?,?,?,?,?,?,?)",
            (self.now(), alert_id, chamber_id, channel, to_name, to_phone, message, st, self.mode))

    def _twilio_send(self, channel, to_phone, message, alert_id) -> str:
        c = self.cfg
        base = f"https://api.twilio.com/2010-04-01/Accounts/{c.TWILIO_SID}"
        to = _normalize(to_phone)
        try:
            if channel == "voice":
                twiml = (f'<Response><Gather numDigits="1" action="{c.PUBLIC_URL}/voice/gather/{alert_id}?who={to}">'
                         f'<Say language="hi-IN">{message}</Say></Gather></Response>')
                r = requests.post(base + "/Calls.json", auth=(c.TWILIO_SID, c.TWILIO_TOKEN),
                                  data={"To": to, "From": c.TWILIO_FROM, "Twiml": twiml}, timeout=10)
            else:
                frm, dst = (c.TWILIO_WHATSAPP_FROM, "whatsapp:" + to) if channel == "whatsapp" else (c.TWILIO_FROM, to)
                r = requests.post(base + "/Messages.json", auth=(c.TWILIO_SID, c.TWILIO_TOKEN),
                                  data={"To": dst, "From": frm, "Body": message}, timeout=10)
            return "sent" if r.status_code < 300 else f"failed: HTTP {r.status_code}"
        except Exception as e:  # network problems must never break monitoring
            return f"failed: {type(e).__name__}"

    # ---- alert fan-out ----------------------------------------------------
    def notify_alert(self, alert_id, chamber, level, hi_text, lots):
        contacts = chamber["call_list"]
        owner, manager = contacts[0], contacts[1] if len(contacts) > 1 else contacts[0]
        cid = chamber["id"]
        if level == "WATCH":
            self.send("whatsapp", owner["name"], owner["phone"], hi_text, alert_id, cid)
            return
        for person in {id(owner): owner, id(manager): manager}.values():
            self.send("whatsapp", person["name"], person["phone"], hi_text, alert_id, cid)
            self.send("sms", person["name"], person["phone"], hi_text, alert_id, cid)
        if level == "CRITICAL":
            for lot in lots:     # farmer lot-linked update
                msg = messages.FARMER_ALARM_HI.format(name=lot["farmer_name"], n=cid)
                self.send("whatsapp", lot["farmer_name"] + " (farmer)", lot["farmer_phone"], msg, alert_id, cid)
            self.start_escalation(alert_id, chamber)

    # ---- voice-call escalation: owner -> manager -> technician -------------
    def start_escalation(self, alert_id, chamber):
        if alert_id in self._escalations:
            return
        t = threading.Thread(target=self._escalate, args=(alert_id, chamber), daemon=True)
        self._escalations[alert_id] = t
        t.start()

    def _alert_closed(self, alert_id) -> bool:
        row = self.store.one("SELECT acked_ts, resolved_ts FROM alerts WHERE id=?", (alert_id,))
        return bool(row and (row["acked_ts"] or row["resolved_ts"]))

    def _escalate(self, alert_id, chamber):
        voice = messages.VOICE_HI.format(n=chamber["id"])
        for person in chamber["call_list"]:
            if self._alert_closed(alert_id):
                return
            nid = self.send("voice", person["name"], person["phone"], voice, alert_id, chamber["id"], status="calling")
            deadline = time.time() + self.wait
            while time.time() < deadline:
                if self._alert_closed(alert_id):
                    self.store.execute("UPDATE notifications SET status='answered (key 1 pressed)' WHERE id=?", (nid,))
                    return
                time.sleep(0.2)
            self.store.execute("UPDATE notifications SET status='no answer' WHERE id=?", (nid,))
        if not self._alert_closed(alert_id):
            self.send("system", "ColdTrust", "-", "Call list exhausted without acknowledgement.", alert_id, chamber["id"], status="escalation-exhausted")

"""ColdTrust configuration. Everything can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("COLDTRUST_DB", str(BASE_DIR / "coldtrust.db"))
MODEL_PATH = os.getenv("COLDTRUST_MODEL", str(BASE_DIR / "models" / "iforest.joblib"))
OUTPUT_DIR = BASE_DIR / "outputs"

SIM_ENABLED = os.getenv("SIM", "1") == "1"
TICK_SECONDS = float(os.getenv("TICK_SECONDS", "1.0"))   # 1 tick = 1 simulated minute
BACKFILL_MINUTES = int(os.getenv("BACKFILL_MINUTES", "360"))

ESCALATION_WAIT = float(os.getenv("ESCALATION_WAIT", "20"))  # seconds between voice-call attempts (demo scale)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "/dashboard")   # where the website's "Dashboard Login" button points
API_URL = os.getenv("API_URL", "http://localhost:8000")

# Sensor-offline watchdog, login, device key
OFFLINE_MINUTES = float(os.getenv("OFFLINE_MINUTES", "5"))        # no reading for this long -> sensor-offline alert
WATCHDOG_SECONDS = float(os.getenv("WATCHDOG_SECONDS", "15"))
AUTH_ENABLED = os.getenv("AUTH", "1") == "1"
DEVICE_KEY = os.getenv("DEVICE_KEY", "demo-device-key")           # ESP32 sends it in the X-Device-Key header
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
COMP_ON_AMPS = float(os.getenv("COMP_ON_AMPS", "1.0"))            # compressor current above this = compressor ON
SPOIL_K = float(os.getenv("SPOIL_K", "6.0"))                      # degC-hours above limit at which risk reaches ~63 (assumption)

# Optional real messaging (Twilio). If not set, notifications run in MOCK mode
# (they are stored and shown in the dashboard, nothing is sent).
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
NOTIFY_MODE = "twilio" if (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM) else "mock"

# Demo chambers (values are configurable; confirm real ranges with storage owners)
DEMO_SITES = [
    {"id": 1, "name": "Agra Cold Storage",    "location": "Agra, UP",    "lat": 27.1767, "lon": 78.0081},
    {"id": 2, "name": "Aligarh Cold Storage", "location": "Aligarh, UP", "lat": 27.8974, "lon": 78.0880},
]
DEMO_CHAMBERS = [
    {"id": 1, "site_id": 1, "name": "Chamber 1", "crop": "Potato (seed)",  "setpoint": 3.0, "temp_max": 5.0, "temp_min": 1.0},
    {"id": 2, "site_id": 1, "name": "Chamber 2", "crop": "Potato (table)", "setpoint": 3.5, "temp_max": 5.5, "temp_min": 1.5},
    {"id": 3, "site_id": 2, "name": "Chamber 3", "crop": "Fruit & veg",    "setpoint": 5.0, "temp_max": 7.0, "temp_min": 3.0},
]
# (task, every N compressor run-hours or None, every N days or None)
DEFAULT_MAINTENANCE = [
    ("Compressor oil and gas check", 500, None),
    ("Condenser coil cleaning", None, 90),
    ("Door gasket and sensor calibration check", None, 30),
]
DEMO_CONTACTS = [
    {"role": "Owner",      "name": "Owner (demo)",      "phone": "+91 90000 00001"},
    {"role": "Manager",    "name": "Manager (demo)",    "phone": "+91 90000 00002"},
    {"role": "Technician", "name": "Technician (demo)", "phone": "+91 90000 00003"},
]

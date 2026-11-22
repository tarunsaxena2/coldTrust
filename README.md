# ColdTrust - working prototype

Low-cost IoT + AI monitoring for cold-storage chambers: learns each chamber's normal pattern, warns early
(Watch → Warning → Critical) in Hindi on WhatsApp/SMS, escalates unanswered critical alerts to automatic Hindi
voice calls, links every farmer's lot to its chamber, and issues a tamper-evident storage certificate with a QR.

Everything runs on a laptop with **no hardware and no paid accounts**. A built-in simulator plays the role of the
ESP32 sensor nodes; WhatsApp/SMS/voice run in MOCK mode (shown in the dashboard instead of being sent).

## Run it
```bash
pip install -r requirements.txt
python run_demo.py          # website + dashboard + API, all on ONE link: http://localhost:8000
```
Open http://localhost:8000 for the public website (its *Dashboard Login* button opens the dashboard at http://localhost:8000/dashboard; API docs at /docs). (Or: `docker compose up --build`. Not tested in the build environment.)
Other commands: `python -m pytest` (tests), `python evaluate.py` (offline model evaluation), `python -m src.train_model` (retrain).

## 5-minute showcase script
1. **Live Monitor**: three chambers, learned normal band (green), limit line (red). 1 second = 1 minute of chamber time.
2. Pick Chamber 1 → **Compressor trip**. Watch the temperature drift: Watch (amber), Warning (orange), Critical (red),
   each with a Hindi message and the estimated minutes to the limit.
3. **Alerts & Notifications**: WhatsApp/SMS to owner and manager, a Hindi update to each farmer with a lot in that chamber,
   then voice calls owner → manager → technician. Press *Acknowledge* and the calls stop.
4. **Lots & Certificates**: download the PDF certificate (QR opens the verification page). Click *Tamper with a stored
   reading* → the chain check turns red; *Restore* → green again.
5. **Loss Avoided**: change the assumptions and show benefit/cost for the Rs. 4,000 + Rs. 1,200/month plan.
6. **Model Performance**: false alarms and early-warning lead time from `evaluate.py`.
Other faults to try: Power cut, Door left open, Cooling degradation. *Fix / reset* restores the chamber.

## Website
`website/index.html` is the public marketing site (hero carousel, products, technology, contact, cookie banner). The API serves it at `/` and `/site`; set `DASHBOARD_URL` if the dashboard runs elsewhere.
The dashboard login page and pages also got a crop-emoji theme and the top-cut fix (`src/theme.py`).

## Careers, applications and suggestions
| What | Where |
|---|---|
| Public careers page with filters, job cards and an *Apply now* form | `/careers` (`website/careers.html`, edit the `JOBS` list to change roles) |
| Job applications go to the owner and manager only | dashboard page **Job Applications** (status, internal note, new-count badge) |
| Suggestion box on the careers page and the website contact form | dashboard page **Suggestions** (status, internal note, new-count badge) |
| API | `POST /careers/apply`, `POST /suggestions` (public, honeypot + rate limit); `GET /careers/applications`, `GET /suggestions` and their `/summary` and `/status` routes (owner/manager) |

## New in this version
| Feature | What it does | Where |
|---|---|---|
| Sensor-offline alert | No data for 5 min -> Warning (Hindi WhatsApp/SMS); still silent after 15 min -> Critical with the voice-call chain. Auto-resolves when data returns. Demo: *Unplug sensor* on the Live Monitor | `Engine.check_offline`, `OFFLINE_MINUTES` |
| Offline buffering (node) | ESP32 saves readings in flash while the network is down and flushes them with `POST /ingest/batch` | `esp32_firmware/`, `/ingest/batch` |
| Login + roles | Owner and manager logins (PBKDF2 + bearer tokens); sensors use a device key (`X-Device-Key`); farmers use a private link. Owner-only: users, sites, simulator, tamper demo | `src/auth.py`, `api.py` |
| Farmer mobile page | `/f/<token>`: Hindi status, 24 h graph, spoilage estimate, certificate, no login, only that lot. The WhatsApp update contains the link | `src/farmer_page.py` |
| Hindi voice menu | Key 1 = acknowledge, key 2 = hear the current temperature | `/voice/gather/{id}` |
| Spoilage risk score | Simple degree-hours-above-limit estimate (0-100), per lot, chamber, certificate and farmer page | `src/spoilage.py`, `SPOIL_K` |
| Weekly report | PDF with uptime, temperatures, alerts, spoilage and maintenance per chamber + Hindi summary message | `src/report.py`, `/reports/weekly` |
| Compressor current | Optional `compressor_current_a`; above `COMP_ON_AMPS` = compressor ON; included in the hash chain | `/ingest`, firmware `HAS_CT` |
| Multi-chamber, multi-site | Sites with location, chambers per site, site filter, add site/chamber from the Admin page | `/sites`, `/chambers` |
| Maintenance reminders | Tasks by days or compressor run-hours, Hindi reminder when due, mark done | `/maintenance` |
| Network map | Map of sites coloured by their worst chamber level | dashboard *Network Map* |
| Retrain on real data | `python -m src.retrain_real --db coldtrust.db` after 2-3 weeks of real readings | `src/retrain_real.py` |

Default logins for the demo: **owner / owner123** and **manager / manager123**. Set `OWNER_PASSWORD` and `MANAGER_PASSWORD` (and
`DEVICE_KEY`) before any real use; set `AUTH=0` only for local experiments. Existing databases are migrated automatically.

## Roles (who can do what)
Create users in the dashboard: **Admin → Users** (owner only). The API enforces these rules; the dashboard only hides the buttons.

| Role | Sees | Can do | Cannot do |
|---|---|---|---|
| **owner** | everything, all sites | everything incl. users, sites, chambers, simulator, tamper demo | - |
| **manager** | all sites | monitor, acknowledge alerts, lots, certificates, maintenance, reports, weekly WhatsApp summary | users, sites, simulator |
| **site_manager** | **only its own site** (chambers, alerts, lots, maintenance, report) | same as manager for that site | other sites, weekly WhatsApp summary, owner functions |
| **technician** | chambers, alerts, notifications, maintenance, map | acknowledge alerts, mark maintenance done | lots, certificates, reports, add tasks |
| **accountant** | lots, certificates, weekly report, chamber list | register / withdraw lots, message farmers, download certificates and reports | alerts, maintenance, live readings, simulator |
| **viewer** | monitoring, lots, certificates, reports | read only | every change |
| **farmer** | only the lots whose phone number matches his login | Hindi status, 24 h graph, spoilage estimate, certificate | everything else (no staff data, no phone numbers) |

`site_manager` needs a site when created; `farmer` needs a phone number (the same number as on his lots, any format). Rules live in `src/access.py`.
Quick start: `python create_demo_users.py` creates one demo login per role (owner/owner123, manager/manager123, sitemgr/sitemgr123, tech/tech123, accountant/account123, viewer/viewer123, farmer/farmer123) plus a demo lot for the farmer. Demo passwords only.
Farmers can still use the private link of a lot (`/f/<token>`) without any login. Voice-call escalation still uses the contacts in `config.py`; a login does not add a phone number to it.

## Look and feel
Green (crops) + harvest gold + warm cream, with a hero banner, branded login screen, page icons and a footer. Colours and CSS live in `src/theme.py`;
the Streamlit theme is in `.streamlit/config.toml` (light theme, so start the dashboard from the project folder, e.g. `python run_demo.py`).
The farmer WhatsApp page (`/f/<token>`) and the public `/verify/<id>` page use the same palette. Change the brand colours in `src/theme.py` and `.streamlit/config.toml`.

## How it works
```
ESP32 node (or simulator) --POST /ingest--> FastAPI --> SQLite (readings + SHA-256 hash chain)
                                               |--> anomaly engine (learned band + rate of change + door/compressor context + IsolationForest)
                                               |--> alerts --> notifier (WhatsApp / SMS / voice escalation, mock or Twilio)
                                               |--> lots, certificate PDF (reportlab + QR), /verify page
Dashboard (website/dashboard.html, served by FastAPI at /dashboard) <-- REST API
```
| File | Role |
|---|---|
| `api.py` | REST API (`/ingest`, `/chambers`, `/alerts`, `/lots`, `/verify/{id}` ...). Docs at `/docs` |
| `src/simulator.py` | chamber physics: thermostat cycling, door events, 4 injectable faults |
| `src/anomaly.py` | detection logic and the 3 alert levels |
| `src/train_model.py` | trains the IsolationForest on simulated normal operation |
| `src/notifier.py`, `src/messages.py` | Hindi texts, mock/Twilio sending, voice escalation |
| `src/audit_chain.py`, `src/certificate.py` | tamper-evident records and certificate |
| `esp32_firmware/` | starter sketch for the real sensor node |

## Measured on the simulator (`python evaluate.py`)
- False alarms in normal operation: **1 episode in 42 simulated chamber-days (0.17 per chamber-week)**.
- All 48 injected faults (12 per type) were detected.
- A fixed 5.0 °C alarm fires late; ColdTrust's first *Watch* came on average **12-21 minutes earlier** depending on the fault
  (compressor trip 19, power cut 12, door left open 15, cooling degradation 21), and *Critical* **5-10 minutes earlier** on average
  (worst single run 1-4 minutes, when a door opening hid the start of the fault).
These come from a simulator I wrote, so they show the logic works, not how it will perform in real chambers.

## Moving to the real thing
- **Sensors**: flash `esp32_firmware/coldtrust_node` (untested on hardware); it posts to the same `/ingest`. Run the API with `SIM=0`.
- **WhatsApp / SMS / voice**: set `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM` (and `PUBLIC_URL` reachable from the internet, e.g. via ngrok).
  The Twilio code path has not been run against a live account. WhatsApp Business templates and TRAI rules apply for production.
- **Thresholds**: chamber set-points and limits in `config.py` are demo values. Confirm real ranges with storage owners.
- **Data**: after 2-3 weeks of real readings per chamber, retrain `train_model.py` on real data.

## Known limitations
- All sensor data and the evaluation are simulated; the model has never seen a real cold storage.
- Certificate integrity: the hash chain detects edits to stored readings, but someone with full database access could rewrite the
  whole chain. Anchor the latest fingerprint outside the database (it is printed on the certificate) for stronger guarantees.
- Still not implemented: SMS fallback from the node (needs a GSM module), per-site user permissions (every login sees all sites), billing-software
  integration, login rate-limiting, HTTPS (put the API behind a reverse proxy such as Caddy/nginx before exposing it), Twilio webhook signature checking.
- The offline buffer and compressor-current code in the ESP32 firmware, and the spoilage constant `SPOIL_K`, are untested on real hardware / real crops.
  A backlog flushed after an outage can raise alerts with old time stamps. The public `/verify/<id>` page masks the farmer's surname, but lot ids are sequential.
- Compressor run-hours and maintenance due-dates follow the simulated clock in demo mode.
- Hindi PDF text is romanised (the PDF font has no Devanagari); dashboard and messages use Devanagari.
- The simulator's random events are seeded, so a demo restart repeats the same door events.

# Hardware test checklist (do this before a pilot)

Nothing in `esp32_firmware/` has run on a real board yet. Work through these steps with one node on a bench, then in one chamber.

1. **Bench**: flash the sketch, set `WIFI_*`, `API_BASE`, `DEVICE_KEY`, `CHAMBER_ID`. Serial monitor at 115200 should show `POST /ingest -> 200` every minute.
2. **Accuracy**: compare the SHT31 with a calibrated reference thermometer in ice water (0 C) and at room temperature. Note the offset; correct it in the sketch if > 0.3 C.
3. **Door switch**: open/close the door; the dashboard must show the door state change within a minute.
4. **Offline buffer**: unplug the router for 10 minutes. Serial must print `buffered reading in flash`. Plug it back: `POST /ingest/batch -> 200`, then the
   dashboard graph has no gap and the *sensor offline* alert resolves.
5. **Power cut**: pull the node's power for 10 minutes. Expect the *sensor offline* Warning at 5 min and the Critical + voice call at 15 min.
6. **Current sensor** (optional): set `HAS_CT = true`, compare the printed amps with a clamp meter and set `CT_AMPS_PER_VOLT`; check `COMP_ON_AMPS` on the server.
7. **Run 2-3 weeks** in one real chamber, then: `python -m src.retrain_real --db coldtrust.db --out models/iforest_real.joblib` and start the API with
   `COLDTRUST_MODEL=models/iforest_real.joblib`. Record false alarms and missed events honestly; replace the simulator numbers in your pitch with these.
8. **Calibrate** `SPOIL_K` and the chamber limits with the storage owner (crop-specific).

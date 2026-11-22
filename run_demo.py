"""Start ColdTrust (website + dashboard + API on ONE link):  python run_demo.py"""
import threading
import time
import webbrowser
import uvicorn
import config

PORT = 8000


def _open_browser():
    time.sleep(2)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass


if __name__ == "__main__":
    print(f"\nColdTrust is running on ONE link:\n  Website:   http://localhost:{PORT}\n  Dashboard: http://localhost:{PORT}/dashboard\n"
          f"  API docs:  http://localhost:{PORT}/docs\n  Demo logins: owner / owner123  and  manager / manager123\n  (Ctrl+C to stop)\n")
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run("api:app", host="0.0.0.0", port=PORT, log_level="warning", app_dir=str(config.BASE_DIR))

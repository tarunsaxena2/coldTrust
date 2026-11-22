"""Mobile-friendly page a farmer opens from the WhatsApp link (no app, no login). Shows only that farmer's lot."""
from __future__ import annotations
import html
from src.certificate import fmt_ts

RISK_HI = {"LOW": ("कम जोखिम", "#1b7f5c"), "MODERATE": ("मध्यम जोखिम", "#ef6c00"), "HIGH": ("ज़्यादा जोखिम", "#d32f2f")}


def _svg(points, tmax, tmin, w=320, h=110):
    if len(points) < 2:
        return ""
    step = max(1, len(points) // 120)
    pts = points[::step]
    ts0, ts1 = pts[0][0], pts[-1][0]
    lo, hi = min(min(p[1] for p in pts), tmin) - 0.5, max(max(p[1] for p in pts), tmax) + 0.5
    X = lambda t: 4 + (w - 8) * (t - ts0) / max(1.0, ts1 - ts0)
    Y = lambda v: h - 4 - (h - 8) * (v - lo) / (hi - lo)
    line = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in pts)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="temperature last 24 hours">'
            f'<line x1="0" x2="{w}" y1="{Y(tmax):.1f}" y2="{Y(tmax):.1f}" stroke="#d32f2f" stroke-dasharray="4 3"/>'
            f'<polyline fill="none" stroke="#1565c0" stroke-width="2" points="{line}"/></svg>')


def render(summary: dict, readings: list[dict], token: str, cert_url: str, verify_url: str) -> str:
    lot, ch = summary["lot"], summary["chamber"]
    ok = summary["minutes_outside"] == 0 and summary["chain"]["ok"]
    colour, head_hi, head_en = ("#1b7f5c", "सब ठीक है", "All good") if ok else ("#ef6c00", "कुछ देर तापमान सीमा से बाहर रहा", "Temperature was outside the limit for some time")
    rk = summary["spoilage"]
    rk_hi, rk_col = RISK_HI[rk["level"]]
    pts = [(r["ts"], r["temp"]) for r in readings]
    e = html.escape
    return f"""<!doctype html><html lang="hi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>ColdTrust - {e(lot['farmer_name'])}</title>
<style>.hb{{height:130px;background:linear-gradient(transparent 30%,#0a2a19cc),url(/site-assets/sunset.webp) center/cover;border-radius:0 0 22px 22px;margin:0 0 4px}}</style><style>body{{font-family:system-ui,sans-serif;margin:0;background:#f1f6ec;color:#1c2b33}}.w{{max-width:480px;margin:0 auto;padding:16px}}.t{{display:flex;align-items:center;gap:8px;color:#14532d;font-weight:800;font-size:19px;margin:2px 2px 12px}}.t small{{font-weight:500;font-size:12px;color:#4b6b55;margin-left:auto}}
.b{{background:{colour};color:#fff;border-radius:14px;padding:18px;font-size:22px;font-weight:700}}.b small{{display:block;font-size:14px;font-weight:400;opacity:.9}}
.c{{background:#fff;border-radius:14px;padding:14px 16px;margin-top:12px;box-shadow:0 1px 3px #0001}}.g{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.g div b{{display:block;font-size:22px}}.g div span{{font-size:13px;color:#5b6b75}}a.btn{{display:block;text-align:center;background:#1f6f3d;color:#fff;
padding:13px;border-radius:12px;text-decoration:none;margin-top:12px;font-weight:600}}.p{{color:#777;font-size:12px;margin-top:14px}}</style></head><body><div class="hb"></div><div class="w">
<div class="t">🌾 ColdTrust<small>किसान का भरोसा</small></div>
<div class="b">{head_hi}<small>{head_en}</small></div>
<div class="c"><b>{e(lot['farmer_name'])}</b> जी · {lot['bags']} बोरी · {e(ch['name'])} ({e(ch['crop'])})<br>
<span style="font-size:13px;color:#5b6b75">{fmt_ts(lot['loaded_ts'])} से {fmt_ts(lot['withdrawn_ts'] or summary['now'])}</span></div>
<div class="c g"><div><b>{summary['avg_temp']:.1f}°C</b><span>औसत तापमान</span></div><div><b>{summary['minutes_outside']} मिनट</b><span>सीमा से बाहर</span></div>
<div><b>{summary['min_temp']:.1f}–{summary['max_temp']:.1f}°C</b><span>न्यूनतम–अधिकतम</span></div><div><b style="color:{rk_col}">{rk_hi}</b><span>ख़राबी का अनुमान ({rk['score']:.0f}/100)</span></div></div>
<div class="c"><b>पिछले 24 घंटे का तापमान</b>{_svg(pts, ch['temp_max'], ch['temp_min'])}<span style="font-size:12px;color:#5b6b75">लाल रेखा = अधिकतम सीमा {ch['temp_max']:.1f}°C</span></div>
<a class="btn" href="{cert_url}">📄 सर्टिफ़िकेट डाउनलोड करें</a><a class="btn" style="background:#1565c0" href="{verify_url}">🔒 रिकॉर्ड की जाँच करें</a>
<p class="p">ख़राबी का अनुमान एक सरल गणना है (सीमा से ऊपर कितने डिग्री-घंटे), प्रयोगशाला-जाँच नहीं। Prototype with simulated data.</p></div></body></html>"""

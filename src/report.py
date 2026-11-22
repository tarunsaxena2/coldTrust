"""Weekly PDF report for the owner: uptime, temperatures, alerts, spoilage risk and maintenance per chamber."""
from __future__ import annotations
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from src.certificate import fmt_ts


def build_weekly_report(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=12 * mm,
                            title="ColdTrust weekly report")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=colors.HexColor("#0B5D4B"), fontSize=22)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5, textColor=colors.HexColor("#555555"))
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F3EF")),
                       ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("PADDING", (0, 0), (-1, -1), 4), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    story = [Paragraph("ColdTrust", h1), Paragraph(f"Weekly report - {fmt_ts(data['start'])} to {fmt_ts(data['end'])}", ss["Heading2"]), Spacer(1, 3 * mm)]
    rows = [["Chamber", "Sensor uptime", "Avg / min / max C", "Min. outside limit", "Alerts W/C", "Offline events", "Spoilage risk"]]
    for c in data["chambers"]:
        rows.append([f"{c['name']}\n({c['site']})", f"{c['uptime_pct']:.0f}%", f"{c['avg']:.1f} / {c['min']:.1f} / {c['max']:.1f}", str(c["minutes_outside"]),
                     f"{c['warnings']} / {c['criticals']}", str(c["offline_events"]), f"{c['spoilage']['level']} ({c['spoilage']['score']:.0f})"])
    t = Table(rows, colWidths=[34 * mm, 22 * mm, 33 * mm, 27 * mm, 20 * mm, 22 * mm, 26 * mm]); t.setStyle(grid)
    story += [t, Spacer(1, 5 * mm), Paragraph("Maintenance", ss["Heading3"])]
    m = [["Chamber", "Task", "Status"]] + [[x["chamber_name"], x["task"], "DUE NOW" if x["due"] else
         (f"in {x['days_left']:.0f} days" if x["days_left"] is not None else f"in {x['run_hours_left']:.0f} compressor-hours")] for x in data["maintenance"]]
    t2 = Table(m, colWidths=[40 * mm, 90 * mm, 50 * mm]); t2.setStyle(grid)
    story += [t2, Spacer(1, 5 * mm), Paragraph("Alerts in this period", ss["Heading3"])]
    a = [["Time", "Chamber", "Level", "Message"]] + [[fmt_ts(x["ts"]), str(x["chamber_id"]), x["level"], x["message_en"][:95]] for x in data["alerts"][:25]]
    if len(a) == 1:
        a.append(["-", "-", "-", "No alerts in this period."])
    t3 = Table(a, colWidths=[34 * mm, 18 * mm, 22 * mm, 106 * mm]); t3.setStyle(grid)
    story += [t3, Spacer(1, 5 * mm),
              Paragraph("Spoilage risk is a simple heuristic (degree-hours above the limit), not a lab-validated model. "
                        "Prototype report generated from SIMULATED sensor data.", small)]
    doc.build(story)
    return buf.getvalue()

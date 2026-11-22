"""Storage-condition certificate (PDF with QR) for a farmer's lot."""
from __future__ import annotations
import io
import datetime as dt
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def fmt_ts(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%d %b %Y, %H:%M")


def build_certificate(lot: dict, chamber: dict, summary: dict, verify_url: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=14 * mm,
                            title=f"ColdTrust certificate - lot {lot['id']}")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=colors.HexColor("#0B5D4B"), fontSize=22)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5, textColor=colors.HexColor("#555555"))
    story = [Paragraph("ColdTrust", h1), Paragraph("Storage Condition Certificate", ss["Heading2"]), Spacer(1, 4 * mm)]

    ok = summary["minutes_outside"] == 0
    verdict = ("Stored within the safe temperature range for the whole period." if ok else
               f"Temperature was outside the safe range for {summary['minutes_outside']} minutes.")
    chain = summary["chain"]
    chain_txt = ("VERIFIED - the condition records are unaltered." if chain["ok"] else
                 f"NOT VERIFIED - records changed (first bad record #{chain['first_bad_id']}).")
    period_end = lot["withdrawn_ts"] or summary["now"]

    info = [["Lot number", f"#{lot['id']}"], ["Farmer", lot["farmer_name"]], ["Quantity", f"{lot['bags']} bags"],
            ["Chamber", f"{chamber['name']} ({chamber['crop']})"],
            ["Safe range", f"{chamber['temp_min']:.1f} to {chamber['temp_max']:.1f} C"],
            ["Period", f"{fmt_ts(lot['loaded_ts'])}  to  {fmt_ts(period_end)}" + ("" if lot["withdrawn_ts"] else "  (still stored)")]]
    stats = [["Average temperature", f"{summary['avg_temp']:.2f} C"], ["Minimum / Maximum", f"{summary['min_temp']:.2f} / {summary['max_temp']:.2f} C"],
             ["Average humidity", f"{summary['avg_hum']:.1f} %"], ["Readings recorded", f"{summary['readings']}"],
             ["Minutes outside safe range", f"{summary['minutes_outside']}"], ["Alerts (Warning or Critical)", f"{summary['alerts']}"],
             ["Spoilage risk (simple estimate)", f"{summary['spoilage']['level']} ({summary['spoilage']['score']:.0f}/100)"]]
    style = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F3EF")),
                        ("FONTSIZE", (0, 0), (-1, -1), 10), ("PADDING", (0, 0), (-1, -1), 5)])
    for data in (info, stats):
        t = Table(data, colWidths=[55 * mm, 115 * mm]); t.setStyle(style); story += [t, Spacer(1, 5 * mm)]

    story += [Paragraph(f"<b>Result:</b> {verdict}", ss["Normal"]), Spacer(1, 2 * mm),
              Paragraph(f"<b>Record integrity:</b> {chain_txt}", ss["Normal"]), Spacer(1, 2 * mm),
              Paragraph(f"Latest record fingerprint (SHA-256): {summary['head_hash'][:32]}...", small), Spacer(1, 4 * mm)]

    qr = qrcode.make(verify_url).convert("RGB")
    qb = io.BytesIO(); qr.save(qb, format="PNG"); qb.seek(0)
    story += [Image(qb, width=32 * mm, height=32 * mm), Paragraph(f"Scan to verify online: {verify_url}", small), Spacer(1, 4 * mm),
              Paragraph("Aapka maal poori avadhi mein surakshit seema ke andar rakha gaya hai. Is certificate ko bank ya kharidaar ko dikha sakte hain." if ok
                        else "Is avadhi mein kuch der temperature surakshit seema se bahar gaya tha. Poori jaankari upar di gayi hai.", ss["Normal"]),
              Spacer(1, 6 * mm),
              Paragraph("Prototype document generated from SIMULATED sensor data for demonstration. Not a legal or insurance certificate.", small)]
    doc.build(story)
    return buf.getvalue()

"""สร้างรายงาน: ข้อมูลรายงาน, กราฟเรดาร์ (SVG สำหรับ PDF / PNG สำหรับ Word), ไฟล์ Word และ Excel
ไม่ใช้ matplotlib เพื่อให้ขนาดแพ็กเกจเล็กพอสำหรับ Vercel"""
import io
import math
import os
from datetime import datetime

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import db as dbm
import storage
from db import get_db
from scoring import CHECKLIST, FOLLOWUP, FTYPES, ITEMS, compute, thai_date

BASE = os.path.dirname(os.path.abspath(__file__))
FONT = "TH Sarabun PSK"
FONT_SIZE = 16
SECTION_META = {s["code"]: s for e in CHECKLIST for s in e["sections"]}
R_COLORS = ("#4472c4", "#ed7d31")


# ------------------------------------------------------------------ data
def _resp(audit_id, rnd):
    rows = get_db().execute("SELECT * FROM responses WHERE audit_id=? AND round=?", (audit_id, rnd)).fetchall()
    return {r["item_id"]: r for r in rows}


def _order(item_id):
    e, r = item_id.split("-")
    return int(e), int(r)


def _atts(audit_id):
    out = {}
    for r in get_db().execute("SELECT * FROM attachments WHERE audit_id=? ORDER BY id", (audit_id,)).fetchall():
        out.setdefault((r["kind"], r["round"], r["item_id"]), []).append(r)
    return out


def build_report_data(a, rnd, with_photos=False):
    resp = _resp(a["id"], rnd)
    r1resp = _resp(a["id"], 1) if rnd == 2 else resp
    summ = compute(resp)
    s1 = compute(r1resp)
    atts = _atts(a["id"]) if with_photos else {}
    capa = {r["item_id"]: r for r in get_db().execute("SELECT * FROM capa WHERE audit_id=?", (a["id"],)).fetchall()}
    audit_date = a[f"r{rnd}_date"]
    report_date = a[f"r{rnd}_report_date"] or datetime.now().strftime("%Y-%m-%d")
    elements = []
    for e in CHECKLIST:
        es = summ["elements"][e["no"]]
        secs = []
        for s in e["sections"]:
            ss = es["sections"][s["code"]]
            secs.append({"code": s["code"], "title": s.get("report_title") or s["title"],
                         "short": s["title"], "max": ss["max"], "got": ss["got"], "pct": ss["pct"],
                         "in_report": s.get("in_report", True)})
        findings = []
        for n in e["nodes"]:
            if n["kind"] != "item":
                continue
            r = resp.get(n["id"])
            if r and r["ftype"] in FTYPES and (r["comment"] or "").strip():
                it = ITEMS[n["id"]]
                findings.append({"id": n["id"], "ref": it["ref"], "label": it["label"], "text": it["text"],
                                 "ftype": r["ftype"], "ftype_th": FTYPES[r["ftype"]]["th"],
                                 "comment": r["comment"].strip(), "score": r["score"], "max": n["max"],
                                 "photos": atts.get(("finding", rnd, n["id"]), [])})
        el_summary = get_db().execute("SELECT summary FROM element_status WHERE audit_id=? AND round=? AND element=?",
                                      (a["id"], rnd, e["no"])).fetchone()
        elements.append({"no": e["no"], "title": e["title"], "short": e["short"], "max": es["max"],
                         "got": es["got"], "pct": es["pct"], "sections": secs, "findings": findings,
                         "r1pct": s1["elements"][e["no"]]["pct"],
                         "summary": el_summary["summary"] if el_summary else None,
                         "counts": {k: sum(1 for f in findings if f["ftype"] == k) for k in FTYPES}})
    followups = []
    if rnd == 2:
        for iid in sorted(r1resp, key=_order):
            r = r1resp[iid]
            if r["ftype"] in ("NC", "OBS") and iid in ITEMS:
                r2 = resp.get(iid)
                it = ITEMS[iid]
                st = r2["followup"] if r2 else None
                c = capa.get(iid)
                followups.append({"id": iid, "element": it["element"], "ref": it["ref"], "label": it["label"],
                                  "ftype": r["ftype"], "comment": r["comment"] or "",
                                  "r1score": r["score"], "r2score": r2["score"] if r2 else None, "max": it["max"],
                                  "status": st, "status_th": FOLLOWUP[st]["th"] if st in FOLLOWUP else "รอติดตาม",
                                  "fnote": (r2["fnote"] if r2 else "") or "",
                                  "capa_action": (c["action"] if c else "") or "",
                                  "capa_files": atts.get(("capa", 1, iid), [])})
    team_rows = get_db().execute(
        """SELECT u.full_name, u.role FROM audit_team t JOIN users u ON u.id=t.user_id WHERE t.audit_id=?
           ORDER BY CASE WHEN u.id=? THEN 0 ELSE 1 END, u.full_name""", (a["id"], a["lead_user_id"])).fetchall()
    s = dbm.all_settings()
    deadline = thai_date(a["deadline"]) if a["deadline"] else "……………………"
    next_steps = (s.get(f"next_steps_r{rnd}") or "").replace("{year}", str(a["year_be"])).replace("{deadline}", deadline)
    total = summ["total"]
    vals = [[e["r1pct"] for e in elements]]
    if rnd == 2:
        vals.append([e["pct"] for e in elements])
    return {
        "rnd": rnd, "year": a["year_be"], "audit_date": thai_date(audit_date) if audit_date else "-",
        "r1_date": thai_date(a["r1_date"]) if a["r1_date"] else "-",
        "report_date": thai_date(report_date), "elements": elements, "total": total,
        "r1total": s1["total"], "followups": followups, "team": [dict(t) for t in team_rows],
        "settings": s, "next_steps": next_steps, "lead_name": a["lead_name"],
        "unanswered": total["count"] - total["answered"], "radar_svg": radar_svg(vals),
        "counts": {k: sum(e["counts"][k] for e in elements) for k in FTYPES},
    }


# ------------------------------------------------------------------ radar
def _radar_points(vals, cx, cy, r):
    pts = []
    for i, v in enumerate(vals):
        ang = -math.pi / 2 + i * 2 * math.pi / len(vals)
        rr = r * max(0, min(100, v)) / 100
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def radar_svg(values_list, size=320):
    """กราฟเรดาร์ร้อยละ 7 องค์ประกอบ (SVG ฝังในหน้ารายงาน)"""
    cx = cy = size / 2
    r = size / 2 - 40
    parts = [f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" class="radar" role="img" '
             f'aria-label="กราฟร้อยละคะแนนตามองค์ประกอบ">']
    for lv in (25, 50, 75, 100):
        pts = _radar_points([lv] * 7, cx, cy, r)
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polygon points="{poly}" fill="none" stroke="#d9dde6" stroke-width="1"/>')
        parts.append(f'<text x="{cx + 3:.1f}" y="{cy - r * lv / 100 + 10:.1f}" font-size="8.5" fill="#8a93a6">{lv}</text>')
    for i, (x, y) in enumerate(_radar_points([100] * 7, cx, cy, r)):
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e6e9f0"/>')
        lx, ly = _radar_points([126] * 7, cx, cy, r)[i]
        parts.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" font-size="12" font-weight="700" text-anchor="middle" '
                     f'fill="#334">{i + 1}</text>')
    for k, vals in enumerate(values_list):
        pts = _radar_points(vals, cx, cy, r)
        c = R_COLORS[k]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polygon points="{poly}" fill="{c}" fill-opacity="0.12" stroke="{c}" stroke-width="2"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{c}"/>')
    parts.append("</svg>")
    return "".join(parts)


def radar_png(values_list, size=900):
    """กราฟเรดาร์สำหรับไฟล์ Word (Pillow, ตัวเลขเท่านั้น — คำอธิบายเป็นข้อความใน Word)"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img, "RGBA")
    cx = cy = size / 2
    r = size / 2 - 90
    fp = os.path.join(BASE, "static", "fonts", "ttf", "IBMPlexSansThai-700.ttf")
    try:
        f_lbl = ImageFont.truetype(fp, 34)
        f_tick = ImageFont.truetype(fp, 22)
    except OSError:
        f_lbl = f_tick = ImageFont.load_default()
    for lv in (25, 50, 75, 100):
        d.polygon(_radar_points([lv] * 7, cx, cy, r), outline=(210, 214, 222), width=2)
        d.text((cx + 6, cy - r * lv / 100 + 4), str(lv), fill=(130, 138, 155), font=f_tick)
    for i, (x, y) in enumerate(_radar_points([100] * 7, cx, cy, r)):
        d.line([(cx, cy), (x, y)], fill=(225, 228, 235), width=2)
        lx, ly = _radar_points([116] * 7, cx, cy, r)[i]
        d.text((lx, ly), str(i + 1), fill=(40, 50, 70), font=f_lbl, anchor="mm")
    for k, vals in enumerate(values_list):
        c = R_COLORS[k]
        rgb = tuple(int(c[j:j + 2], 16) for j in (1, 3, 5))
        pts = _radar_points(vals, cx, cy, r)
        d.polygon(pts, fill=rgb + (30,))
        d.line(pts + [pts[0]], fill=rgb, width=6, joint="curve")
        for x, y in pts:
            d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=rgb)
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


# ------------------------------------------------------------------ docx helpers
def _set_cell_bg(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def _set_borders(table, sz=4, color="000000"):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def _no_borders(table):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tblPr.append(borders)


def _run_font(run, size=None, bold=None, color=None, underline=None):
    run.font.name = FONT
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    for k in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(k), FONT)
    sz = size or FONT_SIZE
    run.font.size = Pt(sz)
    szcs = OxmlElement("w:szCs")
    szcs.set(qn("w:val"), str(int(sz * 2)))
    rPr.append(szcs)
    if bold is not None:
        run.bold = bold
        if bold:
            rPr.append(OxmlElement("w:bCs"))
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if underline:
        run.underline = True


def _para(container, text="", bold=False, size=None, align=None, space_after=0, color=None, underline=False, indent=None):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(0)
    pf.line_spacing = 1.0
    if align:
        p.alignment = align
    if indent is not None:
        pf.left_indent = Cm(indent)
    if text:
        r = p.add_run(text)
        _run_font(r, size=size, bold=bold, color=color, underline=underline)
    return p


def _cell_text(cell, text, bold=False, align=None, size=None, color=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    if align:
        p.alignment = align
    lines = str(text).split("\n")
    for i, ln in enumerate(lines):
        r = p.add_run(ln)
        _run_font(r, size=size, bold=bold, color=color)
        if i < len(lines) - 1:
            r.add_break()
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _add_hyperlink(paragraph, url, text):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rFonts = OxmlElement("w:rFonts")
    for k in ("w:ascii", "w:hAnsi", "w:cs"):
        rFonts.set(qn(k), FONT)
    rPr.append(rFonts)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    c = OxmlElement("w:color")
    c.set(qn("w:val"), "0563C1")
    rPr.append(c)
    for tag in ("w:sz", "w:szCs"):
        s = OxmlElement(tag)
        s.set(qn("w:val"), str(FONT_SIZE * 2))
        rPr.append(s)
    new_run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    new_run.append(t)
    h.append(new_run)
    paragraph._p.append(h)


def _widths(table, widths):
    """กำหนดความกว้างคอลัมน์ให้ทั้ง Word และ LibreOffice (tblGrid + tcW + fixed layout)"""
    table.autofit = False
    tblPr = table._tbl.tblPr
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tblPr.append(lay)
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:w"), str(int(sum(widths) * 567)))
    tw.set(qn("w:type"), "dxa")
    for old in tblPr.findall(qn("w:tblW")):
        tblPr.remove(old)
    tblPr.append(tw)
    grid = table._tbl.tblGrid
    cols = grid.findall(qn("w:gridCol"))
    for gc, w in zip(cols, widths):
        gc.set(qn("w:w"), str(int(w * 567)))
    for row in table.rows:
        col = 0
        for tc in row._tr.findall(qn("w:tc")):
            tcPr = tc.get_or_add_tcPr()
            gs = tcPr.find(qn("w:gridSpan"))
            span = int(gs.get(qn("w:val"))) if gs is not None else 1
            w = sum(widths[col:col + span])
            for old in tcPr.findall(qn("w:tcW")):
                tcPr.remove(old)
            tcw = OxmlElement("w:tcW")
            tcw.set(qn("w:w"), str(int(w * 567)))
            tcw.set(qn("w:type"), "dxa")
            tcPr.insert(0, tcw)
            col += span


def _keep_rows(table, header_rows=1):
    """ไม่ตัดแถวข้ามหน้า และไม่ให้หัวตารางค้างท้ายหน้า"""
    for i, row in enumerate(table.rows):
        trPr = row._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit")
        cs.set(qn("w:val"), "true")
        trPr.append(cs)
        if i < header_rows:
            for c in row.cells:
                for p in c.paragraphs:
                    p.paragraph_format.keep_with_next = True


def _repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _score_table(doc, title_col, rnd_label, date_text, rows, widths=(10.4, 2.2, 2.2, 2.2)):
    """rows: list of (label, max, got, pct, bold)"""
    t = doc.add_table(rows=2, cols=4)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_borders(t)
    h0, h1 = t.rows
    a = h0.cells[0].merge(h1.cells[0])
    _cell_text(a, title_col, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    b = h0.cells[1].merge(h0.cells[3])
    _cell_text(b, f"{rnd_label}\n{date_text}", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    for i, txt in enumerate(("คะแนนเต็ม", "คะแนนที่ได้", "%"), start=1):
        _cell_text(h1.cells[i], txt, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
    for c in (a, b, h1.cells[1], h1.cells[2], h1.cells[3]):
        _set_cell_bg(c, "D0CECE")
    _repeat_header(h0)
    _repeat_header(h1)
    for label, mx, got, p, bold in rows:
        r = t.add_row()
        is_total = label == "รวม"
        _cell_text(r.cells[0], label, bold=bold, size=14, align=WD_ALIGN_PARAGRAPH.CENTER if is_total else None)
        _cell_text(r.cells[1], str(mx), bold=bold, size=14, align=WD_ALIGN_PARAGRAPH.RIGHT)
        _cell_text(r.cells[2], str(got), bold=bold, size=14, align=WD_ALIGN_PARAGRAPH.RIGHT)
        _cell_text(r.cells[3], f"{p:.2f}", bold=bold, size=14, align=WD_ALIGN_PARAGRAPH.RIGHT)
        if is_total:
            for c in r.cells:
                _set_cell_bg(c, "D0CECE")
        elif bold:
            for c in r.cells:
                _set_cell_bg(c, "F3F3F3")
    _widths(t, widths)
    _keep_rows(t, header_rows=2)
    return t


def build_docx(a, rnd):
    d = build_report_data(a, rnd)
    s = d["settings"]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(1.8)
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(FONT_SIZE)
    st.element.rPr.rFonts.set(qn("w:cs"), FONT)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    # footer page number
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = fp.add_run(f"{a['lab_name']} · รายงานครั้งที่ {rnd} · หน้า ")
    _run_font(r, size=12, color="666666")
    for code in ("begin", "PAGE", "end"):
        run = fp.add_run()
        _run_font(run, size=12, color="666666")
        if code == "PAGE":
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = "PAGE"
            run._r.append(it)
        else:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), code)
            run._r.append(fc)

    # title box
    tb = doc.add_table(rows=1, cols=1)
    tb.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_borders(tb)
    _widths(tb, (17.0,))
    c = tb.rows[0].cells[0]
    _set_cell_bg(c, "FBE4D5")
    c.text = ""
    lines = ["สรุปผลตรวจประเมินความปลอดภัยห้องปฏิบัติการตามมาตรฐาน ESPReL Checklist",
             "และรายงานข้อบกพร่อง/ข้อสังเกตและข้อเสนอแนะ",
             f"ครั้งที่ {rnd} ประจำปี {d['year']}",
             s.get("activity_name") or s.get("org_name")]
    for i, ln in enumerate(lines):
        p = c.paragraphs[0] if i == 0 else c.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        _run_font(p.add_run(ln), bold=True, size=16)
    _para(doc)

    info = [f"1) ชื่อห้องปฏิบัติการ: {a['lab_name']}",
            f"2) เลขทะเบียนห้องปฏิบัติการ (ESPReL no.) {a['espre_no'] or '-'}",
            f"3) สังกัด: {a['affiliation'] or '-'}",
            f"4) วันที่ตรวจประเมิน : {d['audit_date']}",
            "5) คะแนนของห้องปฏิบัติการตามองค์ประกอบ"]
    for ln in info:
        _para(doc, ln, bold=True, indent=0.3)
    _para(doc, "ตารางแสดงคะแนนของห้องปฏิบัติการตามองค์ประกอบ", bold=True, indent=0.3, space_after=4)

    rows = [(f"{e['no']}. {e['title']}", e["max"], e["got"], e["pct"], False) for e in d["elements"]]
    rows.append(("รวม", d["total"]["max"], d["total"]["got"], d["total"]["pct"], True))
    _score_table(doc, "คะแนนตามองค์ประกอบ", f"ครั้งที่ {rnd}", d["audit_date"], rows)
    _para(doc)

    vals = [[e["r1pct"] for e in d["elements"]]]
    if rnd == 2:
        vals.append([e["pct"] for e in d["elements"]])
    _para(doc, "กราฟแสดง%คะแนนตามองค์ประกอบ", bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
    img = radar_png(vals)
    p = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    p.add_run().add_picture(img, width=Cm(9.5))
    _para(doc, "(เส้นสีน้ำเงิน = ครั้งที่ 1 · เส้นสีส้ม = ครั้งที่ 2 · ตัวเลข 1–7 = องค์ประกอบ)" if rnd == 2
          else "(ตัวเลข 1–7 = องค์ประกอบ)", size=12, align=WD_ALIGN_PARAGRAPH.CENTER, color="555555")

    _para(doc, "คะแนนของห้องปฏิบัติการตามหัวข้อ", bold=True)
    _para(doc, "ตารางแสดงคะแนนของห้องปฏิบัติการตามหัวข้อ", bold=True, space_after=4)
    rows = []
    for e in d["elements"]:
        rows.append((f"{e['no']}. {e['title']}", e["max"], e["got"], e["pct"], True))
        for sct in e["sections"]:
            if sct["in_report"]:
                rows.append((f"{sct['code']} {sct['title']}", sct["max"], sct["got"], sct["pct"], False))
    rows.append(("รวม", d["total"]["max"], d["total"]["got"], d["total"]["pct"], True))
    _score_table(doc, "คะแนนตามหัวข้อ", f"ครั้งที่ {rnd}", d["audit_date"], rows)
    _para(doc)

    # follow-up (round 2)
    if rnd == 2 and d["followups"]:
        _para(doc, "ผลการติดตามการแก้ไขข้อบกพร่อง/ข้อสังเกต จากการตรวจประเมินครั้งที่ 1", bold=True, space_after=4)
        t = doc.add_table(rows=1, cols=5)
        _set_borders(t)
        for i, h in enumerate(("ข้อ", "ประเภท", "ข้อค้นพบครั้งที่ 1", "CAPA ของห้องปฏิบัติการ / ผลตรวจครั้งที่ 2", "สถานะ")):
            _cell_text(t.rows[0].cells[i], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
            _set_cell_bg(t.rows[0].cells[i], "D0CECE")
        _repeat_header(t.rows[0])
        for f in d["followups"]:
            r = t.add_row().cells
            _cell_text(r[0], f["ref"], size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(r[1], FTYPES[f["ftype"]]["short"], size=14, align=WD_ALIGN_PARAGRAPH.CENTER,
                       color=FTYPES[f["ftype"]]["color"].lstrip("#"), bold=True)
            _cell_text(r[2], f["comment"], size=14)
            parts = []
            if f["capa_action"]:
                parts.append("CAPA: " + f["capa_action"])
            if f["fnote"]:
                parts.append("ผลตรวจ: " + f["fnote"])
            _cell_text(r[3], "\n".join(parts) or "-", size=14)
            col = FOLLOWUP.get(f["status"], {}).get("color", "#666666").lstrip("#")
            _cell_text(r[4], f["status_th"], size=14, align=WD_ALIGN_PARAGRAPH.CENTER, color=col, bold=True)
        _widths(t, (2.0, 1.3, 5.6, 5.9, 2.2))
        _keep_rows(t)
        _para(doc)

    _para(doc, "ข้อเสนอแนะของการตรวจประเมิน", bold=True, space_after=2)
    for e in d["elements"]:
        _para(doc, f"องค์ประกอบที่ {e['no']} {e['title']}", bold=True).paragraph_format.keep_with_next = True
        if not e["findings"]:
            _para(doc, "ไม่มีข้อเสนอแนะ", bold=True, underline=True, space_after=8)
            continue
        _para(doc, "มีข้อเสนอแนะและให้ปรับปรุงแก้ไขดังนี้", bold=True, underline=True, space_after=2).paragraph_format.keep_with_next = True
        t = doc.add_table(rows=1, cols=3)
        _set_borders(t, color="BFBFBF")
        for i, h in enumerate(("ข้อ", "ประเภท", "รายละเอียด")):
            _cell_text(t.rows[0].cells[i], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
            _set_cell_bg(t.rows[0].cells[i], "F3F3F3")
        for f in e["findings"]:
            r = t.add_row().cells
            _cell_text(r[0], f["ref"], size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(r[1], FTYPES[f["ftype"]]["th"].replace(" (", "\n("), size=13,
                       align=WD_ALIGN_PARAGRAPH.CENTER, color=FTYPES[f["ftype"]]["color"].lstrip("#"), bold=True)
            txt = f["comment"]
            _cell_text(r[2], txt if txt.startswith("-") else f"- {txt}", size=14)
        _widths(t, (2.0, 2.6, 12.4))
        _keep_rows(t)
        _para(doc, space_after=6)

    # signature
    _para(doc)
    _para(doc, s.get("team_title"), bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT)
    _para(doc, d["report_date"], bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT, space_after=6)
    if d["team"]:
        _para(doc, "รายชื่อคณะผู้ตรวจประเมิน", bold=True)
        for i, m in enumerate(d["team"], 1):
            role = " (หัวหน้าผู้ตรวจประเมิน)" if m["full_name"] == d["lead_name"] else ""
            _para(doc, f"{i}. {m['full_name']}{role}", indent=0.5)

    if d["next_steps"]:
        _para(doc)
        _para(doc, "ข้อแนะนำต่อไป", bold=True)
        p = _para(doc, indent=0)
        p.paragraph_format.first_line_indent = Cm(1.5)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        txt = d["next_steps"]
        url = s.get("upgrade_form_url")
        key = "แบบฟอร์มรายงานการยกระดับ"
        if url and key in txt:
            before, after = txt.split(key, 1)
            _run_font(p.add_run(before))
            _add_hyperlink(p, url, key)
            _run_font(p.add_run(after))
        else:
            _run_font(p.add_run(txt))
        if rnd == 1:
            qr = s.get("qr_image")
            qimg = os.path.join(BASE, "static", "img", "qr_default.png")
            if qr:
                try:
                    qimg = io.BytesIO(storage.load(qr))
                except Exception:  # noqa
                    pass
            p = _para(doc, align=WD_ALIGN_PARAGRAPH.LEFT)
            p.add_run().add_picture(qimg, width=Cm(2.6))

    import unicodedata
    safe = "".join(ch for ch in (a["lab_name"] or "lab")
                   if unicodedata.category(ch)[0] in "LNM" or ch in " -_").strip().replace(" ", "_")[:60]
    fname = f"Report{rnd}_{safe}_{d['year']}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.docx"
    bio = io.BytesIO()
    doc.save(bio)
    return fname, bio.getvalue()


# ------------------------------------------------------------------ excel export
def build_xlsx(a):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    r1, r2 = _resp(a["id"], 1), _resp(a["id"], 2)
    s1, s2 = compute(r1), compute(r2)
    wb = Workbook()
    ws = wb.active
    ws.title = "สรุปคะแนน"
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="D0CECE")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append([f"1) ชื่อห้องปฏิบัติการ: {a['lab_name']}"])
    ws.append([f"2) เลขทะเบียนห้องปฏิบัติการ (ESPReL no.) {a['espre_no'] or ''}"])
    ws.append([f"3) สังกัด: {a['affiliation'] or ''}"])
    ws.append([f"4) วันที่ตรวจประเมิน : ครั้งที่ 1 {thai_date(a['r1_date'])} / ครั้งที่ 2 {thai_date(a['r2_date']) if a['r2_date'] else '-'}"])
    ws.append([])
    ws.append(["คะแนนตามหัวข้อ", "ครั้งที่ 1 คะแนนเต็ม", "คะแนนที่ได้", "%", "ครั้งที่ 2 คะแนนเต็ม", "คะแนนที่ได้", "%"])
    for c in ws[ws.max_row]:
        c.font, c.fill, c.border = bold, fill, border
    for e in CHECKLIST:
        a1, a2 = s1["elements"][e["no"]], s2["elements"][e["no"]]
        ws.append([f"{e['no']}. {e['title']}", a1["max"], a1["got"], a1["pct"], a2["max"], a2["got"], a2["pct"]])
        for c in ws[ws.max_row]:
            c.font, c.border = bold, border
        for sct in e["sections"]:
            x1, x2 = a1["sections"][sct["code"]], a2["sections"][sct["code"]]
            ws.append([f"{sct['code']} {sct.get('report_title') or sct['title']}", x1["max"], x1["got"], x1["pct"],
                       x2["max"], x2["got"], x2["pct"]])
            for c in ws[ws.max_row]:
                c.border = border
    t1, t2 = s1["total"], s2["total"]
    ws.append(["รวม", t1["max"], t1["got"], t1["pct"], t2["max"], t2["got"], t2["pct"]])
    for c in ws[ws.max_row]:
        c.font, c.fill, c.border = bold, fill, border
    ws.column_dimensions["A"].width = 80
    for col in "BCDEFG":
        ws.column_dimensions[col].width = 14

    for rnd_label in ("ผลการตรวจรายข้อ",):
        ws2 = wb.create_sheet(rnd_label)
        head = ["องค์ประกอบ", "หัวข้อ", "ข้อ", "รายการ", "คะแนนเต็ม", "ครั้งที่ 1 คะแนน", "ครั้งที่ 1 ประเภท",
                "ครั้งที่ 1 ข้อค้นพบ", "ครั้งที่ 2 คะแนน", "ครั้งที่ 2 ประเภท", "ครั้งที่ 2 ข้อค้นพบ",
                "ติดตามผลการแก้ไข", "ผลการแก้ไข"]
        ws2.append(head)
        for c in ws2[1]:
            c.font, c.fill, c.border = bold, fill, border
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for iid in sorted(ITEMS, key=_order):
            it = ITEMS[iid]
            x1, x2 = r1.get(iid), r2.get(iid)
            ws2.append([it["element"], it["section"], it["ref"], it["text"], it["max"],
                        x1["score"] if x1 else None, FTYPES.get(x1["ftype"], {}).get("short") if x1 and x1["ftype"] else None,
                        x1["comment"] if x1 else None,
                        x2["score"] if x2 else None, FTYPES.get(x2["ftype"], {}).get("short") if x2 and x2["ftype"] else None,
                        x2["comment"] if x2 else None,
                        FOLLOWUP.get(x2["followup"], {}).get("th") if x2 and x2["followup"] else None,
                        x2["fnote"] if x2 else None])
        widths = [10, 8, 10, 60, 10, 10, 10, 40, 10, 10, 40, 14, 40]
        for i, w in enumerate(widths, 1):
            ws2.column_dimensions[get_column_letter(i)].width = w
        ws2.freeze_panes = "E2"
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio

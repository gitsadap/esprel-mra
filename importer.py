"""นำเข้าไฟล์ Excel แบบประเมิน ESPReL (รูปแบบเดียวกับไฟล์ต้นแบบ) เข้าสู่ระบบ"""
import re

from db import get_db, now
from excel_parser import parse_lab_workbook
from scoring import CHECKLIST, ITEMS, THAI_MONTHS


def thai_text_to_iso(s):
    """'29 เมษายน 2569' -> '2026-04-29'"""
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s*([ก-๙\.]+)\s*(\d{4})", s)
    if not m:
        return None
    d, mon, y = int(m.group(1)), m.group(2).strip(), int(m.group(3))
    try:
        mi = THAI_MONTHS.index(mon)
    except ValueError:
        return None
    if y > 2400:
        y -= 543
    return f"{y:04d}-{mi:02d}-{d:02d}"


def guess_ftype(score, mx, text):
    if not text:
        return None
    if score not in (None, "NA") and int(score) < mx:
        return "NC"
    if re.search(r"เสนอแนะ|แนะนำ|ควร", text):
        return "SUG"
    return "OBS"


def import_workbook(path, rounds="1", user_id=None):
    info, resp = parse_lab_workbook(path, CHECKLIST)
    if not resp[1]:
        raise ValueError("ไม่พบชีท 'คะแนนองค์ 1-7' ในไฟล์")
    db = get_db()
    r1_date = thai_text_to_iso(info.get("r1_date_text"))
    r2_date = thai_text_to_iso(info.get("r2_date_text"))
    year_be = int(r1_date[:4]) + 543 if r1_date else None
    if not year_be:
        from datetime import datetime
        year_be = datetime.now().year + 543
    lab = None
    if info.get("espre_no"):
        lab = db.execute("SELECT id FROM labs WHERE espre_no=?", (info["espre_no"],)).fetchone()
    if lab:
        lab_id = lab["id"]
    else:
        aff = info.get("affiliation") or ""
        faculty = aff.split(" มหาวิทยาลัย")[0].strip() or None
        lab_id = db.insert("INSERT INTO labs(name,espre_no,faculty,affiliation,status,created_at) VALUES(?,?,?,?,'approved',?)",
                           (info.get("name") or "ห้องปฏิบัติการ (นำเข้า)", info.get("espre_no"), faculty, aff, now()))
    two = rounds == "2"
    aid = db.insert("""INSERT INTO audits(lab_id,year_be,r1_date,r2_date,status,lead_user_id,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (lab_id, year_be, r1_date, r2_date if two else None, "r2" if two else "r1", user_id, now()))
    if user_id:
        db.execute("INSERT INTO audit_team(audit_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (aid, user_id))
    for item_id, r in resp[1].items():
        mx = ITEMS[item_id]["max"]
        ft = guess_ftype(r["score"], mx, r["comment"])
        db.execute("""INSERT INTO responses(audit_id,round,item_id,score,ftype,comment,updated_by,updated_at)
                      VALUES(?,?,?,?,?,?,?,?)""", (aid, 1, item_id, r["score"], ft, r["comment"] or None, user_id, now()))
    if two:
        for item_id, r in resp[2].items():
            mx = ITEMS[item_id]["max"]
            r1 = resp[1].get(item_id) or {}
            had = bool(r1.get("comment")) and guess_ftype(r1.get("score"), mx, r1.get("comment")) in ("NC", "OBS")
            followup = fnote = ftype = comment = None
            if had:
                ok = r["score"] == "NA" or (r["score"] is not None and int(r["score"]) >= mx)
                followup = "resolved" if ok else "open"
                fnote = r["comment"] or None
                if not ok and r["comment"]:
                    ftype, comment = "NC", r["comment"]
            elif r["comment"]:
                ftype = guess_ftype(r["score"], mx, r["comment"])
                comment = r["comment"]
            db.execute("""INSERT INTO responses(audit_id,round,item_id,score,ftype,comment,followup,fnote,updated_by,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (aid, 2, item_id, r["score"], ftype, comment, followup, fnote, user_id, now()))
            if had and fnote:
                db.execute("""INSERT INTO capa(audit_id,item_id,action,updated_by,updated_at) VALUES(?,?,?,?,?)
                              ON CONFLICT(audit_id,item_id) DO NOTHING""", (aid, item_id, fnote, user_id, now()))
    for rnd in ((1, 2) if two else (1,)):
        for e in range(1, 8):
            db.execute("""INSERT INTO element_status(audit_id,round,element,done,done_by,done_at) VALUES(?,?,?,?,?,?)
                          ON CONFLICT(audit_id,round,element) DO UPDATE SET done=1""", (aid, rnd, e, 1, user_id, now()))
    db.commit()
    return aid

"""โครงสร้างแบบประเมิน + การคำนวณคะแนน (ใช้หลักเดียวกับไฟล์ Excel: NA = ไม่นำมาคิดคะแนนเต็ม)"""
import json
import os
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "data", "checklist.json"), encoding="utf-8") as f:
    CHECKLIST = json.load(f)

ELEMENTS = {e["no"]: e for e in CHECKLIST}
ITEMS = {}
SECTION_TITLES = {}
for _e in CHECKLIST:
    _parent_text = {}
    for _n in _e["nodes"]:
        if _n["kind"] == "section":
            SECTION_TITLES[_n["code"]] = _n["text"]
        if _n["kind"] in ("section", "heading") and _n.get("code"):
            _parent_text[_n["code"]] = _n["text"]
        if _n["kind"] == "item":
            it = dict(_n)
            it["element"] = _e["no"]
            if it.get("code"):
                it["ref"] = it["code"]
                it["label"] = f'{it["code"]} {it["text"]}'
            else:
                it["ref"] = it.get("parent") or str(_e["no"])
                it["label"] = f'{it["ref"]} – {it["text"]}'
            it["parent_text"] = _parent_text.get(it.get("parent"), "")
            ITEMS[it["id"]] = it
            if it.get("code"):
                _parent_text[it["code"]] = it["text"]

FTYPES = {
    "NC": {"th": "ข้อบกพร่อง (NC)", "short": "NC", "color": "#dc2626"},
    "OBS": {"th": "ข้อสังเกต (OBS)", "short": "OBS", "color": "#d97706"},
    "SUG": {"th": "ข้อเสนอแนะ", "short": "แนะนำ", "color": "#2563eb"},
}
FOLLOWUP = {
    "resolved": {"th": "แก้ไขแล้ว", "color": "#16a34a"},
    "partial": {"th": "แก้ไขบางส่วน", "color": "#d97706"},
    "open": {"th": "ยังไม่แก้ไข", "color": "#dc2626"},
}

THAI_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
THAI_MONTHS_SHORT = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.",
                     "ต.ค.", "พ.ย.", "ธ.ค."]


def parse_date(s):
    if not s:
        return None
    if isinstance(s, (date, datetime)):
        return s
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    return None


def thai_date(s, short=False):
    d = parse_date(s)
    if not d:
        return s or "-"
    m = THAI_MONTHS_SHORT[d.month] if short else THAI_MONTHS[d.month]
    return f"{d.day} {m} {d.year + 543}"


def pct(got, mx):
    return round(got * 100.0 / mx, 2) if mx else 0.0


def compute(resp):
    """resp: dict item_id -> row/dict (score, ftype, ...).
    คืนค่าสรุปรายองค์ประกอบ/หัวข้อ, ความคืบหน้า และจำนวนข้อค้นพบ"""
    out = {"elements": {}, "total": {"max": 0, "got": 0, "answered": 0, "count": 0,
                                     "NC": 0, "OBS": 0, "SUG": 0}}
    for e in CHECKLIST:
        el = {"no": e["no"], "max": 0, "got": 0, "answered": 0, "count": 0, "na": 0,
              "NC": 0, "OBS": 0, "SUG": 0, "lost": [], "sections": {}}
        for s in e["sections"]:
            el["sections"][s["code"]] = {"code": s["code"], "title": s["title"], "max": 0, "got": 0}
        for n in e["nodes"]:
            if n["kind"] != "item":
                continue
            r = resp.get(n["id"]) or {}
            score = r["score"] if r and r["score"] not in (None, "") else None
            ftype = r["ftype"] if r else None
            el["count"] += 1
            if score is not None:
                el["answered"] += 1
            if ftype in ("NC", "OBS", "SUG"):
                el[ftype] += 1
            sec = el["sections"].get(n["section"])
            if score == "NA":
                el["na"] += 1
                continue
            el["max"] += n["max"]
            got = int(score) if score not in (None, "NA") else 0
            el["got"] += got
            if sec is not None:
                sec["max"] += n["max"]
                sec["got"] += got
            if score is not None and got < n["max"]:
                el["lost"].append({"id": n["id"], "got": got, "max": n["max"]})
        el["pct"] = pct(el["got"], el["max"])
        el["progress"] = round(el["answered"] * 100.0 / el["count"]) if el["count"] else 0
        for s in el["sections"].values():
            s["pct"] = pct(s["got"], s["max"])
        out["elements"][e["no"]] = el
        t = out["total"]
        for k in ("max", "got", "answered", "count", "NC", "OBS", "SUG"):
            t[k] += el[k]
    t = out["total"]
    t["pct"] = pct(t["got"], t["max"])
    t["progress"] = round(t["answered"] * 100.0 / t["count"]) if t["count"] else 0
    return out


def level_of(p):
    """ระดับสีตามร้อยละ (ใช้ในหน้าจอ)"""
    if p >= 90:
        return "good"
    if p >= 75:
        return "ok"
    if p >= 50:
        return "warn"
    return "bad"

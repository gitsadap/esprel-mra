"""ค่าคงที่และฟังก์ชันกลางที่ทุกหน้าใช้ร่วมกัน: สิทธิ์ผู้ใช้, ข้อมูลการตรวจ, ไฟล์อัปโหลด, บัญชี/อีเมล"""
import os
import re
import secrets
import unicodedata
from functools import wraps
from urllib.parse import quote

from flask import abort, current_app, flash, g, jsonify, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import config
import mailer
import storage
from db import get_db, now
from scoring import ELEMENTS, ITEMS, compute

BASE = config.BASE
INSTANCE = config.INSTANCE
STAFF = ("admin", "lead", "assessor")
ROLE_TH = {"admin": "ผู้ดูแลระบบ", "lead": "หัวหน้าผู้ตรวจประเมิน", "assessor": "ผู้ตรวจประเมิน",
           "lab": "ห้องปฏิบัติการ"}
STATUS_TH = {
    "r1": ("กำลังตรวจรอบที่ 1", "blue"),
    "r1_reported": ("ออก Report 1 แล้ว · รอห้องปฏิบัติการตอบ CAPA", "amber"),
    "capa_submitted": ("ส่ง CAPA แล้ว · พร้อมตรวจรอบ 2", "teal"),
    "r2": ("กำลังตรวจรอบที่ 2", "violet"),
    "r2_reported": ("เสร็จสิ้น · ออก Report 2 แล้ว", "green"),
}
STATUS_ORDER = ["r1", "r1_reported", "capa_submitted", "r2", "r2_reported"]
APP_STATUS_TH = {"draft": ("ร่าง · ยังไม่ส่ง", "gray"), "pending": ("ส่งแล้ว · รออนุมัติ", "amber"), "approved": ("อนุมัติแล้ว · รอจัดทีม", "blue"),
                 "assigned": ("จัดทีมแล้ว", "green"), "rejected": ("ไม่อนุมัติ · ส่งกลับแก้ไข", "red")}
IMG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
DOC_TYPES = dict(IMG_TYPES, **{"application/pdf": ".pdf"})


# ----------------------------------------------------------------- helpers
def check_pw(stored, password):
    try:
        return bool(stored) and check_password_hash(stored, password)
    except (AttributeError, ValueError):
        return False


def is_api():
    return request.path.startswith("/api/")


def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        if not g.user:
            if is_api():
                return jsonify(ok=False, error="กรุณาเข้าสู่ระบบใหม่"), 401
            return redirect(url_for("login", next=request.path))
        return fn(*a, **k)
    return w


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if not g.user:
                if is_api():
                    return jsonify(ok=False, error="กรุณาเข้าสู่ระบบใหม่"), 401
                return redirect(url_for("login", next=request.path))
            if g.user["role"] not in allowed:
                if is_api():
                    return jsonify(ok=False, error="ไม่มีสิทธิ์ดำเนินการ"), 403
                flash("ไม่มีสิทธิ์เข้าถึงหน้านี้", "error")
                return redirect(url_for("home"))
            return fn(*a, **k)
        return w
    return deco


def is_lead():
    return bool(g.user) and g.user["role"] in ("lead", "admin")


def is_staff():
    return bool(g.user) and g.user["role"] in STAFF


def load_audit(audit_id):
    a = get_db().execute(
        """SELECT a.*, l.name lab_name, l.espre_no, l.affiliation, l.faculty, l.location, l.head_name, l.contact,
                  l.owner_user_id, u.full_name lead_name
           FROM audits a JOIN labs l ON l.id=a.lab_id LEFT JOIN users u ON u.id=a.lead_user_id
           WHERE a.id=?""", (audit_id,)).fetchone()
    if not a:
        abort(404)
    return a


def is_lab_member(lab_id, user=None):
    user = user or g.user
    if not user:
        return False
    return bool(get_db().execute(
        "SELECT 1 x FROM labs WHERE id=? AND owner_user_id=? UNION SELECT 1 x FROM lab_members WHERE lab_id=? AND user_id=?",
        (lab_id, user["id"], lab_id, user["id"])).fetchone())


def can_view_audit(a):
    return is_staff() or is_lab_member(a["lab_id"])


def lab_may_see_results(a):
    """ห้องปฏิบัติการเห็นผลการตรวจทุกข้อหลังออก Report 1"""
    return a["status"] != "r1"


def get_responses(audit_id, rnd):
    rows = get_db().execute(
        """SELECT r.*, u.full_name updated_name FROM responses r LEFT JOIN users u ON u.id=r.updated_by
           WHERE audit_id=? AND round=?""", (audit_id, rnd)).fetchall()
    return {r["item_id"]: r for r in rows}


def get_capa(audit_id):
    return {r["item_id"]: r for r in get_db().execute("SELECT * FROM capa WHERE audit_id=?", (audit_id,)).fetchall()}


def get_attachments(audit_id):
    """{(kind, round, item_id): [att, ...]}"""
    out = {}
    for r in get_db().execute("SELECT * FROM attachments WHERE audit_id=? ORDER BY id", (audit_id,)).fetchall():
        out.setdefault((r["kind"], r["round"], r["item_id"]), []).append(r)
    return out


def follow_items(audit_id):
    """ข้อที่ต้องตรวจติดตามในรอบ 2 = ข้อที่เป็น NC/OBS ในรอบ 1"""
    return {iid for iid, r in get_responses(audit_id, 1).items() if r["ftype"] in ("NC", "OBS")}


def element_status(audit_id, rnd):
    rows = get_db().execute(
        """SELECT s.*, u.full_name done_name FROM element_status s LEFT JOIN users u ON u.id=s.done_by
           WHERE audit_id=? AND round=?""", (audit_id, rnd)).fetchall()
    return {r["element"]: r for r in rows}


def assignments(audit_id):
    rows = get_db().execute(
        """SELECT a.element, a.user_id, u.full_name FROM assignments a LEFT JOIN users u ON u.id=a.user_id
           WHERE audit_id=?""", (audit_id,)).fetchall()
    return {r["element"]: r for r in rows}


def team(audit_id):
    return get_db().execute(
        """SELECT u.* FROM audit_team t JOIN users u ON u.id=t.user_id WHERE t.audit_id=?
           ORDER BY CASE u.role WHEN 'lead' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.full_name""",
        (audit_id,)).fetchall()


def round_editable(audit, rnd):
    return (rnd == 1 and audit["status"] == "r1") or (rnd == 2 and audit["status"] == "r2")


def current_round(audit):
    return 2 if audit["status"] in ("r2", "r2_reported") else 1


def r2_progress(audit_id, follow=None):
    """ความคืบหน้ารอบ 2: จำนวนข้อ NC/OBS ที่ระบุผลติดตามแล้ว ต่อองค์ประกอบ"""
    follow = follow if follow is not None else follow_items(audit_id)
    r2 = get_responses(audit_id, 2)
    out = {e: [0, 0] for e in ELEMENTS}
    for iid in follow:
        el = ITEMS[iid]["element"]
        out[el][1] += 1
        r = r2.get(iid)
        if r and r["followup"] and not r["carried"]:
            out[el][0] += 1
    return out


def ensure_round2(audit_id):
    """เริ่มรอบ 2: คัดลอกคะแนนรอบ 1 ทุกข้อ — ข้อ NC/OBS ต้องตรวจซ้ำ (carried=1), ข้ออื่นล็อกคะแนนเดิม"""
    db = get_db()
    r1 = get_responses(audit_id, 1)
    follow = {iid for iid, r in r1.items() if r["ftype"] in ("NC", "OBS")}
    have = set(get_responses(audit_id, 2))
    for item_id in ITEMS:
        if item_id in have:
            continue
        r = r1.get(item_id)
        score = r["score"] if r else None
        db.execute("INSERT INTO responses(audit_id,round,item_id,score,carried,updated_at) VALUES(?,?,?,?,?,?)",
                   (audit_id, 2, item_id, score, 1 if item_id in follow else 0, now()))
    for e in ELEMENTS:
        if not any(ITEMS[i]["element"] == e for i in follow):
            db.execute("""INSERT INTO element_status(audit_id,round,element,done,done_at,summary) VALUES(?,?,?,1,?,?)
                          ON CONFLICT(audit_id,round,element) DO UPDATE SET done=1""",
                       (audit_id, 2, e, now(), "ไม่มีข้อบกพร่อง/ข้อสังเกตที่ต้องตรวจติดตาม"))


def summary_for(audit_id):
    return compute(get_responses(audit_id, 1)), compute(get_responses(audit_id, 2))


def file_url(att):
    return url_for("file_view", att_id=att["id"])


def _display_name(fn):
    """ชื่อไฟล์สำหรับแสดงผล (เก็บอักษรไทยไว้ ตัดอักขระพิเศษ/พาธ)"""
    base = os.path.basename((fn or "").replace("\\", "/"))
    keep = "".join(ch for ch in unicodedata.normalize("NFC", base)
                   if unicodedata.category(ch)[0] in "LNM" or ch in " ._-()")
    return keep.strip(" .")[:120]


# ----------------------------------------------------------------- accounts / email helpers
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def gen_password(n=10):
    """รหัสผ่านชั่วคราว (ไม่มีตัวที่สับสน เช่น 0/O, 1/l)"""
    while True:
        pw = "".join(secrets.choice(PW_ALPHABET) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw):
            return pw


def base_url():
    return (config.APP_BASE_URL or request.url_root).rstrip("/")


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="esprel-pw-reset")


def make_reset_token(u):
    # ผูกกับ hash รหัสผ่านปัจจุบัน → ใช้ได้ครั้งเดียว (เปลี่ยนรหัสแล้วลิงก์เดิมใช้ไม่ได้)
    return _reset_serializer().dumps({"u": u["id"], "h": (u["password_hash"] or "")[-16:]})


def read_reset_token(token, max_age=60 * 60 * 24):
    try:
        data = _reset_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    u = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (data.get("u"),)).fetchone()
    if not u or (u["password_hash"] or "")[-16:] != data.get("h"):
        return None
    return u


def mail_credentials(u, password, lab_name=None, app_no=None):
    """อีเมลแจ้งชื่อผู้ใช้/รหัสผ่านชั่วคราวให้ห้องปฏิบัติการ"""
    lines = [f"เรียน {u['full_name']}", ""]
    if lab_name:
        lines += [f"ระบบได้รับใบสมัครรับการตรวจประเมินความปลอดภัยห้องปฏิบัติการ (FM-001) ของ “{lab_name}” แล้ว"
                  + (f" เลขที่ใบสมัคร {app_no}" if app_no else ""), ""]
    lines += ["ข้อมูลสำหรับเข้าสู่ระบบ ESPReL MRA มหาวิทยาลัยนเรศวร",
              f"  ชื่อผู้ใช้ : {u['username']}",
              f"  รหัสผ่านชั่วคราว : {password}",
              f"  เข้าสู่ระบบ : {base_url()}/login",
              "", "เมื่อเข้าสู่ระบบครั้งแรก ระบบจะให้ตั้งรหัสผ่านใหม่", ""]
    if u["role"] == "lab":
        lines += ["ขั้นตอนต่อไป",
                  "  1) เข้าสู่ระบบ → พิมพ์ใบสมัคร FM-001 ให้ผู้มีอำนาจลงนาม แล้วแนบไฟล์ที่ลงนามกลับเข้าระบบ",
                  "  2) ติดตามสถานะใบสมัคร กำหนดการตรวจ ผลการตรวจประเมิน และตอบแผนการแก้ไข (CAPA) ได้ในระบบ",
                  "", "หากท่านไม่ได้เป็นผู้ส่งใบสมัครนี้ โปรดแจ้งกองการวิจัยและนวัตกรรม มหาวิทยาลัยนเรศวร"]
    else:
        lines += ["ท่านได้รับสิทธิ์เป็นผู้ตรวจประเมินในระบบ ESPReL MRA — เมื่อได้รับมอบหมายให้ตรวจห้องปฏิบัติการ",
                  "จะเห็นรายการที่ “ภาพรวม” และให้คะแนน/บันทึกข้อค้นพบได้ทั้งบนมือถือ iPad และคอมพิวเตอร์"]
    lines += ["", "ระบบ ESPReL MRA มหาวิทยาลัยนเรศวร (อีเมลอัตโนมัติ)"]
    subj = f"[ESPReL MRA มน.] {'ได้รับใบสมัคร ' + app_no + ' · ' if app_no else ''}ข้อมูลเข้าสู่ระบบ"
    return mailer.send(u["email"] or u["username"], subj, "\n".join(lines), kind="credentials", user_id=u["id"])


def content_disposition(name, inline=True):
    name = name or "file"
    ascii_name = secure_filename(name) or "file"
    return f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def save_upload(fs, prefix, allowed):
    """บันทึกไฟล์อัปโหลดไปยัง storage คืน (path, filename, content_type, size)"""
    ctype = (fs.mimetype or "").lower()
    if ctype not in allowed:
        raise ValueError("ชนิดไฟล์ไม่รองรับ (รองรับรูปภาพ JPG/PNG/WEBP" + (" และ PDF" if "application/pdf" in allowed else "") + ")")
    data = fs.read()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("ไฟล์ใหญ่เกิน 8 MB")
    name = _display_name(fs.filename) or "file"
    path = f"{prefix}/{secrets.token_hex(8)}{allowed[ctype]}"
    storage.save(path, data, ctype)
    return path, name, ctype, len(data)



def to_nu_account(db, user, nu_username, email=None):
    """บัญชีผู้ตรวจที่ระบบสร้างแบบ "อีเมลภายนอก + รหัสผ่าน" (ชื่อผู้ใช้เป็นอีเมล) → เปลี่ยนเป็นบัญชี NU (LDAP)
    ใช้เมื่อแก้อีเมลผู้ตรวจเป็น @nu.ac.th หรือเมื่อผู้ตรวจเข้าสู่ระบบด้วยบัญชี NU สำเร็จ
    ไม่แตะบัญชี admin/ห้องปฏิบัติการ หรือบัญชีที่ตั้งชื่อผู้ใช้เอง — คืน None ถ้าเปลี่ยนแล้ว/ไม่ต้องเปลี่ยน, ข้อความถ้าเปลี่ยนไม่ได้"""
    if not user or not nu_username or user["role"] not in ("assessor", "lead"):
        return None
    if "@" not in (user["username"] or ""):          # ไม่ใช่บัญชีอีเมลที่ระบบสร้าง
        return None
    if db.execute("SELECT 1 x FROM users WHERE lower(username)=? AND id<>?", (nu_username, user["id"])).fetchone():
        return f"มีบัญชีชื่อ “{nu_username}” อยู่แล้วอีกบัญชี — รวมบัญชีที่เมนู “ผู้ใช้”"
    db.execute("""UPDATE users SET username=?, email=COALESCE(?, email), auth_source='ldap', password_hash=NULL,
                  must_change_pw=0 WHERE id=?""", (nu_username, email, user["id"]))
    return None

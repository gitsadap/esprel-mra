"""ระบบ MRA ตรวจประเมินความปลอดภัยห้องปฏิบัติการ ESPReL – มหาวิทยาลัยนเรศวร
- ฐานข้อมูล: Supabase Postgres (DATABASE_URL) หรือ SQLite เมื่อรันในเครื่อง
- ไฟล์: Supabase Storage หรือโฟลเดอร์ instance/files
- เข้าสู่ระบบ: LDAP ของมหาวิทยาลัย + จับคู่บทบาทจากตาราง users
รันในเครื่อง:  python app.py   →  http://localhost:8080
"""
import io
import os
import re
import secrets
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import auth_ldap
import config
import db as dbm
import storage
from db import get_db, hash_pw, log, now
from scoring import (CHECKLIST, ELEMENTS, FOLLOWUP, FTYPES, ITEMS, SECTION_TITLES, compute,
                     level_of, thai_date)

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
APP_STATUS_TH = {"pending": ("รออนุมัติ", "amber"), "approved": ("อนุมัติแล้ว · รอจัดทีม", "blue"),
                 "assigned": ("จัดทีมแล้ว", "green"), "rejected": ("ไม่อนุมัติ", "red")}
IMG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
DOC_TYPES = dict(IMG_TYPES, **{"application/pdf": ".pdf"})


def create_app():
    app = Flask(__name__, instance_path=INSTANCE)
    os.makedirs(INSTANCE, exist_ok=True)
    secret = config.SECRET_KEY
    if not secret:
        key_file = os.path.join(INSTANCE, "secret.key")
        if not os.path.exists(key_file):
            with open(key_file, "w") as f:
                f.write(secrets.token_hex(32))
        secret = open(key_file).read().strip()
    app.config.update(
        SECRET_KEY=secret,
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=config.ON_VERCEL,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    dbm.init_db()
    app.teardown_appcontext(dbm.close_db)
    register(app)

    @app.cli.command("init-db")
    def _init_db():
        """สร้างตารางในฐานข้อมูล (ใช้กับ Supabase ครั้งแรก)"""
        dbm.init_db(force=True)
        print("initialized")
    return app


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


def save_upload(fs, prefix, allowed):
    """บันทึกไฟล์อัปโหลดไปยัง storage คืน (path, filename, content_type, size)"""
    ctype = (fs.mimetype or "").lower()
    if ctype not in allowed:
        raise ValueError("ชนิดไฟล์ไม่รองรับ (รองรับรูปภาพ JPG/PNG/WEBP" + (" และ PDF" if "application/pdf" in allowed else "") + ")")
    data = fs.read()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("ไฟล์ใหญ่เกิน 8 MB")
    name = secure_filename(fs.filename or "") or "file"
    path = f"{prefix}/{secrets.token_hex(8)}{allowed[ctype]}"
    storage.save(path, data, ctype)
    return path, name, ctype, len(data)


# ----------------------------------------------------------------- routes
def register(app):
    @app.before_request
    def before():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        if request.method == "POST" and not app.config.get("TESTING_NO_CSRF"):
            tok = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
            if not tok or tok != session.get("csrf"):
                if is_api():
                    return jsonify(ok=False, error="เซสชันหมดอายุ กรุณารีเฟรชหน้า"), 400
                flash("เซสชันหมดอายุ กรุณาลองใหม่อีกครั้ง", "error")
                return redirect(request.path)

    @app.context_processor
    def inject():
        return dict(ELEMENTS=ELEMENTS, CHECKLIST=CHECKLIST, FTYPES=FTYPES, FOLLOWUP=FOLLOWUP,
                    STATUS_TH=STATUS_TH, STATUS_ORDER=STATUS_ORDER, APP_STATUS_TH=APP_STATUS_TH, ROLE_TH=ROLE_TH,
                    thai_date=thai_date, level_of=level_of, is_lead=is_lead, is_staff=is_staff,
                    ITEMS=ITEMS, SECTION_TITLES=SECTION_TITLES, csrf_token=session.get("csrf", ""),
                    file_url=file_url, ldap_enabled=auth_ldap.enabled())

    @app.template_filter("short")
    def short_name(name):
        """ชื่อย่อสำหรับช่องแคบ: ตัดคำนำหน้า/ตำแหน่งทางวิชาการ เหลือชื่อแรก"""
        n = (name or "").strip()
        n = re.sub(r"^((ศ|รศ|ผศ|ดร|อ|นพ|พญ|ทพ|ภก|ภญ)\.)+\s*", "", n)
        n = re.sub(r"^(นางสาว|นาย|นาง)\s*", "", n)
        return n.split(" ")[0] if n else name

    # ---------- auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            u = db.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()
            ok_user = None
            if u and not u["active"]:
                flash("บัญชีนี้ถูกปิดการใช้งาน", "error")
                return render_template("login.html")
            if u and u["password_hash"] and config.ALLOW_LOCAL_LOGIN and check_pw(u["password_hash"], password):
                ok_user = u
            elif auth_ldap.enabled():
                try:
                    info = auth_ldap.authenticate(username, password)
                except auth_ldap.LDAPError as ex:
                    flash(str(ex), "error")
                    return render_template("login.html")
                if info:
                    if not u:
                        uid = db.insert("""INSERT INTO users(username,full_name,email,faculty,role,auth_source,created_at)
                                           VALUES(?,?,?,?,?,?,?)""",
                                        (info["username"], info["full_name"] or info["username"], info["email"],
                                         info["faculty"], config.LDAP_NEW_USER_ROLE, "ldap", now()))
                    else:
                        uid = u["id"]
                        db.execute("""UPDATE users SET auth_source='ldap', full_name=COALESCE(?, full_name),
                                      email=COALESCE(?, email), faculty=COALESCE(faculty, ?) WHERE id=?""",
                                   (info["full_name"], info["email"], info["faculty"], uid))
                    db.commit()
                    ok_user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if ok_user:
                db.execute("UPDATE users SET last_login=? WHERE id=?", (now(), ok_user["id"]))
                db.commit()
                csrf = session.get("csrf")
                session.clear()
                session.permanent = True
                session["uid"] = ok_user["id"]
                session["csrf"] = csrf or secrets.token_urlsafe(24)
                nxt = request.args.get("next")
                if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect(url_for("home"))
            flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/home")
    @login_required
    def home():
        return redirect(url_for("lab_home") if g.user["role"] == "lab" else url_for("dashboard"))

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            if g.user["auth_source"] == "ldap" and not g.user["password_hash"]:
                flash("บัญชี LDAP เปลี่ยนรหัสผ่านที่ระบบของมหาวิทยาลัย", "error")
            elif not check_pw(g.user["password_hash"], request.form.get("old", "")):
                flash("รหัสผ่านเดิมไม่ถูกต้อง", "error")
            elif len(request.form.get("new", "")) < 8:
                flash("รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร", "error")
            else:
                get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(request.form["new"]), g.user["id"]))
                get_db().commit()
                flash("เปลี่ยนรหัสผ่านเรียบร้อย", "ok")
                return redirect(url_for("home"))
        return render_template("account.html")

    # ---------- dashboard (ผู้ตรวจ)
    @app.route("/")
    @login_required
    def dashboard():
        if g.user["role"] == "lab":
            return redirect(url_for("lab_home"))
        db = get_db()
        year = request.args.get("year", type=int)
        years = [r["year_be"] for r in db.execute("SELECT DISTINCT year_be FROM audits ORDER BY year_be DESC")]
        q = "SELECT a.*, l.name lab_name, l.espre_no, l.affiliation FROM audits a JOIN labs l ON l.id=a.lab_id"
        args = ()
        if year:
            q += " WHERE a.year_be=?"
            args = (year,)
        q += " ORDER BY a.year_be DESC, l.name"
        audits, mine = [], []
        stats = {"labs": 0, "done": 0, "nc_open": 0, "avg": [], "status": {k: 0 for k in STATUS_TH}}
        for a in db.execute(q, args).fetchall():
            r1, r2 = summary_for(a["id"])
            rnd = current_round(a)
            cur = r2 if rnd == 2 else r1
            st = element_status(a["id"], rnd)
            nc_open = 0
            if a["status"] != "r1":
                r2resp = get_responses(a["id"], 2)
                for iid, r in get_responses(a["id"], 1).items():
                    if r["ftype"] == "NC":
                        f = r2resp.get(iid)
                        if not f or f["followup"] != "resolved":
                            nc_open += 1
            else:
                nc_open = r1["total"]["NC"]
            asg = assignments(a["id"])
            my_elements = sorted(e for e, v in asg.items() if v["user_id"] == g.user["id"])
            row = dict(a=a, r1=r1, r2=r2, rnd=rnd, cur=cur, done=sum(1 for s in st.values() if s["done"]),
                       nc_open=nc_open, my_elements=my_elements)
            audits.append(row)
            if my_elements and a["status"] in ("r1", "r2"):
                mine.append(row)
            stats["labs"] += 1
            stats["status"][a["status"]] = stats["status"].get(a["status"], 0) + 1
            stats["done"] += a["status"] == "r2_reported"
            stats["nc_open"] += nc_open
            if cur["total"]["answered"]:
                stats["avg"].append(cur["total"]["pct"])
        stats["avg"] = round(sum(stats["avg"]) / len(stats["avg"]), 1) if stats["avg"] else 0
        pending = db.execute("SELECT COUNT(*) n FROM applications WHERE status IN ('pending','approved')").fetchone()["n"]
        return render_template("dashboard.html", audits=audits, stats=stats, years=years, year=year, mine=mine,
                               pending=pending)

    # ---------- create / edit audit (ด้วยมือ)
    def _staff_users():
        return get_db().execute(
            "SELECT * FROM users WHERE active=1 AND role IN ('admin','lead','assessor') ORDER BY role, full_name").fetchall()

    def _attach_lab_user(lab_id, username):
        username = (username or "").strip().lower()
        if not username:
            return
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()
        uid = u["id"] if u else db.insert(
            "INSERT INTO users(username,full_name,role,auth_source,created_at) VALUES(?,?,?,?,?)",
            (username, username, "lab", "ldap", now()))
        db.execute("INSERT INTO lab_members(lab_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (lab_id, uid))

    def _save_team(audit_id, lead, member_ids, element_map):
        db = get_db()
        member_ids = set(member_ids)
        if lead:
            member_ids.add(lead)
        member_ids |= {u for u in element_map.values() if u}
        db.execute("DELETE FROM audit_team WHERE audit_id=?", (audit_id,))
        for uid in member_ids:
            db.execute("INSERT INTO audit_team(audit_id,user_id) VALUES(?,?)", (audit_id, uid))
        db.execute("DELETE FROM assignments WHERE audit_id=?", (audit_id,))
        for e, uid in element_map.items():
            if uid:
                db.execute("INSERT INTO assignments(audit_id,element,user_id) VALUES(?,?,?)", (audit_id, e, uid))

    def _save_audit_form(audit_id=None):
        db = get_db()
        f = request.form
        name = f.get("lab_name", "").strip()
        if not name:
            flash("กรุณาระบุชื่อห้องปฏิบัติการ", "error")
            return None
        labvals = (name, f.get("espre_no", "").strip(), f.get("faculty", "").strip(), f.get("affiliation", "").strip(),
                   f.get("location", "").strip(), f.get("head_name", "").strip(), f.get("contact", "").strip())
        r1_date = f.get("r1_date") or None
        year_be = f.get("year_be", type=int) or ((int(r1_date[:4]) if r1_date else dbm.datetime.now().year) + 543)
        lead = f.get("lead_user_id", type=int) or (g.user["id"] if is_lead() else None)
        if audit_id:
            a = load_audit(audit_id)
            lab_id = a["lab_id"]
            db.execute("UPDATE labs SET name=?,espre_no=?,faculty=?,affiliation=?,location=?,head_name=?,contact=? WHERE id=?",
                       labvals + (lab_id,))
            db.execute("""UPDATE audits SET year_be=?, r1_date=?, r2_date=?, lead_user_id=?, deadline=?, notes=?,
                          r1_report_date=?, r2_report_date=? WHERE id=?""",
                       (year_be, r1_date, f.get("r2_date") or None, lead, f.get("deadline") or None,
                        f.get("notes"), f.get("r1_report_date") or None, f.get("r2_report_date") or None, audit_id))
        else:
            lab_id = f.get("lab_id", type=int)
            if lab_id:
                db.execute("UPDATE labs SET name=?,espre_no=?,faculty=?,affiliation=?,location=?,head_name=?,contact=? WHERE id=?",
                           labvals + (lab_id,))
            else:
                lab_id = db.insert("""INSERT INTO labs(name,espre_no,faculty,affiliation,location,head_name,contact,status,created_at)
                                      VALUES(?,?,?,?,?,?,?,'approved',?)""", labvals + (now(),))
            audit_id = db.insert("""INSERT INTO audits(lab_id,year_be,r1_date,r2_date,status,lead_user_id,deadline,notes,created_at)
                                    VALUES(?,?,?,?,'r1',?,?,?,?)""",
                                 (lab_id, year_be, r1_date, f.get("r2_date") or None, lead,
                                  f.get("deadline") or None, f.get("notes"), now()))
        _attach_lab_user(lab_id, f.get("lab_username"))
        _save_team(audit_id, lead, [int(x) for x in f.getlist("team") if x],
                   {e: f.get(f"assign_{e}", type=int) for e in range(1, 8)})
        log(audit_id, "edit_audit")
        db.commit()
        return audit_id

    @app.route("/audit/new", methods=["GET", "POST"])
    @roles("lead", "admin")
    def audit_new():
        if request.method == "POST":
            aid = _save_audit_form()
            if aid:
                flash("สร้างรายการตรวจประเมินเรียบร้อย", "ok")
                return redirect(url_for("audit_view", audit_id=aid))
        labs = get_db().execute("SELECT * FROM labs ORDER BY name").fetchall()
        return render_template("audit_form.html", audit=None, users=_staff_users(), team_ids=set(), asg={},
                               labs=labs, lab_users=[])

    @app.route("/audit/<int:audit_id>/edit", methods=["GET", "POST"])
    @roles("lead", "admin")
    def audit_edit(audit_id):
        a = load_audit(audit_id)
        if request.method == "POST":
            if _save_audit_form(audit_id):
                flash("บันทึกข้อมูลเรียบร้อย", "ok")
                return redirect(url_for("audit_view", audit_id=audit_id))
        lab_users = get_db().execute(
            """SELECT u.* FROM users u WHERE u.id IN (SELECT user_id FROM lab_members WHERE lab_id=?)
               OR u.id=(SELECT owner_user_id FROM labs WHERE id=?)""", (a["lab_id"], a["lab_id"])).fetchall()
        return render_template("audit_form.html", audit=a, users=_staff_users(),
                               team_ids={u["id"] for u in team(audit_id)}, asg=assignments(audit_id), labs=[],
                               lab_users=lab_users)

    @app.route("/audit/<int:audit_id>/delete", methods=["POST"])
    @roles("admin", "lead")
    def audit_delete(audit_id):
        load_audit(audit_id)
        db = get_db()
        for att in db.execute("SELECT path FROM attachments WHERE audit_id=?", (audit_id,)).fetchall():
            storage.delete(att["path"])
        db.execute("UPDATE applications SET audit_id=NULL, status='approved' WHERE audit_id=?", (audit_id,))
        db.execute("DELETE FROM audits WHERE id=?", (audit_id,))
        log(audit_id, "delete_audit")
        db.commit()
        flash("ลบรายการตรวจประเมินแล้ว", "ok")
        return redirect(url_for("dashboard"))

    # ---------- lab summary (ผู้ตรวจ)
    @app.route("/audit/<int:audit_id>")
    @roles(*STAFF)
    def audit_view(audit_id):
        a = load_audit(audit_id)
        r1, r2 = summary_for(audit_id)
        rnd = request.args.get("round", type=int) or current_round(a)
        resp = {1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)}
        reports = get_db().execute(
            "SELECT r.*, u.full_name FROM reports r LEFT JOIN users u ON u.id=r.created_by "
            "WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
        follow = {iid for iid, r in resp[1].items() if r["ftype"] in ("NC", "OBS")}
        logs = get_db().execute(
            """SELECT l.*, u.full_name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id
               WHERE audit_id=? ORDER BY l.id DESC LIMIT 30""", (audit_id,)).fetchall()
        return render_template("audit.html", a=a, r1=r1, r2=r2, rnd=rnd, resp=resp, capa=get_capa(audit_id),
                               st={1: element_status(audit_id, 1), 2: element_status(audit_id, 2)},
                               asg=assignments(audit_id), team=team(audit_id), reports=reports,
                               editable=round_editable(a, rnd), follow=follow,
                               r2prog=r2_progress(audit_id, follow), atts=get_attachments(audit_id), logs=logs)

    # ---------- assessment form
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>")
    @roles(*STAFF)
    def assess(audit_id, rnd, el):
        if el not in ELEMENTS or rnd not in (1, 2):
            abort(404)
        a = load_audit(audit_id)
        if rnd == 2 and a["status"] not in ("r2", "r2_reported"):
            flash("ยังไม่ได้เริ่มการตรวจรอบที่ 2 (ต้องรอห้องปฏิบัติการส่ง CAPA)", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        resp = get_responses(audit_id, rnd)
        r1resp = get_responses(audit_id, 1) if rnd == 2 else {}
        follow = {iid for iid, r in r1resp.items() if r["ftype"] in ("NC", "OBS")} if rnd == 2 else set()
        summ = compute(resp)
        groups = []
        for n in ELEMENTS[el]["nodes"]:
            if n["kind"] == "section":
                groups.append({"sec": n, "nodes": []})
                continue
            if not groups:
                groups.append({"sec": None, "nodes": []})
            if n["kind"] == "option" and groups[-1]["nodes"] and groups[-1]["nodes"][-1]["kind"] == "item":
                opts = groups[-1]["nodes"][-1].setdefault("_opts", [])
                if n["text"] not in opts:
                    opts.append(n["text"])
                continue
            groups[-1]["nodes"].append(dict(n))
        return render_template("assess.html", a=a, rnd=rnd, el=ELEMENTS[el], resp=resp, r1resp=r1resp,
                               summ=summ, es=summ["elements"][el], st=element_status(audit_id, rnd),
                               asg=assignments(audit_id), groups=groups, editable=round_editable(a, rnd),
                               follow=follow, capa=get_capa(audit_id) if rnd == 2 else {},
                               atts=get_attachments(audit_id), r2prog=r2_progress(audit_id) if rnd == 2 else {})

    def _el_json(summ, el):
        e = summ["elements"][el]
        out = {k: e[k] for k in ("no", "max", "got", "pct", "answered", "count", "progress", "NC", "OBS", "SUG")}
        out.update(sections=e["sections"], level=level_of(e["pct"]))
        return out

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/item/<item_id>", methods=["POST"])
    @roles(*STAFF)
    def api_item(audit_id, rnd, item_id):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว (ออกรายงานแล้ว)"), 409
        it = ITEMS.get(item_id)
        if not it:
            return jsonify(ok=False, error="ไม่พบรายการ"), 404
        if rnd == 2 and item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="รอบ 2 ตรวจเฉพาะข้อที่เป็น NC/OBS ในรอบ 1"), 409
        data = request.get_json(force=True) or {}
        db = get_db()
        row = db.execute("SELECT * FROM responses WHERE audit_id=? AND round=? AND item_id=?",
                         (audit_id, rnd, item_id)).fetchone()
        vals = dict(row) if row else {"score": None, "ftype": None, "comment": None, "followup": None,
                                      "fnote": None, "carried": 0}
        before = {k: vals.get(k) for k in ("score", "ftype", "comment", "followup")}
        if "score" in data:
            s = data["score"]
            if s in (None, ""):
                vals["score"] = None
            elif s == "NA":
                vals["score"] = "NA"
            else:
                try:
                    v = int(s)
                except (TypeError, ValueError):
                    return jsonify(ok=False, error="คะแนนไม่ถูกต้อง"), 400
                if v < 0 or v > it["max"]:
                    return jsonify(ok=False, error="คะแนนเกินคะแนนเต็ม"), 400
                vals["score"] = str(v)
            vals["carried"] = 0
        if "ftype" in data:
            vals["ftype"] = data["ftype"] if data["ftype"] in FTYPES else None
        if "comment" in data:
            vals["comment"] = (data["comment"] or "").strip() or None
        if "followup" in data:
            vals["followup"] = data["followup"] if data["followup"] in FOLLOWUP else None
        if "fnote" in data:
            vals["fnote"] = (data["fnote"] or "").strip() or None
        if data.get("confirm"):
            vals["carried"] = 0
        db.execute("""INSERT INTO responses(audit_id,round,item_id,score,ftype,comment,followup,fnote,carried,updated_by,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(audit_id,round,item_id) DO UPDATE SET score=excluded.score, ftype=excluded.ftype,
                      comment=excluded.comment, followup=excluded.followup, fnote=excluded.fnote,
                      carried=excluded.carried, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                   (audit_id, rnd, item_id, vals["score"], vals["ftype"], vals["comment"], vals["followup"],
                    vals["fnote"], vals["carried"], g.user["id"], now()))
        after = {k: vals.get(k) for k in ("score", "ftype", "comment", "followup")}
        changed = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
        if changed:
            log(audit_id, "item", rnd, item_id, repr(changed))
        db.commit()
        summ = compute(get_responses(audit_id, rnd))
        return jsonify(ok=True, item=vals, element=_el_json(summ, it["element"]), total=summ["total"])

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/item/<item_id>/photo", methods=["POST"])
    @roles(*STAFF)
    def api_photo(audit_id, rnd, item_id):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        if item_id not in ITEMS:
            return jsonify(ok=False, error="ไม่พบรายการ"), 404
        fs = request.files.get("file")
        if not fs:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 400
        try:
            path, name, ctype, size = save_upload(fs, f"audits/{audit_id}/r{rnd}/{item_id}", IMG_TYPES)
        except (ValueError, RuntimeError) as ex:
            return jsonify(ok=False, error=str(ex)), 400
        db = get_db()
        att_id = db.insert("""INSERT INTO attachments(audit_id,round,item_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (audit_id, rnd, item_id, "finding", path, name, ctype, size, g.user["id"], now()))
        log(audit_id, "photo_add", rnd, item_id, name)
        db.commit()
        return jsonify(ok=True, id=att_id, url=url_for("file_view", att_id=att_id))

    @app.route("/api/attachment/<int:att_id>/delete", methods=["POST"])
    @login_required
    def api_attachment_delete(att_id):
        db = get_db()
        att = db.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not att:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 404
        a = load_audit(att["audit_id"])
        if att["kind"] == "finding":
            if not is_staff() or not round_editable(a, att["round"]):
                return jsonify(ok=False, error="ไม่มีสิทธิ์ลบ"), 403
        else:
            if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
                return jsonify(ok=False, error="ไม่มีสิทธิ์ลบ"), 403
        storage.delete(att["path"])
        db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        log(a["id"], "file_delete", att["round"], att["item_id"], att["filename"])
        db.commit()
        return jsonify(ok=True)

    @app.route("/file/<int:att_id>")
    @login_required
    def file_view(att_id):
        att = get_db().execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not att:
            abort(404)
        a = load_audit(att["audit_id"])
        if not is_staff():
            if not is_lab_member(a["lab_id"]) or (att["kind"] == "finding" and not lab_may_see_results(a)):
                abort(403)
        url = storage.signed_url(att["path"])
        if url:
            return redirect(url)
        try:
            data = storage.load(att["path"])
        except FileNotFoundError:
            abort(404)
        return Response(data, mimetype=att["content_type"] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/fill", methods=["POST"])
    @roles(*STAFF)
    def api_fill(audit_id, rnd, el):
        """ให้คะแนนเต็มกับข้อที่ยังไม่ได้ตรวจ (รอบ 1) หรือยืนยันคะแนนที่ยกมา (รอบ 2)"""
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        data = request.get_json(force=True) or {}
        section = data.get("section")
        mode = data.get("mode", "full")
        db = get_db()
        resp = get_responses(audit_id, rnd)
        follow = follow_items(audit_id) if rnd == 2 else None
        n = 0
        for node in ELEMENTS[el]["nodes"]:
            if node["kind"] != "item" or (section and node["section"] != section):
                continue
            if follow is not None and node["id"] not in follow:
                continue
            r = resp.get(node["id"])
            if mode == "confirm":
                if r and r["carried"]:
                    db.execute("UPDATE responses SET carried=0, updated_by=?, updated_at=? WHERE audit_id=? AND round=? AND item_id=?",
                               (g.user["id"], now(), audit_id, rnd, node["id"]))
                    n += 1
                continue
            if rnd == 2 or (r and r["score"] is not None):
                continue
            db.execute("""INSERT INTO responses(audit_id,round,item_id,score,carried,updated_by,updated_at) VALUES(?,?,?,?,0,?,?)
                          ON CONFLICT(audit_id,round,item_id) DO UPDATE SET score=excluded.score, carried=0,
                          updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                       (audit_id, rnd, node["id"], str(node["max"]), g.user["id"], now()))
            n += 1
        log(audit_id, "fill_" + mode, rnd, None, f"element {el} section {section} ({n} ข้อ)")
        db.commit()
        return jsonify(ok=True, changed=n)

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/done", methods=["POST"])
    @roles(*STAFF)
    def api_done(audit_id, rnd, el):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        data = request.get_json(force=True) or {}
        done = 1 if data.get("done") else 0
        db = get_db()
        db.execute("""INSERT INTO element_status(audit_id,round,element,done,done_by,done_at) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(audit_id,round,element) DO UPDATE SET done=excluded.done, done_by=excluded.done_by,
                      done_at=excluded.done_at""", (audit_id, rnd, el, done, g.user["id"], now()))
        log(audit_id, "element_done" if done else "element_undone", rnd, None, f"element {el}")
        db.commit()
        st = element_status(audit_id, rnd)
        return jsonify(ok=True, done=done, all_done=sum(1 for s in st.values() if s["done"]) == 7)

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/summary", methods=["POST"])
    @roles(*STAFF)
    def api_el_summary(audit_id, rnd, el):
        data = request.get_json(force=True) or {}
        db = get_db()
        db.execute("""INSERT INTO element_status(audit_id,round,element,done,summary) VALUES(?,?,?,0,?)
                      ON CONFLICT(audit_id,round,element) DO UPDATE SET summary=excluded.summary""",
                   (audit_id, rnd, el, (data.get("summary") or "").strip() or None))
        db.commit()
        return jsonify(ok=True)

    # ---------- workflow (หัวหน้าผู้ตรวจ)
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/report", methods=["POST"])
    @roles("lead", "admin")
    def make_report(audit_id, rnd):
        from reports import build_docx
        a = load_audit(audit_id)
        if rnd == 2 and a["status"] not in ("r2", "r2_reported"):
            flash("ยังไม่ได้ตรวจรอบที่ 2", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        db = get_db()
        if request.form.get("report_date"):
            db.execute(f"UPDATE audits SET r{rnd}_report_date=? WHERE id=?", (request.form["report_date"], audit_id))
        if rnd == 1 and request.form.get("deadline"):
            db.execute("UPDATE audits SET deadline=? WHERE id=?", (request.form["deadline"], audit_id))
        db.commit()
        a = load_audit(audit_id)
        fname, data = build_docx(a, rnd)
        path = storage.save(f"reports/{audit_id}/{secrets.token_hex(4)}_{fname}", data,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        db.insert("INSERT INTO reports(audit_id,round,filename,path,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (audit_id, rnd, fname, path, g.user["id"], now()))
        if rnd == 1 and a["status"] == "r1":
            db.execute("UPDATE audits SET status='r1_reported', r1_report_at=? WHERE id=?", (now(), audit_id))
        if rnd == 2 and a["status"] == "r2":
            db.execute("UPDATE audits SET status='r2_reported', r2_report_at=? WHERE id=?", (now(), audit_id))
        log(audit_id, f"report{rnd}", rnd, None, fname)
        db.commit()
        flash(f"สร้าง Report {rnd} เรียบร้อย" + (" — ห้องปฏิบัติการเข้าดูผลและตอบ CAPA ได้แล้ว" if rnd == 1 else ""), "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=rnd))

    @app.route("/report/<int:report_id>/download")
    @login_required
    def report_download(report_id):
        r = get_db().execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not r:
            abort(404)
        a = load_audit(r["audit_id"])
        if not can_view_audit(a):
            abort(403)
        data = storage.load(r["path"])
        return send_file(io.BytesIO(data), as_attachment=True, download_name=r["filename"],
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.route("/audit/<int:audit_id>/round/<int:rnd>/report/view")
    @login_required
    def report_view(audit_id, rnd):
        """รายงานรูปแบบ PDF (A4) — พิมพ์/บันทึกเป็น PDF จากเบราว์เซอร์"""
        from reports import build_report_data
        a = load_audit(audit_id)
        if not can_view_audit(a):
            abort(403)
        issued = (rnd == 1 and a["status"] != "r1") or (rnd == 2 and a["status"] == "r2_reported")
        if not is_staff() and not issued:
            flash("รายงานฉบับนี้ยังไม่ออก", "error")
            return redirect(url_for("lab_audit", audit_id=audit_id))
        data = build_report_data(a, rnd, with_photos=True)
        return render_template("report_pdf.html", a=a, rnd=rnd, d=data, issued=issued)

    @app.route("/audit/<int:audit_id>/capa/return", methods=["POST"])
    @roles("lead", "admin")
    def capa_return(audit_id):
        a = load_audit(audit_id)
        if a["status"] != "capa_submitted":
            flash("สถานะปัจจุบันส่ง CAPA กลับไม่ได้", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        note = request.form.get("note", "").strip()
        db = get_db()
        db.execute("UPDATE audits SET status='r1_reported', capa_return_note=? WHERE id=?", (note or None, audit_id))
        log(audit_id, "capa_return", None, None, note)
        db.commit()
        flash("ส่ง CAPA กลับให้ห้องปฏิบัติการแก้ไขแล้ว", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id))

    @app.route("/audit/<int:audit_id>/start-round2", methods=["POST"])
    @roles("lead", "admin")
    def start_round2(audit_id):
        a = load_audit(audit_id)
        if a["status"] != "capa_submitted":
            flash("เริ่มรอบที่ 2 ได้หลังจากห้องปฏิบัติการส่ง CAPA แล้วเท่านั้น", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        db = get_db()
        db.execute("UPDATE audits SET status='r2', r2_date=COALESCE(?, r2_date) WHERE id=?",
                   (request.form.get("r2_date") or None, audit_id))
        ensure_round2(audit_id)
        log(audit_id, "start_r2")
        db.commit()
        flash("เริ่มการตรวจรอบที่ 2 แล้ว — ตรวจเฉพาะข้อที่เป็น NC/OBS พร้อม CAPA ของห้องปฏิบัติการ", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=2))

    @app.route("/audit/<int:audit_id>/reopen/<int:rnd>", methods=["POST"])
    @roles("lead", "admin")
    def reopen(audit_id, rnd):
        a = load_audit(audit_id)
        db = get_db()
        if rnd == 1 and a["status"] == "r1_reported":
            db.execute("UPDATE audits SET status='r1' WHERE id=?", (audit_id,))
        elif rnd == 2 and a["status"] == "r2_reported":
            db.execute("UPDATE audits SET status='r2' WHERE id=?", (audit_id,))
        else:
            flash("ไม่สามารถเปิดแก้ไขรอบนี้ได้", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        log(audit_id, f"reopen_r{rnd}", rnd, None, request.form.get("reason"))
        db.commit()
        flash(f"เปิดให้แก้ไขผลการตรวจรอบที่ {rnd} อีกครั้ง (เมื่อแก้ไขเสร็จ ให้กดสร้าง Report ใหม่)", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=rnd))

    # ---------- closing meeting
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/closing")
    @roles(*STAFF)
    def closing(audit_id, rnd):
        a = load_audit(audit_id)
        r1, r2 = summary_for(audit_id)
        return render_template("closing.html", a=a, rnd=rnd, r1=r1, r2=r2,
                               resp={1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)},
                               st=element_status(audit_id, rnd), asg=assignments(audit_id), team=team(audit_id),
                               capa=get_capa(audit_id), atts=get_attachments(audit_id))

    # ---------- LAB PORTAL
    @app.route("/lab")
    @login_required
    def lab_home():
        db = get_db()
        if is_staff() and request.args.get("as") != "lab":
            return redirect(url_for("dashboard"))
        labs = db.execute(
            """SELECT * FROM labs WHERE owner_user_id=? OR id IN (SELECT lab_id FROM lab_members WHERE user_id=?)
               ORDER BY name""", (g.user["id"], g.user["id"])).fetchall()
        items = []
        for l in labs:
            apps = db.execute("SELECT * FROM applications WHERE lab_id=? ORDER BY year_be DESC", (l["id"],)).fetchall()
            audits = db.execute("SELECT * FROM audits WHERE lab_id=? ORDER BY year_be DESC", (l["id"],)).fetchall()
            aud = []
            for a in audits:
                r1, r2 = summary_for(a["id"])
                need = sum(1 for r in get_responses(a["id"], 1).values() if r["ftype"] in ("NC", "OBS"))
                aud.append({"a": a, "r1": r1, "r2": r2, "need": need})
            items.append({"lab": l, "apps": apps, "audits": aud})
        return render_template("lab_home.html", items=items, this_year=dbm.datetime.now().year + 543)

    @app.route("/lab/register", methods=["GET", "POST"])
    @login_required
    def lab_register():
        db = get_db()
        lab_id = request.args.get("lab_id", type=int) or request.form.get("lab_id", type=int)
        lab = db.execute("SELECT * FROM labs WHERE id=?", (lab_id,)).fetchone() if lab_id else None
        if lab and not (is_lab_member(lab["id"]) or is_staff()):
            abort(403)
        this_year = dbm.datetime.now().year + 543
        if request.method == "POST":
            f = request.form
            name = f.get("name", "").strip()
            year_be = f.get("year_be", type=int) or this_year
            if not name or not f.get("faculty", "").strip():
                flash("กรุณากรอกชื่อห้องปฏิบัติการและคณะ/หน่วยงาน", "error")
                return render_template("lab_register.html", lab=lab or f, this_year=this_year)
            vals = (name, f.get("espre_no", "").strip(), f.get("faculty", "").strip(), f.get("affiliation", "").strip(),
                    f.get("location", "").strip(), f.get("head_name", "").strip(), f.get("phone", "").strip(),
                    f.get("email", "").strip(), f.get("contact", "").strip())
            if lab:
                db.execute("""UPDATE labs SET name=?,espre_no=?,faculty=?,affiliation=?,location=?,head_name=?,phone=?,
                              email=?,contact=? WHERE id=?""", vals + (lab["id"],))
                lid = lab["id"]
            else:
                lid = db.insert("""INSERT INTO labs(name,espre_no,faculty,affiliation,location,head_name,phone,email,contact,
                                   owner_user_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                                vals + (g.user["id"], now()))
                db.execute("INSERT INTO lab_members(lab_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (lid, g.user["id"]))
            if f.get("apply"):
                db.execute("""INSERT INTO applications(lab_id,year_be,status,note,created_by,created_at) VALUES(?,?,'pending',?,?,?)
                              ON CONFLICT(lab_id,year_be) DO NOTHING""",
                           (lid, year_be, f.get("note", "").strip() or None, g.user["id"], now()))
            db.commit()
            flash("บันทึกข้อมูลห้องปฏิบัติการแล้ว" + (f" และส่งใบสมัครเข้ารับการตรวจปี {year_be}" if f.get("apply") else ""), "ok")
            return redirect(url_for("lab_home", **({"as": "lab"} if is_staff() else {})))
        return render_template("lab_register.html", lab=lab, this_year=this_year)

    @app.route("/lab/audit/<int:audit_id>")
    @login_required
    def lab_audit(audit_id):
        a = load_audit(audit_id)
        if not can_view_audit(a):
            abort(403)
        r1, r2 = summary_for(audit_id)
        resp = {1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)}
        follow = [iid for iid in sorted(resp[1], key=lambda x: tuple(map(int, x.split("-"))))
                  if resp[1][iid]["ftype"] in ("NC", "OBS")]
        reports = get_db().execute("SELECT * FROM reports WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
        return render_template("lab_audit.html", a=a, r1=r1, r2=r2, resp=resp, follow=follow, capa=get_capa(audit_id),
                               atts=get_attachments(audit_id), visible=lab_may_see_results(a) or is_staff(),
                               capa_open=a["status"] == "r1_reported" and is_lab_member(a["lab_id"]),
                               reports=reports)

    @app.route("/api/lab/audit/<int:audit_id>/capa/<item_id>", methods=["POST"])
    @login_required
    def api_capa(audit_id, item_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]):
            return jsonify(ok=False, error="ไม่มีสิทธิ์"), 403
        if a["status"] != "r1_reported":
            return jsonify(ok=False, error="ส่ง CAPA แล้ว หรือยังไม่ถึงขั้นตอนตอบ CAPA"), 409
        if item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="ข้อนี้ไม่ต้องตอบ CAPA"), 400
        d = request.get_json(force=True) or {}
        db = get_db()
        row = db.execute("SELECT * FROM capa WHERE audit_id=? AND item_id=?", (audit_id, item_id)).fetchone()
        vals = dict(row) if row else {"root_cause": None, "action": None, "owner": None, "due_date": None}
        for k in ("root_cause", "action", "owner", "due_date"):
            if k in d:
                vals[k] = (d[k] or "").strip() or None
        db.execute("""INSERT INTO capa(audit_id,item_id,root_cause,action,owner,due_date,updated_by,updated_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(audit_id,item_id) DO UPDATE SET root_cause=excluded.root_cause,
                      action=excluded.action, owner=excluded.owner, due_date=excluded.due_date,
                      updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                   (audit_id, item_id, vals["root_cause"], vals["action"], vals["owner"], vals["due_date"],
                    g.user["id"], now()))
        db.commit()
        return jsonify(ok=True)

    @app.route("/api/lab/audit/<int:audit_id>/capa/<item_id>/file", methods=["POST"])
    @login_required
    def api_capa_file(audit_id, item_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
            return jsonify(ok=False, error="ไม่มีสิทธิ์อัปโหลด"), 403
        if item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="ข้อนี้ไม่ต้องตอบ CAPA"), 400
        fs = request.files.get("file")
        if not fs:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 400
        try:
            path, name, ctype, size = save_upload(fs, f"audits/{audit_id}/capa/{item_id}", DOC_TYPES)
        except (ValueError, RuntimeError) as ex:
            return jsonify(ok=False, error=str(ex)), 400
        db = get_db()
        att_id = db.insert("""INSERT INTO attachments(audit_id,round,item_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (audit_id, 1, item_id, "capa", path, name, ctype, size, g.user["id"], now()))
        db.commit()
        return jsonify(ok=True, id=att_id, url=url_for("file_view", att_id=att_id), name=name, ctype=ctype)

    @app.route("/lab/audit/<int:audit_id>/capa/submit", methods=["POST"])
    @login_required
    def capa_submit(audit_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
            abort(403)
        capa = get_capa(audit_id)
        missing = [ITEMS[i]["ref"] for i in follow_items(audit_id) if not (capa.get(i) and capa[i]["action"])]
        if missing:
            flash("ยังไม่ได้ระบุการแก้ไขในข้อ: " + ", ".join(sorted(missing)), "error")
            return redirect(url_for("lab_audit", audit_id=audit_id))
        db = get_db()
        db.execute("UPDATE audits SET status='capa_submitted', capa_submitted_at=?, capa_return_note=NULL WHERE id=?",
                   (now(), audit_id))
        log(audit_id, "capa_submit")
        db.commit()
        flash("ส่ง CAPA เรียบร้อย คณะผู้ตรวจจะนัดตรวจติดตามครั้งที่ 2", "ok")
        return redirect(url_for("lab_audit", audit_id=audit_id))

    # ---------- PLANNING: ใบสมัคร + จัดทีมผู้ตรวจ
    @app.route("/planning", methods=["GET", "POST"])
    @roles("lead", "admin")
    def planning():
        from planner import propose
        db = get_db()
        year = request.values.get("year", type=int) or dbm.datetime.now().year + 543
        if request.method == "POST":
            act = request.form.get("action")
            if act in ("approve", "reject"):
                ids = [int(x) for x in request.form.getlist("app_id")]
                for i in ids:
                    db.execute("UPDATE applications SET status=? WHERE id=? AND status IN ('pending','approved','rejected')",
                               ("approved" if act == "approve" else "rejected", i))
                    if act == "approve":
                        db.execute("UPDATE labs SET status='approved' WHERE id=(SELECT lab_id FROM applications WHERE id=?)", (i,))
                db.commit()
                flash(f"{'อนุมัติ' if act == 'approve' else 'ไม่อนุมัติ'} {len(ids)} ใบสมัคร", "ok")
            elif act == "team_size":
                db.execute("INSERT INTO settings(key,value) VALUES('team_size',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (str(max(1, min(7, request.form.get("team_size", type=int) or 3))),))
                db.commit()
            elif act == "create":
                n = 0
                for app_id in [int(x) for x in request.form.getlist("plan_app")]:
                    ap = db.execute("SELECT * FROM applications WHERE id=? AND status='approved' AND audit_id IS NULL",
                                    (app_id,)).fetchone()
                    if not ap:
                        continue
                    lead = request.form.get(f"lead_{app_id}", type=int)
                    members = [int(x) for x in request.form.getlist(f"members_{app_id}") if x]
                    emap = {e: request.form.get(f"el_{app_id}_{e}", type=int) for e in range(1, 8)}
                    aid = db.insert("""INSERT INTO audits(lab_id,year_be,r1_date,status,lead_user_id,created_at)
                                       VALUES(?,?,?,'r1',?,?)""",
                                    (ap["lab_id"], ap["year_be"], request.form.get(f"date_{app_id}") or None, lead, now()))
                    _save_team(aid, lead, members, emap)
                    db.execute("UPDATE applications SET status='assigned', audit_id=? WHERE id=?", (aid, app_id))
                    log(aid, "created_from_plan")
                    n += 1
                db.commit()
                flash(f"สร้างการตรวจประเมิน {n} ห้อง พร้อมทีมผู้ตรวจแล้ว", "ok")
            return redirect(url_for("planning", year=year))
        apps = db.execute(
            """SELECT ap.*, l.name lab_name, l.faculty, l.affiliation, l.espre_no, l.head_name, l.phone, l.email,
                      u.full_name applicant FROM applications ap JOIN labs l ON l.id=ap.lab_id
               LEFT JOIN users u ON u.id=ap.created_by WHERE ap.year_be=? ORDER BY ap.status, l.faculty, l.name""",
            (year,)).fetchall()
        assessors = [dict(u) for u in _staff_users() if u["role"] in ("lead", "assessor")] or \
            [dict(u) for u in _staff_users()]
        existing = {}
        for r in db.execute("""SELECT t.user_id, COUNT(*) n FROM audit_team t JOIN audits a ON a.id=t.audit_id
                               WHERE a.year_be=? GROUP BY t.user_id""", (year,)).fetchall():
            existing[r["user_id"]] = r["n"]
        to_plan = [dict(id=ap["id"], name=ap["lab_name"], faculty=ap["faculty"], affiliation=ap["affiliation"])
                   for ap in apps if ap["status"] == "approved" and not ap["audit_id"]]
        team_size = int(dbm.get_setting("team_size") or 3)
        proposals, load = propose(to_plan, assessors, team_size, existing)
        years = sorted({r["year_be"] for r in db.execute("SELECT DISTINCT year_be FROM applications").fetchall()} | {year},
                       reverse=True)
        return render_template("planning.html", year=year, years=years, apps=apps, proposals=proposals,
                               assessors=assessors, load=load, existing=existing, team_size=team_size)

    # ---------- Excel export / import
    @app.route("/audit/<int:audit_id>/export.xlsx")
    @roles(*STAFF)
    def export_xlsx(audit_id):
        from reports import build_xlsx
        a = load_audit(audit_id)
        bio = build_xlsx(a)
        name = secure_filename(f"ESPReL_{a['espre_no'] or audit_id}_{a['year_be']}.xlsx") or "export.xlsx"
        return send_file(bio, as_attachment=True, download_name=name,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.route("/import", methods=["GET", "POST"])
    @roles("lead", "admin")
    def import_xlsx():
        if request.method == "POST":
            f = request.files.get("file")
            if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
                flash("กรุณาเลือกไฟล์ Excel (.xlsx)", "error")
                return redirect(url_for("import_xlsx"))
            path = os.path.join(INSTANCE, f"import_{secrets.token_hex(4)}.xlsx")
            f.save(path)
            try:
                from importer import import_workbook
                aid = import_workbook(path, rounds=request.form.get("rounds", "1"), user_id=g.user["id"])
            except Exception as ex:  # noqa
                get_db().rollback()
                flash(f"นำเข้าไม่สำเร็จ: {ex}", "error")
                return redirect(url_for("import_xlsx"))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            flash("นำเข้าข้อมูลจาก Excel เรียบร้อย — กรุณาตรวจสอบประเภทข้อค้นพบ (NC/OBS/ข้อเสนอแนะ) อีกครั้ง", "ok")
            return redirect(url_for("audit_view", audit_id=aid))
        return render_template("import.html")

    # ---------- users & settings
    @app.route("/users", methods=["GET", "POST"])
    @roles("admin", "lead")
    def users():
        db = get_db()
        if request.method == "POST":
            act = request.form.get("action")
            if act == "add":
                un = request.form.get("username", "").strip().lower()
                if not re.match(r"^[a-z0-9_.\-@]{2,60}$", un):
                    flash("ชื่อผู้ใช้ (LDAP) ต้องเป็นภาษาอังกฤษ/ตัวเลข", "error")
                elif db.execute("SELECT 1 x FROM users WHERE lower(username)=?", (un,)).fetchone():
                    flash("มีชื่อผู้ใช้นี้แล้ว — แก้ไขบทบาทในรายการด้านซ้าย", "error")
                else:
                    role = request.form.get("role", "assessor")
                    if role == "admin" and g.user["role"] != "admin":
                        role = "assessor"
                    pw = request.form.get("password", "").strip()
                    db.execute("""INSERT INTO users(username,password_hash,full_name,email,faculty,role,auth_source,created_at)
                                  VALUES(?,?,?,?,?,?,?,?)""",
                               (un, hash_pw(pw) if pw else None, request.form.get("full_name", un).strip() or un,
                                request.form.get("email", "").strip() or None, request.form.get("faculty", "").strip() or None,
                                role, "local" if pw else "ldap", now()))
                    db.commit()
                    flash("เพิ่มผู้ใช้เรียบร้อย", "ok")
            elif act == "update":
                uid = request.form.get("uid", type=int)
                u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if u and (g.user["role"] == "admin" or u["role"] != "admin"):
                    role = request.form.get("role", u["role"])
                    if role == "admin" and g.user["role"] != "admin":
                        role = u["role"]
                    db.execute("UPDATE users SET full_name=?, faculty=?, email=?, role=?, active=? WHERE id=?",
                               (request.form.get("full_name", u["full_name"]), request.form.get("faculty") or None,
                                request.form.get("email") or None, role, 1 if request.form.get("active") else 0, uid))
                    if request.form.get("password"):
                        db.execute("UPDATE users SET password_hash=?, auth_source='local' WHERE id=?",
                                   (hash_pw(request.form["password"]), uid))
                    db.commit()
                    flash("บันทึกข้อมูลผู้ใช้แล้ว", "ok")
            return redirect(url_for("users", role=request.args.get("role")))
        role = request.args.get("role")
        q = "SELECT * FROM users"
        args = ()
        if role:
            q += " WHERE role=?"
            args = (role,)
        return render_template("users.html", users=db.execute(q + " ORDER BY active DESC, role, full_name", args).fetchall(),
                               role=role)

    @app.route("/settings", methods=["GET", "POST"])
    @roles("admin", "lead")
    def settings():
        db = get_db()
        if request.method == "POST":
            for k in dbm.DEFAULT_SETTINGS:
                if k in ("qr_image", "team_size"):
                    continue
                if k in request.form:
                    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (k, request.form[k]))
            f = request.files.get("qr")
            if f and f.filename:
                try:
                    path, *_ = save_upload(f, "settings", IMG_TYPES)
                    db.execute("INSERT INTO settings(key,value) VALUES('qr_image',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (path,))
                except (ValueError, RuntimeError) as ex:
                    flash(str(ex), "error")
            if request.form.get("qr_reset"):
                db.execute("UPDATE settings SET value='' WHERE key='qr_image'")
            db.commit()
            flash("บันทึกการตั้งค่าแล้ว", "ok")
            return redirect(url_for("settings"))
        return render_template("settings.html", s=dbm.all_settings())

    @app.route("/qr.png")
    def qr_image():
        path = dbm.get_setting("qr_image")
        if path:
            try:
                return Response(storage.load(path), mimetype="image/png")
            except Exception:  # noqa
                pass
        return send_from_directory(os.path.join(BASE, "static", "img"), "qr_default.png")

    @app.route("/healthz")
    def healthz():
        get_db().execute("SELECT 1 x").fetchone()
        return jsonify(ok=True, db="postgres" if dbm.IS_PG else "sqlite", storage="supabase" if storage.USE_SUPABASE else "local",
                       ldap=auth_ldap.enabled())

    @app.errorhandler(500)
    def server_error(e):
        import traceback
        orig = getattr(e, "original_exception", None) or e
        tb = "".join(traceback.format_exception(type(orig), orig, orig.__traceback__))
        try:
            with open(os.path.join(INSTANCE, "error.log"), "a", encoding="utf-8") as f:
                f.write(f"\n===== {now()} {request.method} {request.path}\n{tb}")
        except OSError:
            pass
        print(tb, flush=True)
        return render_template("error.html", msg="เกิดข้อผิดพลาดในระบบ",
                               detail=f"{type(orig).__name__}: {orig}" if is_staff() else None), 500

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", msg="ไม่มีสิทธิ์เข้าถึงข้อมูลนี้"), 403

    @app.errorhandler(404)
    def nf(_e):
        return render_template("error.html", msg="ไม่พบหน้าที่ต้องการ"), 404


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  ESPReL MRA กำลังทำงานที่  http://localhost:{port}\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False)

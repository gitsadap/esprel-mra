"""เข้าสู่ระบบ/ออก, ลืมรหัสผ่าน, บัญชีของฉัน"""
import secrets
from datetime import datetime, timedelta

from flask import flash, g, redirect, render_template, request, session, url_for

import auth_ldap
import config
import mailer
from core import base_url, check_pw, login_required, make_reset_token, read_reset_token
from db import get_db, hash_pw, log, now


# ----------------------------------------------------------------- เข้าสู่ระบบ
def client_ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


def _too_many_fails(username, ip):
    since = (datetime.now() - timedelta(minutes=config.LOGIN_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    by_user = db.execute("SELECT COUNT(*) n FROM audit_log WHERE action='login_fail' AND item_id=? AND created_at>?",
                         (username[:120], since)).fetchone()["n"]
    by_ip = db.execute("SELECT COUNT(*) n FROM audit_log WHERE action='login_fail' AND detail=? AND created_at>?",
                       (ip, since)).fetchone()["n"]
    return by_user >= config.LOGIN_MAX_FAILS or by_ip >= config.LOGIN_MAX_FAILS * 3


def _authenticate(raw, password):
    """คืน (แถว users, ข้อความผิดพลาด)
    1) บัญชีรหัสผ่านภายในระบบ (ห้องปฏิบัติการ/ผู้ตรวจภายนอก/admin ตั้งต้น)
    2) บัญชี มน.: ยืนยันตัวตนที่ LDAP (ผ่านเซิร์ฟเวอร์ภายใน) ก่อน → ผ่านแล้วจึงตรวจรายชื่อในฐานข้อมูล"""
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE lower(username)=?", (raw,)).fetchone()
    if not u and "@" in raw:   # ห้องปฏิบัติการเข้าสู่ระบบด้วยอีเมลที่ลงทะเบียน
        u = db.execute("SELECT * FROM users WHERE lower(email)=? AND role='lab' ORDER BY id LIMIT 1", (raw,)).fetchone()
    if u and not u["active"]:
        return None, "บัญชีนี้ถูกปิดการใช้งาน"
    # บัญชีอีเมล+รหัสผ่านใช้ได้เสมอ · ALLOW_LOCAL_LOGIN คุมบัญชีทดสอบ/admin ตั้งต้น
    local_ok = config.ALLOW_LOCAL_LOGIN or (u and (u["role"] == "lab" or "@" in u["username"]))
    if u and u["password_hash"] and local_ok and check_pw(u["password_hash"], password):
        return u, None
    nu_user = auth_ldap.normalize_username(raw)
    if not (auth_ldap.enabled() and nu_user and password):
        return None, None
    try:
        info = auth_ldap.authenticate(nu_user, password)          # ขั้นที่ 1: LDAP
    except auth_ldap.LDAPError as ex:
        return None, str(ex)
    if not info:
        return None, None
    return _staff_account(info)                                    # ขั้นที่ 2: ฐานข้อมูล


def _staff_account(info):
    """LDAP ผ่านแล้ว → หา/เปิดบัญชีในฐานข้อมูล: ตาราง users ก่อน แล้วจึงทะเบียนผู้ตรวจ (อีเมล @nu.ac.th ตรงกัน)"""
    db = get_db()
    un = info["username"]
    emails = tuple({e for e in (f"{un}@nu.ac.th", (info.get("email") or "").lower()) if e})
    marks = ",".join("?" * len(emails))
    u = db.execute("SELECT * FROM users WHERE lower(username)=?", (un,)).fetchone() or db.execute(
        f"SELECT * FROM users WHERE lower(email) IN ({marks}) AND role<>'lab' ORDER BY id LIMIT 1", emails).fetchone()
    if u:
        if not u["active"]:
            return None, "บัญชีนี้ถูกปิดการใช้งาน — โปรดติดต่อผู้ดูแลระบบ"
        # ชื่อที่ admin เพิ่มไว้เป็นแค่ชื่อผู้ใช้ → แทนด้วยชื่อจาก LDAP
        name = info.get("full_name") if info.get("full_name") and u["full_name"] == u["username"] else u["full_name"]
        db.execute("""UPDATE users SET auth_source=CASE WHEN password_hash IS NULL THEN 'ldap' ELSE auth_source END,
                      email=COALESCE(email, ?), faculty=COALESCE(faculty, ?), full_name=? WHERE id=?""",
                   (info.get("email"), info.get("faculty"), name, u["id"]))
        db.commit()
        return db.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone(), None
    a = db.execute(f"SELECT * FROM assessors WHERE active=1 AND lower(email) IN ({marks}) ORDER BY id LIMIT 1",
                   emails).fetchone()
    if a:   # อยู่ในทะเบียนผู้ตรวจ → เปิดบัญชีผู้ตรวจให้อัตโนมัติ
        uid = db.insert("""INSERT INTO users(username,full_name,email,faculty,role,auth_source,created_at)
                           VALUES(?,?,?,?,'assessor','ldap',?)""",
                        (un, a["full_name"], a["email"] or emails[0], a["faculty"] or info.get("faculty"), now()))
        db.execute("UPDATE assessors SET user_id=?, updated_at=? WHERE id=?", (uid, now(), a["id"]))
        db.commit()
        return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), None
    if config.LDAP_NEW_USER_ROLE:
        uid = db.insert("""INSERT INTO users(username,full_name,email,faculty,role,auth_source,created_at)
                           VALUES(?,?,?,?,?,'ldap',?)""",
                        (un, info.get("full_name") or un, info.get("email"), info.get("faculty"), config.LDAP_NEW_USER_ROLE, now()))
        db.commit()
        return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), None
    return None, ("ยืนยันตัวตนกับบัญชีมหาวิทยาลัยสำเร็จ แต่ยังไม่มีชื่อท่านในระบบ — โปรดติดต่อหัวหน้าผู้ตรวจ/ผู้ดูแลระบบ "
                  "(ห้องปฏิบัติการ: เข้าสู่ระบบด้วยอีเมลและรหัสผ่านที่ได้รับหลังส่งใบสมัคร)")


def _start_session(user, ip):
    db = get_db()
    db.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
    log(None, "login", item_id=user["username"][:120], detail=ip)
    db.commit()
    csrf = session.get("csrf")
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["csrf"] = csrf or secrets.token_urlsafe(24)
    nxt = request.args.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("home"))


def register(app):
    # ---------- auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            raw = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            ip = client_ip()
            if _too_many_fails(raw, ip):
                flash(f"ลองเข้าสู่ระบบผิดหลายครั้ง — กรุณารอ {config.LOGIN_WINDOW_MINUTES} นาทีแล้วลองใหม่", "error")
                return render_template("login.html"), 429
            ok_user, err = _authenticate(raw, password)
            if ok_user:
                return _start_session(ok_user, ip)
            log(None, "login_fail", item_id=raw[:120], detail=ip)
            get_db().commit()
            flash(err or "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        """ลืมรหัสผ่าน (บัญชีภายในระบบ) → ส่งลิงก์ตั้งรหัสผ่านใหม่ทางอีเมล"""
        if request.method == "POST":
            em = request.form.get("email", "").strip().lower()
            db = get_db()
            u = db.execute("""SELECT * FROM users WHERE (lower(email)=? OR lower(username)=?) AND active=1
                              AND password_hash IS NOT NULL ORDER BY id LIMIT 1""", (em, em)).fetchone()
            if u and (u["email"] or "@" in u["username"]):
                link = f"{base_url()}{url_for('reset_pw', token=make_reset_token(u))}"
                body = "\n".join([f"เรียน {u['full_name']}", "",
                                   "มีการขอตั้งรหัสผ่านใหม่สำหรับบัญชี ESPReL MRA มหาวิทยาลัยนเรศวร ของท่าน",
                                   f"  ชื่อผู้ใช้ : {u['username']}",
                                   "กดลิงก์ต่อไปนี้เพื่อตั้งรหัสผ่านใหม่ (ใช้ได้ภายใน 24 ชั่วโมง และใช้ได้ครั้งเดียว)",
                                   f"  {link}", "", "หากท่านไม่ได้ขอ ไม่ต้องดำเนินการใด ๆ รหัสผ่านเดิมยังใช้ได้",
                                   "", "ระบบ ESPReL MRA มหาวิทยาลัยนเรศวร (อีเมลอัตโนมัติ)"])
                st, _ = mailer.send(u["email"] or u["username"], "[ESPReL MRA มน.] ตั้งรหัสผ่านใหม่", body,
                                    kind="reset", user_id=u["id"])
                if st == "dev" and config.MAIL_DEV_SHOW:
                    flash("โหมดทดสอบ (ยังไม่ได้ตั้งค่า SMTP) — ลิงก์ตั้งรหัสผ่านใหม่: " + link, "ok")
            flash("หากอีเมลนี้มีบัญชีในระบบ ท่านจะได้รับลิงก์ตั้งรหัสผ่านใหม่ภายในไม่กี่นาที (ตรวจสอบกล่องจดหมายขยะด้วย)", "ok")
            return redirect(url_for("login"))
        return render_template("forgot.html")

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset_pw(token):
        u = read_reset_token(token)
        if not u:
            flash("ลิงก์ตั้งรหัสผ่านใหม่ไม่ถูกต้องหรือหมดอายุแล้ว — กรุณาขอใหม่", "error")
            return redirect(url_for("forgot"))
        if request.method == "POST":
            pw = request.form.get("new", "")
            if len(pw) < 8:
                flash("รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร", "error")
            elif pw != request.form.get("confirm"):
                flash("ยืนยันรหัสผ่านไม่ตรงกัน", "error")
            else:
                db = get_db()
                db.execute("UPDATE users SET password_hash=?, must_change_pw=0, auth_source='local' WHERE id=?",
                           (hash_pw(pw), u["id"]))
                db.commit()
                flash("ตั้งรหัสผ่านใหม่แล้ว — เข้าสู่ระบบด้วยรหัสผ่านใหม่", "ok")
                return redirect(url_for("login"))
        return render_template("reset.html", u=u)

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
                if request.form["new"] == request.form.get("old"):
                    flash("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม", "error")
                    return render_template("account.html")
                get_db().execute("UPDATE users SET password_hash=?, must_change_pw=0 WHERE id=?",
                                 (hash_pw(request.form["new"]), g.user["id"]))
                get_db().commit()
                flash("เปลี่ยนรหัสผ่านเรียบร้อย", "ok")
                return redirect(url_for("home"))
        return render_template("account.html")

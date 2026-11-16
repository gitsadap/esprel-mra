"""เข้าสู่ระบบ/ออก, ลืมรหัสผ่าน, บัญชีของฉัน"""
import secrets

from flask import flash, g, redirect, render_template, request, session, url_for

import auth_ldap
import config
import mailer
from core import base_url, check_pw, login_required, make_reset_token, read_reset_token
from db import get_db, hash_pw, now


def register(app):
    # ---------- auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            u = db.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()
            if not u and "@" in username:   # ห้องปฏิบัติการเข้าสู่ระบบด้วยอีเมลที่ลงทะเบียน
                u = db.execute("SELECT * FROM users WHERE lower(email)=? AND role='lab' ORDER BY id LIMIT 1",
                               (username,)).fetchone()
            ok_user = None
            if u and not u["active"]:
                flash("บัญชีนี้ถูกปิดการใช้งาน", "error")
                return render_template("login.html")
            # บัญชีอีเมล+รหัสผ่าน (ห้องปฏิบัติการ/ผู้ตรวจภายนอก) ใช้ได้เสมอ · ALLOW_LOCAL_LOGIN คุมบัญชีทดสอบ/admin ตั้งต้น
            local_ok = config.ALLOW_LOCAL_LOGIN or (u and (u["role"] == "lab" or "@" in u["username"]))
            if u and u["password_hash"] and local_ok and check_pw(u["password_hash"], password):
                ok_user = u
            elif auth_ldap.enabled() and "@" not in username:
                try:
                    info = auth_ldap.authenticate(username, password)
                except auth_ldap.LDAPError as ex:
                    flash(str(ex), "error")
                    return render_template("login.html")
                if info and not u and not config.LDAP_NEW_USER_ROLE:
                    flash("บัญชีมหาวิทยาลัยของท่านยังไม่ได้รับสิทธิ์ในระบบ — โปรดติดต่อผู้ดูแลระบบ "
                          "(ห้องปฏิบัติการ: เข้าสู่ระบบด้วยอีเมลและรหัสผ่านที่ได้รับหลังส่งใบสมัคร)", "error")
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

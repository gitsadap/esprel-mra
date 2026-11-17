"""นำเข้า/ส่งออก Excel, ผู้ใช้, ตั้งค่า, healthz, หน้าข้อผิดพลาด"""
import os
import re
import secrets
import traceback

from flask import Response, flash, g, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from werkzeug.utils import secure_filename

import assessors as assessors_mod
import auth_ldap
import db as dbm
import storage
from core import (
    BASE,
    IMG_TYPES,
    INSTANCE,
    STAFF,
    gen_password,
    is_staff,
    load_audit,
    mail_credentials,
    roles,
    save_upload,
    sync_nu_accounts,
    to_nu_account,
)
from db import get_db, hash_pw, now


def register(app):
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

    def _flash_nu_sync(db):
        done = sync_nu_accounts(db)
        if done:
            flash("เปลี่ยนเป็นบัญชี NU (LDAP) แล้ว: " + ", ".join(done) + " — เข้าสู่ระบบด้วยชื่อผู้ใช้นี้และรหัสผ่าน NU", "ok")

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
                    gen = None
                    if not pw and "@" in un:   # ผู้ใช้ภายนอก (ชื่อผู้ใช้เป็นอีเมล) → สร้างรหัสผ่านและส่งทางอีเมล
                        gen = pw = gen_password()
                    uid = db.insert("""INSERT INTO users(username,password_hash,full_name,email,faculty,role,auth_source,
                                     must_change_pw,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                                    (un, hash_pw(pw) if pw else None, request.form.get("full_name", un).strip() or un,
                                     request.form.get("email", "").strip() or (un if "@" in un else None),
                                     request.form.get("faculty", "").strip() or None,
                                     role, "local" if pw else "ldap", 1 if gen else 0, now()))
                    db.commit()
                    if gen:
                        st, err = mail_credentials(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), gen)
                        if st == "sent":
                            flash(f"เพิ่มผู้ใช้แล้ว และส่งรหัสผ่านไปที่ {un}", "ok")
                        else:
                            flash(f"เพิ่มผู้ใช้แล้ว — {'โหมดทดสอบ' if st == 'dev' else 'ส่งอีเมลไม่สำเร็จ (' + str(err) + ')'}: "
                                  f"รหัสผ่านชั่วคราว {gen}", "ok" if st == "dev" else "error")
                    else:
                        flash("เพิ่มผู้ใช้เรียบร้อย", "ok")
                    if role == "observer":
                        flash("ขั้นต่อไป: เปิดหน้าการตรวจของห้องที่ต้องการ (เมนู “ภาพรวม” → คลิกชื่อห้อง) → การ์ด “คณะผู้ตรวจประเมิน” "
                              "→ เลือกชื่อแล้วกด “+ เชิญ”", "ok")
            elif act == "resetpw":
                u = db.execute("SELECT * FROM users WHERE id=?", (request.form.get("uid", type=int),)).fetchone()
                if u and (g.user["role"] == "admin" or u["role"] in ("lab", "observer")):
                    pw = gen_password()
                    db.execute("UPDATE users SET password_hash=?, must_change_pw=1, auth_source='local' WHERE id=?",
                               (hash_pw(pw), u["id"]))
                    db.commit()
                    u = db.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
                    st, err = mail_credentials(u, pw)
                    if st == "sent":
                        flash(f"ส่งรหัสผ่านใหม่ไปที่ {u['email'] or u['username']} แล้ว", "ok")
                    elif st == "dev":
                        flash(f"โหมดทดสอบ (ยังไม่ได้ตั้งค่า SMTP) — รหัสผ่านใหม่ของ {u['username']}: {pw}", "ok")
                    else:
                        flash(f"ตั้งรหัสผ่านใหม่แล้วแต่ส่งอีเมลไม่สำเร็จ ({err}) — รหัสผ่านใหม่: {pw}", "error")
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
                    email = (request.form.get("email") or "").strip().lower()
                    if email:   # อีเมลบัญชีกับทะเบียนผู้ตรวจต้องตรงกัน
                        db.execute("UPDATE assessors SET email=?, updated_at=? WHERE user_id=?", (email, now(), uid))
                    err = to_nu_account(db, db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(),
                                        assessors_mod.ldap_username(email), email) if role in ("assessor", "lead") else None
                    if err:
                        flash(err, "error")
                    if request.form.get("password"):
                        db.execute("UPDATE users SET password_hash=?, auth_source='local' WHERE id=?",
                                   (hash_pw(request.form["password"]), uid))
                    db.commit()
                    flash("บันทึกข้อมูลผู้ใช้แล้ว", "ok")
            return redirect(url_for("users", role=request.args.get("role")))
        _flash_nu_sync(db)
        role = request.args.get("role")
        q = "SELECT * FROM users"
        args = ()
        if role:
            q += " WHERE role=?"
            args = (role,)
        lab_map = {}
        for r in db.execute("""SELECT m.user_id, l.name FROM lab_members m JOIN labs l ON l.id=m.lab_id
                               UNION SELECT owner_user_id, name FROM labs WHERE owner_user_id IS NOT NULL""").fetchall():
            if r["name"] not in lab_map.setdefault(r["user_id"], []):
                lab_map[r["user_id"]].append(r["name"])
        return render_template("users.html", users=db.execute(q + " ORDER BY active DESC, role, full_name", args).fetchall(),
                               role=role, lab_map=lab_map)

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
                    path, *_ = save_upload(f, "settings", IMG_TYPES, shrink=False)
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
                       ldap=auth_ldap.mode() or False)

    @app.errorhandler(500)
    def server_error(e):
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

"""คำขอทุกหน้า: ตรวจผู้ใช้/CSRF/บังคับเปลี่ยนรหัส, ตัวแปรกลางของ template, ตัวกรอง"""
import re
import secrets

from flask import flash, g, jsonify, redirect, request, session, url_for

import auth_ldap
from core import APP_STATUS_TH, ROLE_TH, STATUS_ORDER, STATUS_TH, file_url, is_api, is_lead, is_staff
from db import get_db
from scoring import CHECKLIST, ELEMENTS, FOLLOWUP, FTYPES, ITEMS, SECTION_TITLES, level_of, thai_date


def register(app):
    @app.before_request
    def before():
        g.user = None
        if request.endpoint == "static":      # ไฟล์ CSS/JS/รูป ไม่ต้องใช้ฐานข้อมูล
            return None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        if (g.user and g.user["must_change_pw"] and request.endpoint not in ("account", "logout", "static")
                and not request.path.startswith("/static")):
            if is_api():
                return jsonify(ok=False, error="กรุณาตั้งรหัสผ่านใหม่ก่อนใช้งาน"), 403
            return redirect(url_for("account"))
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

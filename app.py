"""ระบบ MRA ตรวจประเมินความปลอดภัยห้องปฏิบัติการ ESPReL – มหาวิทยาลัยนเรศวร
- ฐานข้อมูล: Supabase Postgres (DATABASE_URL) หรือ SQLite เมื่อรันในเครื่อง
- ไฟล์: Supabase Storage หรือโฟลเดอร์ instance/files
- เข้าสู่ระบบ: LDAP มหาวิทยาลัย (ผู้ตรวจ) / อีเมล + รหัสผ่าน (ห้องปฏิบัติการ, ผู้ตรวจภายนอก)
- หน้าเว็บแยกตามส่วนงานในโฟลเดอร์ views/ · ฟังก์ชันกลางใน core.py
รันในเครื่อง:  python app.py   →  http://localhost:8080
"""
import os
import secrets

from flask import Flask

import config
import db as dbm
from views import admin, assess, audits, auth, base, lab, planning

INSTANCE = config.INSTANCE


def create_app():
    app = Flask(__name__, instance_path=INSTANCE)
    os.makedirs(INSTANCE, exist_ok=True)
    secret = config.SECRET_KEY
    if not secret:
        key_file = os.path.join(INSTANCE, "secret.key")
        if not os.path.exists(key_file):
            with open(key_file, "w") as f:
                f.write(secrets.token_hex(32))
        with open(key_file) as f:
            secret = f.read().strip()
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
    for mod in (base, auth, audits, assess, lab, planning, admin):
        mod.register(app)

    @app.cli.command("init-db")
    def _init_db():
        """สร้างตารางในฐานข้อมูล (ใช้กับ Supabase ครั้งแรก)"""
        dbm.init_db(force=True)
        print("initialized")
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  ESPReL MRA กำลังทำงานที่  http://localhost:{port}\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False)


"""การตั้งค่าทั้งหมดอ่านจาก Environment Variables (ตั้งใน Vercel → Project → Settings → Environment Variables
หรือไฟล์ .env สำหรับรันในเครื่อง)"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    p = os.path.join(BASE, ".env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


ON_VERCEL = bool(env("VERCEL"))
# Vercel เขียนไฟล์ได้เฉพาะ /tmp
INSTANCE = env("INSTANCE_DIR", "/tmp/esprel" if ON_VERCEL else os.path.join(BASE, "instance"))

# ---- ฐานข้อมูล: Supabase Postgres (production) หรือ SQLite (พัฒนาในเครื่อง)
# Supabase → Project Settings → Database → Connection string → "Transaction pooler" (port 6543)
DATABASE_URL = env("DATABASE_URL")            # postgresql://postgres.xxxx:password@aws-0-xx.pooler.supabase.com:6543/postgres
SQLITE_PATH = env("SQLITE_PATH", os.path.join(INSTANCE, "esprel.db"))
AUTO_MIGRATE = env("AUTO_MIGRATE", "1" if not DATABASE_URL else "0") == "1"

# ---- ไฟล์รูปหลักฐาน/รายงาน: Supabase Storage (bucket แบบ private)
SUPABASE_URL = env("SUPABASE_URL")            # https://xxxx.supabase.co
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_ROLE_KEY")   # ใช้ฝั่งเซิร์ฟเวอร์เท่านั้น ห้ามใส่ในหน้าเว็บ
STORAGE_BUCKET = env("STORAGE_BUCKET", "esprel")

# ---- LDAP มหาวิทยาลัย
LDAP_URL = env("LDAP_URL")                    # ldaps://ldap.nu.ac.th:636
LDAP_BASE_DN = env("LDAP_BASE_DN")            # dc=nu,dc=ac,dc=th
LDAP_USER_DN_TEMPLATE = env("LDAP_USER_DN_TEMPLATE")   # uid={username},ou=people,dc=nu,dc=ac,dc=th  (bind ตรง)
LDAP_UPN_SUFFIX = env("LDAP_UPN_SUFFIX")      # @nu.ac.th  (Active Directory: bind เป็น user@domain)
LDAP_BIND_DN = env("LDAP_BIND_DN")            # บัญชีบริการสำหรับค้นหา (ถ้ามี)
LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD")
LDAP_USER_FILTER = env("LDAP_USER_FILTER", "(|(uid={username})(sAMAccountName={username}))")
LDAP_ATTR_NAME = env("LDAP_ATTR_NAME", "displayName")
LDAP_ATTR_MAIL = env("LDAP_ATTR_MAIL", "mail")
LDAP_ATTR_DEPT = env("LDAP_ATTR_DEPT", "department")
LDAP_TIMEOUT = int(env("LDAP_TIMEOUT", "8"))
# ผู้ใช้ LDAP ที่ยังไม่อยู่ในระบบ จะถูกสร้างเป็นบทบาทนี้อัตโนมัติ (ผู้ใช้ฝั่งห้องปฏิบัติการ)
LDAP_NEW_USER_ROLE = env("LDAP_NEW_USER_ROLE", "lab")
# อนุญาตบัญชีรหัสผ่านภายในระบบ (ใช้สำหรับ admin ตั้งต้น/ทดสอบ) — production แนะนำเปิดเฉพาะ admin
ALLOW_LOCAL_LOGIN = env("ALLOW_LOCAL_LOGIN", "1") == "1"

SECRET_KEY = env("SECRET_KEY")
ADMIN_INITIAL_PASSWORD = env("ADMIN_INITIAL_PASSWORD", "admin1234")
SEED_DEMO_USERS = env("SEED_DEMO_USERS", "0" if DATABASE_URL else "1") == "1"

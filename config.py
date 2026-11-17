"""การตั้งค่าทั้งหมดอ่านจาก Environment Variables (ตั้งใน Vercel → Project → Settings → Environment Variables
หรือไฟล์ .env สำหรับรันในเครื่อง)"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    p = os.path.join(BASE, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
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
# ใส่รหัสผ่านฐานข้อมูลแยกได้ที่ SUPABASE_DB_PASSWORD แล้วเว้น [YOUR-PASSWORD] ไว้ใน DATABASE_URL (ระบบเข้ารหัสอักขระพิเศษให้)
if DATABASE_URL and "[YOUR-PASSWORD]" in DATABASE_URL:
    from urllib.parse import quote as _quote
    _pw = env("SUPABASE_DB_PASSWORD")
    if _pw:
        DATABASE_URL = DATABASE_URL.replace("[YOUR-PASSWORD]", _quote(_pw, safe=""))
    else:
        print("\n  ⚠ ยังไม่ได้ใส่ SUPABASE_DB_PASSWORD ในไฟล์ .env — ใช้ฐานข้อมูลทดสอบในเครื่อง (SQLite) แทน Supabase\n")
        DATABASE_URL = None
SQLITE_PATH = env("SQLITE_PATH", os.path.join(INSTANCE, "esprel.db"))
AUTO_MIGRATE = env("AUTO_MIGRATE", "1" if not DATABASE_URL else "0") == "1"

# ---- ไฟล์รูปหลักฐาน/รายงาน: Supabase Storage (bucket แบบ private)
SUPABASE_URL = env("SUPABASE_URL")            # https://xxxx.supabase.co
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_ROLE_KEY")   # ใช้ฝั่งเซิร์ฟเวอร์เท่านั้น ห้ามใส่ในหน้าเว็บ
STORAGE_BUCKET = env("STORAGE_BUCKET", "esprel")

# ---- LDAP มหาวิทยาลัย (ผู้ตรวจ/ทีมงาน) — ยืนยันตัวตนที่ LDAP ก่อน แล้วจึงตรวจรายชื่อผู้ใช้ในฐานข้อมูล (Supabase)
# แบบ A: แอปเรียก "เซิร์ฟเวอร์ภายใน มน." (LDAP gateway, ดู ldap_gateway/) ผ่าน HTTPS — ใช้เมื่อแอปอยู่บน Vercel/คลาวด์
LDAP_GATEWAY_URL = env("LDAP_GATEWAY_URL")    # https://<เซิร์ฟเวอร์ภายใน>/api/esprel/ldap-verify
LDAP_GATEWAY_KEY = env("LDAP_GATEWAY_KEY")    # คีย์ลับร่วมกับ gateway (ESPREL_GATEWAY_KEY ฝั่งเซิร์ฟเวอร์)
# แบบ B: แอปติดตั้งในเครือข่าย มน. แล้ว bind LDAP เอง (แบบเดียวกับระบบ NRED)
LDAP_URL = env("LDAP_URL")                    # ldap://10.10.10.71  หรือ ldaps://...:636
LDAP_UPN_SUFFIX = env("LDAP_UPN_SUFFIX")      # @nu.local  (Active Directory: bind เป็น user@domain)
LDAP_REQUIRE_TLS = env("LDAP_REQUIRE_TLS", "0") == "1"   # 1 = บังคับ StartTLS เมื่อใช้ ldap://
LDAP_TLS_VALIDATE = env("LDAP_TLS_VALIDATE", "1") == "1" # ตรวจใบรับรองเซิร์ฟเวอร์ LDAP
LDAP_CA_CERT_FILE = env("LDAP_CA_CERT_FILE")  # ไฟล์ CA ของ มน. (ถ้าใช้ใบรับรองภายใน)
LDAP_BASE_DN = env("LDAP_BASE_DN")            # DC=nu,DC=local — ถ้าระบุ จะดึงชื่อ/อีเมล/หน่วยงานหลัง bind
LDAP_USER_DN_TEMPLATE = env("LDAP_USER_DN_TEMPLATE")   # uid={username},ou=people,dc=... (OpenLDAP แบบ bind ตรง)
LDAP_BIND_DN = env("LDAP_BIND_DN")            # บัญชีบริการสำหรับค้นหา (ถ้ามี)
LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD")
LDAP_USER_FILTER = env("LDAP_USER_FILTER", "(|(uid={username})(sAMAccountName={username}))")
LDAP_ATTR_NAME = env("LDAP_ATTR_NAME", "displayName")
LDAP_ATTR_MAIL = env("LDAP_ATTR_MAIL", "mail")
LDAP_ATTR_DEPT = env("LDAP_ATTR_DEPT", "department")
LDAP_TIMEOUT = int(env("LDAP_TIMEOUT", "8"))
# จำกัดการเข้าสู่ระบบผิด (ต่อชื่อผู้ใช้หรือ IP) ภายในช่วงเวลา
LOGIN_MAX_FAILS = int(env("LOGIN_MAX_FAILS", "10"))
LOGIN_WINDOW_MINUTES = int(env("LOGIN_WINDOW_MINUTES", "15"))
# LDAP ใช้สำหรับผู้ตรวจ/ทีมงานเท่านั้น: ผู้ใช้ LDAP ที่ admin ยังไม่ได้เพิ่มในเมนู "ผู้ใช้" จะเข้าสู่ระบบไม่ได้
# (ตั้งเป็น assessor เพื่อสร้างบัญชีผู้ตรวจอัตโนมัติ — ไม่แนะนำ)
LDAP_NEW_USER_ROLE = env("LDAP_NEW_USER_ROLE", "")
# บัญชีรหัสผ่านภายในระบบของ "เจ้าหน้าที่" (admin ตั้งต้น/ทดสอบ) — production ปิดได้เมื่อมีผู้ดูแลที่เป็น LDAP แล้ว
# (บัญชีห้องปฏิบัติการ = อีเมล + รหัสผ่านที่ระบบสร้าง ใช้ได้เสมอ)
ALLOW_LOCAL_LOGIN = env("ALLOW_LOCAL_LOGIN", "1") == "1"

# ---- อีเมล (ส่งชื่อผู้ใช้/รหัสผ่านให้ห้องปฏิบัติการ, ลิงก์ตั้งรหัสผ่านใหม่) — SMTP ของมหาวิทยาลัยหรือบัญชีอีเมลหน่วยงาน
SMTP_HOST = env("SMTP_HOST")                  # เช่น smtp.office365.com / smtp.gmail.com / mail.nu.ac.th
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
SMTP_SECURITY = env("SMTP_SECURITY", "starttls")   # starttls | ssl | none
MAIL_FROM = env("MAIL_FROM", SMTP_USER or "esprel@nu.ac.th")
MAIL_FROM_NAME = env("MAIL_FROM_NAME", "ESPReL MRA มหาวิทยาลัยนเรศวร")
# URL ของระบบ (ใช้ในลิงก์ในอีเมล) เช่น https://esprel-mra.vercel.app
APP_BASE_URL = env("APP_BASE_URL")
# ยังไม่ตั้งค่า SMTP + รันในเครื่อง → แสดงรหัสผ่านบนหน้าจอแทนการส่งอีเมล (โหมดทดสอบเท่านั้น)
MAIL_DEV_SHOW = env("MAIL_DEV_SHOW", "1" if not SMTP_HOST and not ON_VERCEL else "0") == "1"
# จำกัดการส่งใบสมัครแบบไม่ต้องเข้าสู่ระบบ (ต่อ IP ต่อชั่วโมง)
PUBLIC_APPLY_PER_HOUR = int(env("PUBLIC_APPLY_PER_HOUR", "10"))

SECRET_KEY = env("SECRET_KEY")
ADMIN_INITIAL_PASSWORD = env("ADMIN_INITIAL_PASSWORD", "admin1234")
SEED_DEMO_USERS = env("SEED_DEMO_USERS", "0" if DATABASE_URL else "1") == "1"

"""ESPReL LDAP gateway — ติดตั้งบน "เซิร์ฟเวอร์ภายใน มน." ที่เข้าถึง LDAP/AD ได้ (เช่นเครื่องเดียวกับระบบ NRED)

แอป ESPReL MRA บน Vercel ส่ง {username, password} มาที่ POST /api/esprel/ldap-verify (HTTPS + คีย์ลับ)
gateway bind LDAP แบบเดียวกับ NRED (user@nu.local) แล้วตอบแค่ "ผ่าน/ไม่ผ่าน" + ชื่อ/อีเมล/หน่วยงาน (ถ้าอ่านได้)
ไม่เก็บรหัสผ่าน ไม่ออก token — การตรวจสิทธิ์ในระบบ ESPReL ทำที่ฐานข้อมูล Supabase ต่อจากนี้

ใช้ได้ 2 แบบ
  1) เพิ่มเข้า NRED (FastAPI):   คัดลอกไฟล์นี้ไป app/routers/ แล้วใน main.py
         from app.routers.esprel_ldap_gateway import router as esprel_ldap_router
         app.include_router(esprel_ldap_router)
  2) รันแยก:                     uvicorn esprel_ldap_gateway:app --host 127.0.0.1 --port 8701
                                 (ให้ nginx/Apache ทำ HTTPS แล้ว proxy มาที่พอร์ตนี้)

ตั้งค่า (environment / .env ของเซิร์ฟเวอร์)
  ESPREL_GATEWAY_KEY   คีย์ลับยาว ๆ (ต้องตรงกับ LDAP_GATEWAY_KEY ของแอป ESPReL)   — จำเป็น
  ESPREL_LDAP_SERVER   ldap://10.10.10.71        (ค่าเริ่มต้นเหมือน NRED)
  ESPREL_LDAP_DOMAIN   nu.local                  → bind เป็น user@nu.local
  ESPREL_LDAP_BASE_DN  DC=nu,DC=local            (ว่าง = ไม่ดึงชื่อ/อีเมล)
  ESPREL_LDAP_REQUIRE_TLS 0/1 · ESPREL_LDAP_TLS_VALIDATE 0/1 · ESPREL_LDAP_CA_CERT_FILE · ESPREL_LDAP_TIMEOUT
  ESPREL_ALLOWED_IPS   (ไม่บังคับ) จำกัด IP ผู้เรียก คั่นด้วย ,
"""
import hmac
import os
import re
import ssl
import threading
import time

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel


def _env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


GATEWAY_KEY = _env("ESPREL_GATEWAY_KEY")
LDAP_SERVER = _env("ESPREL_LDAP_SERVER", "ldap://10.10.10.71")
LDAP_DOMAIN = _env("ESPREL_LDAP_DOMAIN", "nu.local")
LDAP_BASE_DN = _env("ESPREL_LDAP_BASE_DN", "")
REQUIRE_TLS = _env("ESPREL_LDAP_REQUIRE_TLS", "0") == "1"
TLS_VALIDATE = _env("ESPREL_LDAP_TLS_VALIDATE", "1") == "1"
CA_CERT_FILE = _env("ESPREL_LDAP_CA_CERT_FILE")
TIMEOUT = int(_env("ESPREL_LDAP_TIMEOUT", "8"))
ALLOWED_IPS = {x.strip() for x in (_env("ESPREL_ALLOWED_IPS", "") or "").split(",") if x.strip()}
MAX_FAILS, WINDOW = 8, 900          # ผิดได้ 8 ครั้ง / 15 นาที ต่อชื่อผู้ใช้
_fails, _lock = {}, threading.Lock()

router = APIRouter(prefix="/api/esprel", tags=["esprel"])


class VerifyIn(BaseModel):
    username: str
    password: str


def _blocked(user):
    now = time.time()
    with _lock:
        hits = [t for t in _fails.get(user, []) if now - t < WINDOW]
        _fails[user] = hits
        return len(hits) >= MAX_FAILS


def _fail(user):
    with _lock:
        _fails.setdefault(user, []).append(time.time())


def _first(entry, attr):
    try:
        v = {k.lower(): x for k, x in entry.entry_raw_attributes.items()}.get(attr.lower())
    except Exception:  # noqa: BLE001
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v) if v else None


def ldap_bind(username, password):
    """True/False + ข้อมูลผู้ใช้ (ถ้ามี BASE_DN) — ยก RuntimeError เมื่อเชื่อมต่อ LDAP ไม่ได้"""
    from ldap3 import SIMPLE, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPBindError, LDAPException
    from ldap3.utils.conv import escape_filter_chars

    use_ssl = LDAP_SERVER.lower().startswith("ldaps://")
    tls = Tls(validate=ssl.CERT_REQUIRED if TLS_VALIDATE else ssl.CERT_NONE, ca_certs_file=CA_CERT_FILE or None)
    server = Server(LDAP_SERVER, use_ssl=use_ssl, tls=tls, connect_timeout=TIMEOUT)
    upn = f"{username}@{LDAP_DOMAIN}"
    try:
        conn = Connection(server, user=upn, password=password, authentication=SIMPLE, receive_timeout=TIMEOUT,
                          check_names=False)
        if not use_ssl and REQUIRE_TLS:
            conn.open()
            conn.start_tls()
        if not conn.bind():
            if not use_ssl and not REQUIRE_TLS and (conn.result or {}).get("result") == 8:   # strongerAuthRequired
                conn = Connection(server, user=upn, password=password, authentication=SIMPLE, receive_timeout=TIMEOUT,
                                  check_names=False)
                conn.open()
                conn.start_tls()
                if not conn.bind():
                    return False, {}
            else:
                return False, {}
        info = {}
        if LDAP_BASE_DN:
            try:
                conn.search(LDAP_BASE_DN, f"(sAMAccountName={escape_filter_chars(username)})",
                            attributes=["displayName", "mail", "department"])
                if conn.entries:
                    e = conn.entries[0]
                    info = {"full_name": _first(e, "displayName"), "email": _first(e, "mail"),
                            "department": _first(e, "department")}
            except LDAPException:
                pass
        conn.unbind()
        return True, info
    except LDAPBindError:
        return False, {}
    except LDAPException as ex:
        raise RuntimeError(str(ex)) from ex


@router.post("/ldap-verify")
def ldap_verify(body: VerifyIn, request: Request, x_esprel_key: str = Header(default="")):
    if not GATEWAY_KEY or not hmac.compare_digest(x_esprel_key.encode(), GATEWAY_KEY.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    if ALLOWED_IPS and (request.client.host if request.client else "") not in ALLOWED_IPS:
        raise HTTPException(status_code=403, detail="forbidden")
    user = (body.username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._\-]{1,64}", user) or not body.password:
        # ปฏิเสธรหัสผ่านว่างเสมอ (กัน anonymous bind ที่ AD ตอบว่าผ่าน)
        raise HTTPException(status_code=401, detail="invalid")
    if _blocked(user):
        raise HTTPException(status_code=429, detail="too many attempts")
    try:
        ok, info = ldap_bind(user, body.password)
    except RuntimeError as ex:
        raise HTTPException(status_code=502, detail="ldap unavailable") from ex
    if not ok:
        _fail(user)
        raise HTTPException(status_code=401, detail="invalid")
    return {"ok": True, "username": user, **info}


@router.get("/ldap-health")
def ldap_health():
    return {"ok": True, "server": LDAP_SERVER.split("//")[-1].split(":")[0], "domain": LDAP_DOMAIN,
            "key_configured": bool(GATEWAY_KEY)}


# รันแยกเป็นบริการเล็ก ๆ: uvicorn esprel_ldap_gateway:app --host 127.0.0.1 --port 8701
app = FastAPI(title="ESPReL LDAP gateway", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)

"""ยืนยันตัวตนผู้ตรวจ/ทีมงานด้วย LDAP ของมหาวิทยาลัย (ขั้นที่ 1) — การตรวจสิทธิ์ในฐานข้อมูลทำต่อใน views/auth.py (ขั้นที่ 2)

สองแบบ:
  A) LDAP_GATEWAY_URL  — ส่งชื่อผู้ใช้/รหัสผ่านผ่าน HTTPS ไปให้ "เซิร์ฟเวอร์ภายใน มน." bind LDAP แทน
                          (ใช้เมื่อแอปอยู่บน Vercel ซึ่งเข้าถึง LDAP ภายในไม่ได้) — โค้ดฝั่งเซิร์ฟเวอร์อยู่ใน ldap_gateway/
  B) LDAP_URL          — แอปอยู่ในเครือข่าย มน. แล้ว bind เอง แบบเดียวกับระบบ NRED (user@nu.local, StartTLS ตามตั้งค่า)
ทั้งสองแบบคืน dict {username, full_name, email, faculty} เมื่อผ่าน, None เมื่อรหัสผ่านผิด, และยก LDAPError เมื่อเชื่อมต่อไม่ได้
"""
import hashlib
import hmac
import json
import re
import secrets
import ssl
import time

import requests

import config


class LDAPError(Exception):
    pass


def enabled():
    return bool(config.LDAP_GATEWAY_URL or config.LDAP_URL)


def mode():
    return "gateway" if config.LDAP_GATEWAY_URL else ("direct" if config.LDAP_URL else None)


NU_SUFFIXES = ("@nu.ac.th", "@nu.local")


def normalize_username(raw):
    """รับได้ทั้ง 'gitsadap', 'GITSADAP@nu.ac.th', 'NU\\gitsadap' → 'gitsadap' (คืน None ถ้าไม่ใช่บัญชี มน.)"""
    u = (raw or "").strip().lower()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    suffixes = NU_SUFFIXES + ((config.LDAP_UPN_SUFFIX.lower(),) if config.LDAP_UPN_SUFFIX else ())
    for suf in suffixes:
        if u.endswith(suf):
            u = u[: -len(suf)]
            break
    if not u or "@" in u or not re.fullmatch(r"[a-z0-9._\-]{1,64}", u):
        return None
    return u


def authenticate(username, password, server=None):
    username = normalize_username(username)
    if not username or not password:  # รหัสผ่านว่าง = anonymous bind ซึ่ง AD บางเครื่องตอบว่า "ผ่าน" — ต้องปฏิเสธเสมอ
        return None
    if config.LDAP_GATEWAY_URL and server is None:
        return _via_gateway(username, password)
    return _direct(username, password, server)


# ----------------------------------------------------------------- A) ผ่านเซิร์ฟเวอร์ภายใน (NRED)
# โปรโตคอลเดียวกับ ldap_gateway/esprel_ldap_gateway.py: คำขอและคำตอบลงลายเซ็น HMAC-SHA256 ด้วยคีย์ร่วม
GATEWAY_PROTOCOL = "v1"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _sign_request(key, ts, nonce, body):
    return hmac.new(key, f"{GATEWAY_PROTOCOL}\n{ts}\n{nonce}\n{_sha(body)}".encode(), hashlib.sha256).hexdigest()


def _sign_response(key, nonce, status, body):
    return hmac.new(key, f"{GATEWAY_PROTOCOL}-resp\n{nonce}\n{status}\n{_sha(body)}".encode(), hashlib.sha256).hexdigest()


def _via_gateway(username, password):
    url = config.LDAP_GATEWAY_URL
    key = (config.LDAP_GATEWAY_KEY or "").encode()
    if len(key) < 32:
        raise LDAPError("ยังไม่ได้ตั้งค่า LDAP_GATEWAY_KEY (ต้องยาวอย่างน้อย 32 ตัวอักษร) — แจ้งผู้ดูแลระบบ")
    if not url.lower().startswith("https://") and not config.LDAP_GATEWAY_ALLOW_HTTP:
        raise LDAPError("LDAP_GATEWAY_URL ต้องเป็น https:// — แจ้งผู้ดูแลระบบ")
    body = json.dumps({"username": username, "password": password}, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(24)
    headers = {"Content-Type": "application/json", "X-ESPReL-Timestamp": ts, "X-ESPReL-Nonce": nonce,
               "X-ESPReL-Signature": _sign_request(key, ts, nonce, body)}
    try:
        r = requests.post(url, data=body, headers=headers, timeout=config.LDAP_TIMEOUT + 4, allow_redirects=False)
    except requests.RequestException as ex:
        raise LDAPError("ติดต่อเซิร์ฟเวอร์ยืนยันตัวตนของมหาวิทยาลัยไม่ได้ — ลองใหม่อีกครั้ง") from ex
    # ยอมรับเฉพาะคำตอบที่ลงลายเซ็นถูกต้องและผูกกับ nonce ของคำขอนี้ (กันเซิร์ฟเวอร์ปลอม/คำตอบปลอม)
    rsig = r.headers.get("X-ESPReL-Signature", "")
    signed = bool(rsig) and hmac.compare_digest(rsig, _sign_response(key, nonce, r.status_code, r.content or b""))
    if r.status_code == 401 and signed:
        return None
    if r.status_code == 429:
        raise LDAPError("ลองเข้าสู่ระบบผิดหลายครั้ง — กรุณารอสักครู่แล้วลองใหม่")
    if r.status_code in (403, 503) and not signed:
        raise LDAPError("เซิร์ฟเวอร์ยืนยันตัวตนปฏิเสธคำขอ (คีย์หรือเวลาเครื่องไม่ตรงกัน) — แจ้งผู้ดูแลระบบ")
    if r.status_code == 502:
        raise LDAPError("เซิร์ฟเวอร์ยืนยันตัวตนติดต่อ LDAP ของมหาวิทยาลัยไม่ได้ — ลองใหม่อีกครั้ง")
    if r.status_code != 200 or not signed:
        raise LDAPError(f"เซิร์ฟเวอร์ยืนยันตัวตนตอบกลับผิดปกติ (HTTP {r.status_code}) — แจ้งผู้ดูแลระบบ")
    try:
        j = json.loads(r.content)
    except ValueError as ex:
        raise LDAPError("เซิร์ฟเวอร์ยืนยันตัวตนตอบกลับผิดรูปแบบ") from ex
    if not j.get("ok") or j.get("username") != username:
        return None
    return {"username": username, "full_name": j.get("full_name") or None, "email": (j.get("email") or "").lower() or None,
            "faculty": j.get("department") or None}


# ----------------------------------------------------------------- B) bind LDAP เอง (แบบ NRED)
def _first(entry, attr):
    if not attr:
        return None
    try:  # ค่า raw (bytes) ถอดรหัส UTF-8 เอง — ใช้ได้แม้ไม่ได้ตรวจ schema (check_names=False)
        d = {k.lower(): v for k, v in entry.entry_raw_attributes.items()}
    except Exception:  # noqa: BLE001
        return None
    v = d.get(attr.lower())
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v) if v else None


def _server():
    from ldap3 import ALL, Server, Tls
    url = config.LDAP_URL
    use_ssl = url.lower().startswith("ldaps://")
    tls = Tls(validate=ssl.CERT_REQUIRED if config.LDAP_TLS_VALIDATE else ssl.CERT_NONE,
              ca_certs_file=config.LDAP_CA_CERT_FILE or None)
    return Server(url, use_ssl=use_ssl, tls=tls, get_info=ALL, connect_timeout=config.LDAP_TIMEOUT)


def _direct(username, password, server=None):
    from ldap3 import SIMPLE, Connection
    from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPSocketOpenError
    from ldap3.utils.conv import escape_filter_chars

    plain = server is None and not config.LDAP_URL.lower().startswith("ldaps://")
    server = server or _server()
    flt = config.LDAP_USER_FILTER.replace("{username}", escape_filter_chars(username))
    attrs = [a for a in (config.LDAP_ATTR_NAME, config.LDAP_ATTR_MAIL, config.LDAP_ATTR_DEPT) if a]
    try:
        entry = None
        if config.LDAP_BIND_DN:   # ค้นหา DN ด้วยบัญชีบริการ แล้ว bind ด้วยรหัสผ่านผู้ใช้
            svc = Connection(server, user=config.LDAP_BIND_DN, password=config.LDAP_BIND_PASSWORD, authentication=SIMPLE,
                             auto_bind=True, receive_timeout=config.LDAP_TIMEOUT, check_names=False)
            svc.search(config.LDAP_BASE_DN, flt, attributes=attrs)
            if not svc.entries:
                return None
            entry = svc.entries[0]
            user_dn = entry.entry_dn
            svc.unbind()
        elif config.LDAP_USER_DN_TEMPLATE:
            user_dn = config.LDAP_USER_DN_TEMPLATE.replace("{username}", username)
        elif config.LDAP_UPN_SUFFIX:  # Active Directory: user@nu.local (แบบระบบ NRED)
            user_dn = username + config.LDAP_UPN_SUFFIX
        else:
            raise LDAPError("ยังไม่ได้ตั้งค่า LDAP_UPN_SUFFIX / LDAP_USER_DN_TEMPLATE / LDAP_BIND_DN")
        conn = Connection(server, user=user_dn, password=password, authentication=SIMPLE,
                          receive_timeout=config.LDAP_TIMEOUT, check_names=False)
        if plain and config.LDAP_REQUIRE_TLS:
            conn.open()
            conn.start_tls()
        if not conn.bind():
            # AD ที่บังคับการเข้ารหัส ตอบ strongerAuthRequired (8) → ลองใหม่ด้วย StartTLS
            if plain and not config.LDAP_REQUIRE_TLS and (conn.result or {}).get("result") == 8:
                conn = Connection(server, user=user_dn, password=password, authentication=SIMPLE,
                                  receive_timeout=config.LDAP_TIMEOUT, check_names=False)
                conn.open()
                conn.start_tls()
                if not conn.bind():
                    return None
            else:
                return None
        if entry is None and config.LDAP_BASE_DN:
            try:
                conn.search(config.LDAP_BASE_DN, flt, attributes=attrs)
                entry = conn.entries[0] if conn.entries else None
            except LDAPException:
                entry = None   # ดึงข้อมูลเพิ่มไม่ได้ ไม่เป็นไร — ยืนยันตัวตนผ่านแล้ว
        info = {"username": username, "full_name": None, "email": None, "faculty": None}
        if entry is not None:
            info["full_name"] = _first(entry, config.LDAP_ATTR_NAME)
            info["email"] = (_first(entry, config.LDAP_ATTR_MAIL) or "").lower() or None
            info["faculty"] = _first(entry, config.LDAP_ATTR_DEPT)
        conn.unbind()
        return info
    except LDAPBindError:
        return None
    except LDAPSocketOpenError as ex:
        raise LDAPError("เชื่อมต่อเซิร์ฟเวอร์ LDAP ของมหาวิทยาลัยไม่ได้ (ต้องอยู่ในเครือข่าย มน. หรือใช้ LDAP gateway)") from ex
    except LDAPException as ex:
        raise LDAPError(f"LDAP ผิดพลาด: {ex}") from ex

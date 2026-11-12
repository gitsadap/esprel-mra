"""ยืนยันตัวตนกับ LDAP ของมหาวิทยาลัย แล้วจับคู่กับตาราง users ใน Supabase"""
import config


class LDAPError(Exception):
    pass


def enabled():
    return bool(config.LDAP_URL)


def _first(entry, attr):
    if not attr:
        return None
    try:  # ค่า raw (bytes) ถอดรหัส UTF-8 เอง — ใช้ได้แม้ไม่ได้ตรวจ schema (check_names=False)
        d = {k.lower(): v for k, v in entry.entry_raw_attributes.items()}
    except Exception:  # noqa
        return None
    v = d.get(attr.lower())
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v) if v else None


def _escape(s):
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(s)


def authenticate(username, password, server=None):
    """คืน dict {username, full_name, email, faculty} ถ้าผ่าน, None ถ้ารหัสผ่านผิด; ยก LDAPError เมื่อเชื่อมต่อไม่ได้
    `server` ใช้สำหรับทดสอบ (ldap3 MOCK)"""
    from ldap3 import ALL, SIMPLE, Connection, Server
    from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPSocketOpenError

    username = (username or "").strip()
    if not username or not password:  # ป้องกัน anonymous bind ด้วยรหัสผ่านว่าง
        return None
    if server is None:
        server = Server(config.LDAP_URL, get_info=ALL, connect_timeout=config.LDAP_TIMEOUT)
    attrs = [a for a in (config.LDAP_ATTR_NAME, config.LDAP_ATTR_MAIL, config.LDAP_ATTR_DEPT) if a]
    try:
        user_dn = None
        entry = None
        if config.LDAP_BIND_DN:
            # ค้นหา DN ด้วยบัญชีบริการ แล้ว bind ด้วยรหัสผ่านผู้ใช้
            svc = Connection(server, user=config.LDAP_BIND_DN, password=config.LDAP_BIND_PASSWORD,
                             authentication=SIMPLE, auto_bind=True, receive_timeout=config.LDAP_TIMEOUT,
                             check_names=False)
            svc.search(config.LDAP_BASE_DN, config.LDAP_USER_FILTER.replace("{username}", _escape(username)),
                       attributes=attrs)
            if not svc.entries:
                return None
            entry = svc.entries[0]
            user_dn = entry.entry_dn
            svc.unbind()
        elif config.LDAP_USER_DN_TEMPLATE:
            user_dn = config.LDAP_USER_DN_TEMPLATE.replace("{username}", username)
        elif config.LDAP_UPN_SUFFIX:
            user_dn = username + config.LDAP_UPN_SUFFIX
        else:
            raise LDAPError("ยังไม่ได้ตั้งค่า LDAP_USER_DN_TEMPLATE / LDAP_UPN_SUFFIX / LDAP_BIND_DN")
        conn = Connection(server, user=user_dn, password=password, authentication=SIMPLE,
                          receive_timeout=config.LDAP_TIMEOUT, check_names=False)
        if not conn.bind():
            return None
        if entry is None and config.LDAP_BASE_DN:
            conn.search(config.LDAP_BASE_DN, config.LDAP_USER_FILTER.replace("{username}", _escape(username)),
                        attributes=attrs)
            entry = conn.entries[0] if conn.entries else None
        info = {"username": username.lower(), "full_name": None, "email": None, "faculty": None}
        if entry is not None:
            info["full_name"] = _first(entry, config.LDAP_ATTR_NAME)
            info["email"] = _first(entry, config.LDAP_ATTR_MAIL)
            info["faculty"] = _first(entry, config.LDAP_ATTR_DEPT)
        conn.unbind()
        return info
    except LDAPBindError:
        return None
    except LDAPSocketOpenError as ex:
        raise LDAPError(f"เชื่อมต่อเซิร์ฟเวอร์ LDAP ไม่ได้: {ex}") from ex
    except LDAPException as ex:
        raise LDAPError(f"LDAP ผิดพลาด: {ex}") from ex

"""เข้าสู่ระบบบัญชี มน.: LDAP (ผ่านเซิร์ฟเวอร์ภายใน) ก่อน → แล้วจึงตรวจผู้ใช้ในฐานข้อมูล"""
import importlib
import os
import sys

import pytest

import auth_ldap
import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


KEY = "k" * 40


def _load_gateway(monkeypatch, key=KEY):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("ESPREL_GATEWAY_KEY", key)
    monkeypatch.setenv("ESPREL_ENV_FILE", "/nonexistent")
    if os.path.join(ROOT, "ldap_gateway") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "ldap_gateway"))
    mod = importlib.reload(importlib.import_module("esprel_ldap_gateway"))
    seen = []

    def fake_bind(user, pw):
        seen.append(user)
        if pw == "nu-pass":
            return True, {"full_name": "LDAP Name", "email": f"{user}@nu.ac.th", "department": "คณะทดสอบ"}
        return False, {}
    monkeypatch.setattr(mod, "ldap_bind", fake_bind)
    return mod, seen


@pytest.fixture
def gateway(monkeypatch):
    """แอป ESPReL ↔ gateway ตัวจริง (FastAPI, LDAP จำลอง: รหัส 'nu-pass' ถูก) ผ่าน HTTPS จำลอง"""
    from fastapi.testclient import TestClient
    mod, seen = _load_gateway(monkeypatch)
    client = TestClient(mod.app, base_url="https://nred.example")
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, allow_redirects=False):
        calls.append({"url": url, "body": data, "headers": headers})
        return client.post("/api/esprel/ldap-verify", content=data, headers=headers)
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "https://nred.example/api/esprel/ldap-verify")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", KEY)
    monkeypatch.setattr(auth_ldap.requests, "post", fake_post)
    return calls


def _login(app, user, pw):
    c = app.test_client()
    r = c.post("/login", data={"username": user, "password": pw})
    return c, r


def test_normalize_username():
    n = auth_ldap.normalize_username
    assert n("GITSADAP") == "gitsadap" and n("gitsadap@nu.ac.th") == "gitsadap" and n("NU\\gitsadap") == "gitsadap"
    assert n("gitsadap@nu.local") == "gitsadap"
    assert n("someone@gmail.com") is None and n("") is None and n("a b") is None


def test_gateway_client(gateway, monkeypatch):
    assert auth_ldap.mode() == "gateway"
    info = auth_ldap.authenticate("Tester1@nu.ac.th", "nu-pass")
    assert info == {"username": "tester1", "full_name": "LDAP Name", "email": "tester1@nu.ac.th", "faculty": "คณะทดสอบ"}
    sent = gateway[-1]
    assert KEY not in str(sent["headers"])                       # คีย์ไม่ถูกส่งไปกับคำขอ
    assert set(sent["headers"]) >= {"X-ESPReL-Timestamp", "X-ESPReL-Nonce", "X-ESPReL-Signature"}
    assert auth_ldap.authenticate("tester1", "wrong") is None
    n = len(gateway)
    assert auth_ldap.authenticate("tester1", "") is None and len(gateway) == n   # รหัสว่างไม่ถูกส่งออกไปเลย
    # คีย์ไม่ตรงกัน → ปฏิเสธ (ไม่ใช่ "รหัสผิด")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", "x" * 40)
    with pytest.raises(auth_ldap.LDAPError):
        auth_ldap.authenticate("tester1", "nu-pass")
    # คีย์สั้น / URL ไม่ใช่ https → ไม่ส่งเลย
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", "short")
    with pytest.raises(auth_ldap.LDAPError):
        auth_ldap.authenticate("tester1", "nu-pass")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", KEY)
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "http://nred.example/api/esprel/ldap-verify")
    with pytest.raises(auth_ldap.LDAPError):
        auth_ldap.authenticate("tester1", "nu-pass")


def test_fake_gateway_response_rejected(monkeypatch):
    """เซิร์ฟเวอร์ปลอม/คำตอบปลอมที่ตอบ ok:true แต่ไม่มีลายเซ็นที่ถูกต้อง → ไม่ยอมให้เข้า"""
    class R:
        status_code = 200
        content = b'{"ok":true,"username":"tester1"}'
        headers = {"X-ESPReL-Signature": "00" * 32}
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "https://evil.example/x")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", KEY)
    monkeypatch.setattr(auth_ldap.requests, "post", lambda *a, **k: R())
    with pytest.raises(auth_ldap.LDAPError):
        auth_ldap.authenticate("tester1", "anything")


def test_gateway_down(monkeypatch, app):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "https://internal.example/x")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", KEY)
    monkeypatch.setattr(auth_ldap.requests, "post", boom)
    _, r = _login(app, "ldapdown", "nu-pass")
    assert r.status_code == 200 and "ติดต่อเซิร์ฟเวอร์ยืนยันตัวตน" in r.get_data(as_text=True)


def test_ldap_first_then_database(app, gateway):
    from db import DB
    d = DB()
    # ผู้ใช้ที่ admin เพิ่มไว้แล้ว (ไม่มีรหัสผ่านในระบบ)
    d.execute("INSERT INTO users(username,full_name,role,auth_source,created_at) VALUES('staff1','staff1','lead','ldap','x')")
    # อยู่ในทะเบียนผู้ตรวจ แต่ยังไม่มีบัญชี
    d.execute("""INSERT INTO assessors(full_name,name_key,faculty,org_type,email,created_at)
                 VALUES('ดร.ทะเบียน ทดสอบ','ทะเบียนทดสอบ','คณะวิทยาศาสตร์','nu','reg1@nu.ac.th','x')""")
    d.commit()
    d.close()

    # 1) มีในตาราง users → เข้าได้ + เติมชื่อ/อีเมลจาก LDAP
    c, r = _login(app, "STAFF1@nu.ac.th", "nu-pass")
    assert r.status_code == 302
    d = DB()
    u = d.execute("SELECT * FROM users WHERE username='staff1'").fetchone()
    assert u["full_name"] == "LDAP Name" and u["email"] == "staff1@nu.ac.th" and u["role"] == "lead"
    assert c.get("/planning").status_code == 200

    # 2) LDAP ผ่าน แต่รหัสผิด → ไม่ได้
    _, r = _login(app, "staff1", "wrong")
    assert r.status_code == 200 and "ไม่ถูกต้อง" in r.get_data(as_text=True)

    # 3) ไม่อยู่ใน users แต่อยู่ในทะเบียนผู้ตรวจ → เปิดบัญชีผู้ตรวจอัตโนมัติ + ผูกทะเบียน
    _, r = _login(app, "reg1", "nu-pass")
    assert r.status_code == 302
    u = d.execute("SELECT * FROM users WHERE username='reg1'").fetchone()
    a = d.execute("SELECT user_id FROM assessors WHERE email='reg1@nu.ac.th'").fetchone()
    assert u["role"] == "assessor" and u["full_name"] == "ดร.ทะเบียน ทดสอบ" and u["faculty"] == "คณะวิทยาศาสตร์"
    assert a["user_id"] == u["id"]

    # 4) LDAP ผ่าน แต่ไม่มีชื่อในฐานข้อมูลเลย → ปฏิเสธ ไม่สร้างบัญชี
    _, r = _login(app, "stranger9", "nu-pass")
    assert "ยังไม่มีชื่อท่านในระบบ" in r.get_data(as_text=True)
    assert not d.execute("SELECT 1 x FROM users WHERE username='stranger9'").fetchone()

    # 5) บัญชีถูกปิด → ไม่ได้ แม้ LDAP ผ่าน
    d.execute("UPDATE users SET active=0 WHERE username='staff1'")
    d.commit()
    _, r = _login(app, "staff1", "nu-pass")
    assert "ปิดการใช้งาน" in r.get_data(as_text=True)
    d.execute("UPDATE users SET active=1 WHERE username='staff1'")
    d.commit()

    # 6b) บัญชีผู้ตรวจแบบอีเมล+รหัสผ่านเดิม (อีเมลถูกแก้เป็น @nu.ac.th) → login NU แล้วเปลี่ยนเป็นบัญชี LDAP
    d.execute("""INSERT INTO users(username,password_hash,full_name,email,role,auth_source,must_change_pw,created_at)
                 VALUES('old.mail@gmail.com','x','ผู้ตรวจ เดิม','visa9@nu.ac.th','assessor','local',1,'x')""")
    d.commit()
    _, r = _login(app, "visa9", "nu-pass")
    assert r.status_code == 302
    u = d.execute("SELECT * FROM users WHERE email='visa9@nu.ac.th'").fetchone()
    assert u["username"] == "visa9" and u["auth_source"] == "ldap" and u["password_hash"] is None
    assert not d.execute("SELECT 1 x FROM users WHERE username='old.mail@gmail.com'").fetchone()

    # 6) บัญชีภายในระบบ (admin) ยังใช้รหัสผ่านเดิมได้ ไม่ต้องผ่าน LDAP
    n = len(gateway)
    _, r = _login(app, "admin", "admin1234")
    assert r.status_code == 302 and len(gateway) == n
    # บัญชีห้องปฏิบัติการ (อีเมล) ไม่ถูกส่งไป LDAP
    _login(app, "someone@example.org", "x")
    assert len(gateway) == n
    d.close()


def test_login_rate_limit(app, gateway, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_FAILS", 3)
    for _ in range(3):
        _login(app, "brute1", "wrong")
    _, r = _login(app, "brute1", "nu-pass")
    assert r.status_code == 429


# ----------------------------------------------------------------- ฝั่ง NRED (gateway)
def _signed(mod, body: dict, key=KEY, ts=None, nonce=None):
    import secrets as _s
    import time as _t
    raw = __import__("json").dumps(body).encode()
    ts = str(ts if ts is not None else int(_t.time()))
    nonce = nonce or _s.token_urlsafe(24)
    return raw, {"X-ESPReL-Timestamp": ts, "X-ESPReL-Nonce": nonce,
                 "X-ESPReL-Signature": mod.sign_request(key.encode(), ts, nonce, raw), "Content-Type": "application/json"}


def test_gateway_server_security(monkeypatch):
    from fastapi.testclient import TestClient
    mod, seen = _load_gateway(monkeypatch)
    https = TestClient(mod.app, base_url="https://nred.example")
    url = "/api/esprel/ldap-verify"
    good = {"username": "gitsadap", "password": "nu-pass"}

    raw, h = _signed(mod, good)
    r = https.post(url, content=raw, headers=h)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["username"] == "gitsadap"
    assert r.headers["X-ESPReL-Signature"] == mod.sign_response(KEY.encode(), h["X-ESPReL-Nonce"], 200, r.content)
    assert r.headers["Cache-Control"] == "no-store"
    # ส่งซ้ำด้วย nonce เดิม (replay) → ปฏิเสธ
    assert https.post(url, content=raw, headers=h).status_code == 403
    # ไม่มีลายเซ็น / คีย์ผิด / เวลาเก่า / แก้ body หลังลงลายเซ็น → 403 และไม่ bind LDAP
    n = len(seen)
    assert https.post(url, json=good).status_code == 403
    raw, h = _signed(mod, good, key="z" * 40)
    assert https.post(url, content=raw, headers=h).status_code == 403
    raw, h = _signed(mod, good, ts=1)
    assert https.post(url, content=raw, headers=h).status_code == 403
    raw, h = _signed(mod, good)
    assert https.post(url, content=raw.replace(b"gitsadap", b"someone"), headers=h).status_code == 403
    assert len(seen) == n
    # HTTP ธรรมดา → ปฏิเสธ
    raw, h = _signed(mod, good)
    assert TestClient(mod.app, base_url="http://nred.example").post(url, content=raw, headers=h).status_code == 400
    # รหัสว่าง / ชื่อแปลก (LDAP injection) → 401 โดยไม่ bind
    for bad in ({"username": "gitsadap", "password": ""}, {"username": "x)(cn=*", "password": "p"}):
        raw, h = _signed(mod, bad)
        assert https.post(url, content=raw, headers=h).status_code == 401
    assert len(seen) == n
    # เดารหัส 8 ครั้ง → ล็อกชื่อผู้ใช้นั้น
    for _ in range(8):
        raw, h = _signed(mod, {"username": "victim", "password": "bad"})
        assert https.post(url, content=raw, headers=h).status_code == 401
    raw, h = _signed(mod, {"username": "victim", "password": "nu-pass"})
    assert https.post(url, content=raw, headers=h).status_code == 429
    # health ไม่เปิดเผยค่าภายใน
    hj = https.get("/api/esprel/ldap-health").json()
    assert hj == {"ok": True, "protocol": "v1"}


def test_gateway_refuses_short_key(monkeypatch):
    from fastapi.testclient import TestClient
    mod, _ = _load_gateway(monkeypatch, key="short-key")
    raw, h = _signed(mod, {"username": "a", "password": "b"}, key="short-key")
    r = TestClient(mod.app, base_url="https://nred.example").post("/api/esprel/ldap-verify", content=raw, headers=h)
    assert r.status_code == 503


def test_install_into_nred(tmp_path, monkeypatch):
    """ติดตั้งเข้าโครงสร้างแบบ NRED (main.py + app/routers) ได้ และรันซ้ำไม่ซ้ำซ้อน"""
    (tmp_path / "app" / "routers").mkdir(parents=True)
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\nfrom app.routers import auth\nfrom app.routers import chat\n\n"
        "app = FastAPI(\n    title='NRED',\n)\napp.include_router(auth.router)\napp.include_router(chat.router)\n\n"
        "@app.get('/')\ndef root():\n    return {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_KEY=x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, os.path.join(ROOT, "ldap_gateway"))
    inst = importlib.import_module("install_into_nred")
    inst.main()
    main_py = (tmp_path / "main.py").read_text(encoding="utf-8")
    lines = main_py.split("\n")
    assert lines.index(inst.IMPORT_LINE) == 3 and lines.index(inst.INCLUDE_LINE) == 10
    compile(main_py, "main.py", "exec")
    assert (tmp_path / "app" / "routers" / "esprel_gateway.py").exists()
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    key = [x for x in env.split("\n") if x.startswith("ESPREL_GATEWAY_KEY=")][0].split("=", 1)[1]
    assert len(key) >= 32
    inst.main()   # รันซ้ำ
    assert (tmp_path / "main.py").read_text(encoding="utf-8").count(inst.INCLUDE_LINE) == 1
    assert (tmp_path / ".env").read_text(encoding="utf-8").count("ESPREL_GATEWAY_KEY=") == 1

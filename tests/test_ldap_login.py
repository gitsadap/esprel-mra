"""เข้าสู่ระบบบัญชี มน.: LDAP (ผ่านเซิร์ฟเวอร์ภายใน) ก่อน → แล้วจึงตรวจผู้ใช้ในฐานข้อมูล"""
import importlib
import os
import sys

import pytest

import auth_ldap
import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeResp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


@pytest.fixture
def gateway(monkeypatch):
    """จำลองเซิร์ฟเวอร์ภายใน: รหัสผ่าน 'nu-pass' ถูกเสมอ"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if headers.get("X-ESPReL-Key") != "k3y":
            return FakeResp(403, {"detail": "forbidden"})
        if json["password"] == "nu-pass":
            return FakeResp(200, {"ok": True, "username": json["username"], "full_name": "LDAP Name",
                                  "email": f"{json['username']}@nu.ac.th", "department": "คณะทดสอบ"})
        return FakeResp(401, {"detail": "invalid"})
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "https://internal.example/api/esprel/ldap-verify")
    monkeypatch.setattr(config, "LDAP_GATEWAY_KEY", "k3y")
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


def test_gateway_client(gateway):
    assert auth_ldap.mode() == "gateway"
    info = auth_ldap.authenticate("Tester1@nu.ac.th", "nu-pass")
    assert info == {"username": "tester1", "full_name": "LDAP Name", "email": "tester1@nu.ac.th", "faculty": "คณะทดสอบ"}
    assert gateway[-1]["json"] == {"username": "tester1", "password": "nu-pass"}
    assert auth_ldap.authenticate("tester1", "wrong") is None
    n = len(gateway)
    assert auth_ldap.authenticate("tester1", "") is None and len(gateway) == n   # รหัสว่างไม่ถูกส่งออกไปเลย
    config.LDAP_GATEWAY_KEY = "bad"
    with pytest.raises(auth_ldap.LDAPError):
        auth_ldap.authenticate("tester1", "nu-pass")


def test_gateway_down(monkeypatch, app):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(config, "LDAP_GATEWAY_URL", "https://internal.example/x")
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


# ----------------------------------------------------------------- ฝั่งเซิร์ฟเวอร์ภายใน (gateway)
@pytest.fixture
def gw(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("ESPREL_GATEWAY_KEY", "secret-key")
    sys.path.insert(0, os.path.join(ROOT, "ldap_gateway"))
    mod = importlib.reload(importlib.import_module("esprel_ldap_gateway"))
    seen = []

    def fake_bind(user, pw):
        seen.append(user)
        return (pw == "nu-pass"), ({"full_name": "จาก AD", "email": f"{user}@nu.ac.th"} if pw == "nu-pass" else {})
    monkeypatch.setattr(mod, "ldap_bind", fake_bind)
    return TestClient(mod.app), seen


def test_gateway_server(gw):
    client, seen = gw
    url = "/api/esprel/ldap-verify"
    assert client.post(url, json={"username": "a", "password": "nu-pass"}).status_code == 403           # ไม่มีคีย์
    h = {"X-ESPReL-Key": "secret-key"}
    r = client.post(url, json={"username": "Gitsadap", "password": "nu-pass"}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["username"] == "gitsadap" and seen == ["gitsadap"]
    assert client.post(url, json={"username": "gitsadap", "password": ""}, headers=h).status_code == 401   # รหัสว่าง
    assert len(seen) == 1                                                                                  # ไม่ bind เลย
    assert client.post(url, json={"username": "x)(cn=*", "password": "p"}, headers=h).status_code == 401  # ชื่อแปลก
    for _ in range(8):
        assert client.post(url, json={"username": "victim", "password": "bad"}, headers=h).status_code == 401
    assert client.post(url, json={"username": "victim", "password": "nu-pass"}, headers=h).status_code == 429
    assert client.get("/api/esprel/ldap-health").json()["key_configured"] is True

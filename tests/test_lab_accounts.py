"""สมัครโดยไม่ต้องเข้าสู่ระบบ → บัญชีห้องปฏิบัติการ (อีเมล + รหัสผ่านที่ระบบสร้าง) → เห็นเฉพาะห้องตัวเอง"""
import io
import re

import fm001
import mailer
from conftest import login

FAC = "คณะวิทยาศาสตร์"


def _pdf(name="survey.pdf"):
    return (io.BytesIO(b"%PDF-1.4 test"), name, "application/pdf")


def _apply(client, name, email, year="2573", **over):
    d = fm001.example_form(name, FAC, year_be=year, coord_email=email, coord_name="ผู้ประสาน " + name, **over)
    d.pop("action", None)
    d["action"] = "submit"
    d["file_survey"] = _pdf()
    return client.post("/apply", data=d, content_type="multipart/form-data")


def _creds(email):
    msg = [m for m in mailer.OUTBOX if m["to"] == email and m["kind"] == "credentials"][-1]
    user = re.search(r"ชื่อผู้ใช้ : (\S+)", msg["body"]).group(1)
    pw = re.search(r"รหัสผ่านชั่วคราว : (\S+)", msg["body"]).group(1)
    return user, pw


def _first_login(app, email, pw, new="NewPass!234"):
    c = app.test_client()
    r = c.post("/login", data={"username": email.upper(), "password": pw})   # ไม่สนตัวพิมพ์
    assert r.status_code == 302
    # ต้องตั้งรหัสผ่านใหม่ก่อนใช้งาน
    r = c.get("/lab")
    assert r.status_code == 302 and "/account" in r.headers["Location"]
    assert c.post("/account", data={"old": pw, "new": new}).status_code == 302
    assert c.get("/lab").status_code == 200
    return c


def test_public_apply_creates_account_and_emails(app):
    anon = app.test_client()
    page = anon.get("/apply").get_data(as_text=True)
    assert "สมัครได้ทันที ไม่ต้องมีบัญชี" in page and "บันทึกร่าง" not in page

    # กรอกไม่ครบ → 400 ไม่สร้างอะไร
    r = anon.post("/apply", data={"name": "ห้องไม่ครบ", "coord_email": "x@example.org", "action": "submit"})
    assert r.status_code == 400 and "ยังส่งไม่ได้" in r.get_data(as_text=True)
    from db import DB
    d = DB()
    assert not d.execute("SELECT 1 x FROM labs WHERE name='ห้องไม่ครบ'").fetchone()
    assert not d.execute("SELECT 1 x FROM users WHERE username='x@example.org'").fetchone()
    d.close()

    # honeypot
    d0 = fm001.example_form("ห้องบอท", FAC, coord_email="bot@example.org", website="http://spam")
    d0["file_survey"] = _pdf()
    assert anon.post("/apply", data=d0, content_type="multipart/form-data").status_code == 400

    mailer.OUTBOX.clear()
    r = _apply(anon, "ห้องปฏิบัติการ A 101", "Lab.A@Example.org")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "ส่งใบสมัครเรียบร้อย" in html and re.search(r"เลขที่ใบสมัคร \d{3}/2573", html)
    user, pw = _creds("lab.a@example.org")
    assert user == "lab.a@example.org" and len(pw) == 10
    assert pw in html            # โหมดทดสอบ (ไม่มี SMTP) แสดงบนหน้าจอ

    # ส่งซ้ำ (กดรีเฟรช) → ไม่สร้างซ้ำ
    r = _apply(anon, "ห้องปฏิบัติการ A 101", "lab.a@example.org")
    assert "มีใบสมัครนี้อยู่แล้ว" in r.get_data(as_text=True)

    # รหัสผิด → เข้าไม่ได้
    assert app.test_client().post("/login", data={"username": user, "password": "wrong"}).status_code == 200

    c = _first_login(app, user, pw)
    home = c.get("/lab").get_data(as_text=True)
    assert "ห้องปฏิบัติการ A 101" in home
    # รหัสชั่วคราวใช้ไม่ได้อีก
    assert app.test_client().post("/login", data={"username": user, "password": pw}).status_code == 200


def test_lab_sees_only_own_lab(app):
    anon = app.test_client()
    _apply(anon, "ห้อง B 201", "b@example.org")
    _apply(anon, "ห้อง C 301", "c@example.org")
    cb = _first_login(app, "b@example.org", _creds("b@example.org")[1])
    cc = _first_login(app, "c@example.org", _creds("c@example.org")[1])
    hb = cb.get("/lab").get_data(as_text=True)
    assert "ห้อง B 201" in hb and "ห้อง C 301" not in hb and "ห้องปฏิบัติการ A 101" not in hb

    from db import DB
    d = DB()
    app_c = d.execute("SELECT ap.id, ap.lab_id FROM applications ap JOIN labs l ON l.id=ap.lab_id WHERE l.name='ห้อง C 301'").fetchone()
    f_c = d.execute("SELECT id FROM app_files WHERE application_id=?", (app_c["id"],)).fetchone()
    d.close()
    assert cb.get(f"/lab/apply/{app_c['id']}").status_code == 403
    assert cb.get(f"/application/{app_c['id']}/print").status_code == 403
    assert cb.get(f"/application/file/{f_c['id']}").status_code == 403
    assert cb.get(f"/lab/apply?lab_id={app_c['lab_id']}").status_code == 403
    assert cb.post(f"/application/{app_c['id']}/signed", data={"file_signed": _pdf()},
                   content_type="multipart/form-data").status_code == 403
    assert cc.get(f"/application/{app_c['id']}/print").status_code == 200

    # ผลการตรวจ/รายงาน/CAPA ของห้องอื่น (การตรวจ AG ของ labuser จากชุดทดสอบหลัก)
    d = DB()
    other = d.execute("SELECT id FROM audits ORDER BY id LIMIT 1").fetchone()
    rep = d.execute("SELECT id FROM reports ORDER BY id LIMIT 1").fetchone()
    att = d.execute("SELECT id FROM attachments ORDER BY id LIMIT 1").fetchone()
    d.close()
    assert other and rep and att        # มาจาก test_flow (รันก่อนไฟล์นี้)
    if other:
        aid = other["id"]
        assert cb.get(f"/lab/audit/{aid}").status_code == 403
        assert cb.get(f"/audit/{aid}/round/1/report/view").status_code == 403
        assert cb.post(f"/api/lab/audit/{aid}/capa/1-1", json={"plan": "x"}).status_code in (403, 404)
        assert cb.get(f"/audit/{aid}").status_code in (302, 403)
    if rep:
        assert cb.get(f"/report/{rep['id']}/download").status_code == 403
    if att:
        assert cb.get(f"/file/{att['id']}").status_code == 403
    # หน้าสำหรับเจ้าหน้าที่
    for u in ("/", "/planning", "/users", "/settings"):
        r = cb.get(u)
        assert r.status_code in (302, 403) and r.headers.get("Location", "/lab") in ("/lab", "/home")


def test_same_email_second_lab_attaches_to_account(app):
    anon = app.test_client()
    mailer.OUTBOX.clear()
    _apply(anon, "ห้อง D 401", "d@example.org")
    _apply(anon, "ห้อง D 402", "d@example.org")
    kinds = [m["kind"] for m in mailer.OUTBOX if m["to"] == "d@example.org"]
    assert kinds == ["credentials", "apply"]      # ห้องที่ 2 ไม่สร้างรหัสใหม่
    c = _first_login(app, "d@example.org", _creds("d@example.org")[1])
    h = c.get("/lab").get_data(as_text=True)
    assert "ห้อง D 401" in h and "ห้อง D 402" in h


def test_forgot_and_admin_reset(app):
    anon = app.test_client()
    _apply(anon, "ห้อง E 501", "e@example.org")
    _first_login(app, "e@example.org", _creds("e@example.org")[1], new="Second#Pass1")
    mailer.OUTBOX.clear()
    r = anon.post("/forgot", data={"email": "e@example.org"})
    assert r.status_code == 302
    link = re.search(r"(/reset/\S+)", mailer.OUTBOX[-1]["body"]).group(1)
    # อีเมลที่ไม่มีในระบบ → ตอบเหมือนกัน ไม่ส่งอีเมล
    n = len(mailer.OUTBOX)
    anon.post("/forgot", data={"email": "nobody@example.org"})
    assert len(mailer.OUTBOX) == n
    assert anon.post(link, data={"new": "short", "confirm": "short"}).status_code == 200
    assert anon.post(link, data={"new": "Third#Pass12", "confirm": "Third#Pass12"}).status_code == 302
    assert app.test_client().post("/login", data={"username": "e@example.org", "password": "Third#Pass12"}).status_code == 302
    # ลิงก์ใช้ได้ครั้งเดียว
    r = anon.get(link)
    assert r.status_code == 302 and "/forgot" in r.headers["Location"]

    # admin ส่งรหัสผ่านใหม่
    admin = login(app.test_client(), "admin", "admin1234")
    from db import DB
    d = DB()
    uid = d.execute("SELECT id FROM users WHERE username='e@example.org'").fetchone()["id"]
    d.close()
    mailer.OUTBOX.clear()
    admin.post("/users", data={"action": "resetpw", "uid": str(uid)})
    _, pw = _creds("e@example.org")
    _first_login(app, "e@example.org", pw, new="Fourth#Pass1")


def test_ldap_unknown_user_rejected(app, monkeypatch):
    import auth_ldap
    import config
    monkeypatch.setattr(auth_ldap, "enabled", lambda: True)
    monkeypatch.setattr(auth_ldap, "authenticate", lambda u, p: {"username": u, "full_name": "Stranger", "email": None,
                                                                  "faculty": None})
    monkeypatch.setattr(config, "LDAP_NEW_USER_ROLE", "")
    c = app.test_client()
    r = c.post("/login", data={"username": "stranger", "password": "x"})
    assert r.status_code == 200 and "ยังไม่ได้รับสิทธิ์" in r.get_data(as_text=True)
    from db import DB
    d = DB()
    assert not d.execute("SELECT 1 x FROM users WHERE username='stranger'").fetchone()
    d.close()


def test_lead_attaches_lab_account_by_email(app):
    lead = login(app.test_client(), "lead", "lead1234")
    from db import DB
    d = DB()
    a = d.execute("SELECT a.*, l.name lab_name, l.espre_no, l.faculty, l.affiliation FROM audits a JOIN labs l ON l.id=a.lab_id ORDER BY a.id LIMIT 1").fetchone()
    d.close()
    mailer.OUTBOX.clear()
    r = lead.post(f"/audit/{a['id']}/edit", data={"lab_name": a["lab_name"], "espre_no": a["espre_no"] or "",
                                                  "faculty": a["faculty"] or "", "year_be": str(a["year_be"]),
                                                  "lead_user_id": str(a["lead_user_id"]), "lab_username": "Head.F@Example.org"})
    assert r.status_code == 302
    _, pw = _creds("head.f@example.org")
    c = _first_login(app, "head.f@example.org", pw)
    assert a["lab_name"] in c.get("/lab").get_data(as_text=True)


def test_reject_with_reason_and_resubmit(app):
    anon = app.test_client()
    mailer.OUTBOX.clear()
    _apply(anon, "ห้อง G 701", "g@example.org")
    c = _first_login(app, "g@example.org", _creds("g@example.org")[1])
    from db import DB
    d = DB()
    ap = d.execute("SELECT ap.* FROM applications ap JOIN labs l ON l.id=ap.lab_id WHERE l.name='ห้อง G 701'").fetchone()
    d.close()
    lead = login(app.test_client(), "lead", "lead1234")
    # ไม่มีเหตุผล → ไม่เปลี่ยนสถานะ
    lead.post(f"/application/{ap['id']}/reject", data={"reason": ""})
    assert _status("ห้อง G 701") == "pending"
    # ห้องปฏิบัติการไม่มีสิทธิ์กดไม่อนุมัติ
    assert c.post(f"/application/{ap['id']}/reject", data={"reason": "ทดสอบสิทธิ์"}).status_code in (302, 403)
    assert _status("ห้อง G 701") == "pending"
    reason = "ใบสมัครที่แนบยังไม่มีลายมือชื่อผู้มีอำนาจลงนาม\nเลขทะเบียน ESPReL ไม่ตรงกับระบบ วช."
    mailer.OUTBOX.clear()
    lead.post(f"/application/{ap['id']}/reject", data={"reason": reason})
    assert _status("ห้อง G 701") == "rejected"
    msg = [m for m in mailer.OUTBOX if m["to"] == "g@example.org" and m["kind"] == "review"][-1]
    assert "ยังไม่มีลายมือชื่อ" in msg["body"] and f"/lab/apply/{ap['id']}" in msg["body"]
    # ห้องเห็นเหตุผลในหน้าแรกและในใบสมัคร
    assert "ยังไม่มีลายมือชื่อ" in c.get("/lab").get_data(as_text=True)
    page = c.get(f"/lab/apply/{ap['id']}").get_data(as_text=True)
    assert "ยังไม่ได้รับการอนุมัติ" in page and "ส่งใบสมัครอีกครั้ง" in page
    # หน้าจัดทีมแสดงเหตุผล
    assert "ยังไม่มีลายมือชื่อ" in lead.get("/planning?year=2573").get_data(as_text=True)
    # แก้ไขแล้วส่งใหม่ → pending
    data = fm001.example_form("ห้อง G 701", FAC, "2-0100-1234-0", year_be="2573", coord_email="g@example.org",
                              lab_id=str(ap["lab_id"]))
    r = c.post(f"/lab/apply/{ap['id']}", data=data)
    assert r.status_code == 302 and _status("ห้อง G 701") == "pending"
    assert "ส่งใหม่หลังแก้ไข" in lead.get("/planning?year=2573").get_data(as_text=True)
    # อนุมัติ → อีเมลแจ้งห้อง
    mailer.OUTBOX.clear()
    lead.post("/planning", data={"year": "2573", "action": "approve", "app_id": str(ap["id"])})
    assert _status("ห้อง G 701") == "approved"
    assert any(m["to"] == "g@example.org" and "ได้รับการอนุมัติ" in m["subject"] for m in mailer.OUTBOX)


def _status(name):
    from db import DB
    d = DB()
    r = d.execute("SELECT ap.status FROM applications ap JOIN labs l ON l.id=ap.lab_id WHERE l.name=?", (name,)).fetchone()
    d.close()
    return r["status"]

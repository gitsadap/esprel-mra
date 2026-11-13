"""ทะเบียนผู้ตรวจ: นำเข้า Excel รายชื่อผู้อบรม (ข้อมูลสมมติ) → รวมรายชื่อซ้ำ → เปิดสิทธิ์เข้าระบบ"""
import io
import re

import openpyxl

import assessors
import mailer
from conftest import login


def _xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1 (2)"
    ws.append(["รายชื่อผู้อบรมผู้ตรวจประเมิน NU"])
    ws.append(["ลำดับที่", "รายชื่อ", "สังกัด", "อีเมล์", "เบอร์โทรศัพท์", "ESPReL", "Peer"])
    ws.append([1, "ผู้ช่วยศาสตราจารย์ ดร.ทดสอบ  หนึ่ง   ", "คณะวิทยาศาสตร์", "Test1@nu.ac.th ", "081-2345678", "/", "/"])
    ws.append([2, "นางสาวทดสอบ สอง", "คณะเภสัชศาสตร์ มหาวิทยาลัยนเรศวร", "test2@gmail.com", "0812345679", "/", None])
    ws.append([3, "นายทดสอบ สาม", "กองการวิจัยและนวัตกรรม", "test3@nu.ac.th", "081-111 2222", "/", "/"])
    ws2 = wb.create_sheet("Sheet1")
    ws2.append(["รายชื่อผู้อบรมผู้ตรวจประเมิน Peer ปี2569"])
    ws2.append(["ลำดับที่", "รายชื่อ", "สังกัด", "เบอร์โทรศัพท์", "อีเมล์", "หมายเหตุ"])
    ws2.append([1, "ผศ.ดร.ทดสอบ หนึ่ง", "คณะวิทยาศาสตร์ มหาวิทยาลัยนเรศวร", "081-2345678", "test1@nu.ac.th", None])
    ws2.append([2, "นางสาวภายนอก ทดสอบ", "ศูนย์วิทยาศาสตร์การแพทย์ที่ 2 พิษณุโลก", "091-0000000", "ext.a@dmsc.mail.go.th", None])
    ws2.append([3, "ผศ.ราชภัฏ ทดสอบ", "คณะวิทยาศาสตร์และเทคโนโลยี มหาวิทยาลัยราชภัฎกำแพงเพชร", "080-0000000", "ext.b@gmail.com", None])
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    return b


def test_parse_merge_and_classify():
    people, warns = assessors.parse_workbook(_xlsx(), "รายชื่อผู้อบรมผู้ตรวจประเมิน Peer 2569.xlsx")
    assert len(people) == 5                                   # คนที่ 1 อยู่ทั้ง 2 ชีต → รวมเป็นคนเดียว
    p1 = people[0]
    assert p1["full_name"] == "ผู้ช่วยศาสตราจารย์ ดร.ทดสอบ หนึ่ง" and p1["email"] == "test1@nu.ac.th"
    assert p1["faculty"] == "คณะวิทยาศาสตร์" and p1["affiliation"].endswith("มหาวิทยาลัยนเรศวร")
    assert p1["trainings"] == {("ESPReL", 2569), ("Peer", 2569)} and p1["phone"] == "081-234-5678"
    p2 = people[1]
    assert p2["trainings"] == {("ESPReL", 2569)} and p2["org_type"] == "nu"
    ext = {p["full_name"]: p for p in people if p["org_type"] == "external"}
    assert set(ext) == {"นางสาวภายนอก ทดสอบ", "ผศ.ราชภัฏ ทดสอบ"}
    assert all(p["trainings"] == {("Peer", 2569)} for p in ext.values())
    assert any("อีเมลภายนอก" in w and "ทดสอบ สอง" in w for w in warns)
    assert assessors.name_key("ผศ.ดร.ทดสอบ หนึ่ง") == assessors.name_key("ผู้ช่วยศาสตราจารย์ ดร.ทดสอบ  หนึ่ง")
    assert assessors.ldap_username("Test1@nu.ac.th") == "test1" and assessors.ldap_username("a@gmail.com") is None


def test_import_page_accounts_and_planner(app):
    lead = login(app.test_client(), "lead", "lead1234")
    lab = login(app.test_client(), "labuser", "lab1234")
    assert lab.get("/assessors").status_code in (302, 403)          # ข้อมูลส่วนบุคคล: เฉพาะหัวหน้าผู้ตรวจ/admin
    r = lead.post("/assessors", data={"action": "import", "year": "2569",
                                      "file": (_xlsx(), "รายชื่อผู้อบรมผู้ตรวจประเมิน Peer 2569.xlsx")},
                  content_type="multipart/form-data")
    assert r.status_code == 302
    page = lead.get("/assessors").get_data(as_text=True)
    assert "ผู้ช่วยศาสตราจารย์ ดร.ทดสอบ หนึ่ง" in page and "ภายนอก" in page
    from db import DB
    d = DB()
    assert d.execute("SELECT COUNT(*) n FROM assessors").fetchone()["n"] == 5
    assert d.execute("SELECT COUNT(*) n FROM assessor_trainings").fetchone()["n"] == 7
    # นำเข้าซ้ำ → ไม่เพิ่ม
    lead.post("/assessors", data={"action": "import", "file": (_xlsx(), "x 2569.xlsx")}, content_type="multipart/form-data")
    assert d.execute("SELECT COUNT(*) n FROM assessors").fetchone()["n"] == 5
    assert d.execute("SELECT COUNT(*) n FROM assessor_trainings").fetchone()["n"] == 7
    ids = {r["email"]: r["id"] for r in d.execute("SELECT id, email FROM assessors").fetchall()}
    d.close()
    # ตัวกรอง
    assert "นายทดสอบ สาม" not in lead.get("/assessors?f=esprel_only").get_data(as_text=True)
    assert "นางสาวทดสอบ สอง" in lead.get("/assessors?f=esprel_only").get_data(as_text=True)

    # เปิดสิทธิ์: มน. → LDAP username, ภายนอก → อีเมล + รหัสผ่าน
    mailer.OUTBOX.clear()
    lead.post("/assessors", data={"action": "accounts", "aid": [str(ids["test1@nu.ac.th"]), str(ids["ext.a@dmsc.mail.go.th"])]})
    d = DB()
    u1 = d.execute("SELECT * FROM users WHERE username='test1'").fetchone()
    u2 = d.execute("SELECT * FROM users WHERE username='ext.a@dmsc.mail.go.th'").fetchone()
    d.close()
    assert u1 and u1["role"] == "assessor" and u1["auth_source"] == "ldap" and u1["faculty"] == "คณะวิทยาศาสตร์"
    assert u2 and u2["auth_source"] == "local" and u2["must_change_pw"] == 1
    msg = [m for m in mailer.OUTBOX if m["to"] == "ext.a@dmsc.mail.go.th"][-1]
    assert "ผู้ตรวจประเมิน" in msg["body"] and "FM-001" not in msg["body"]
    pw = re.search(r"รหัสผ่านชั่วคราว : (\S+)", msg["body"]).group(1)
    c = app.test_client()
    assert c.post("/login", data={"username": "ext.a@dmsc.mail.go.th", "password": pw}).status_code == 302
    # ซ้ำ → ไม่สร้างบัญชีใหม่
    lead.post("/assessors", data={"action": "accounts", "aid": [str(ids["test1@nu.ac.th"])]})
    d = DB()
    assert d.execute("SELECT COUNT(*) n FROM users WHERE username='test1'").fetchone()["n"] == 1
    d.close()

    # หน้าแก้ไข: แก้อีเมล เพิ่ม/ลบการอบรม
    aid = ids["test2@gmail.com"]
    r = lead.post(f"/assessors/{aid}", data={"action": "save", "full_name": "นางสาวทดสอบ สอง", "affiliation": "คณะเภสัชศาสตร์",
                                             "email": "test2@nu.ac.th", "phone": "0812345679", "active": "1",
                                             "new_course": "Peer", "new_year": "2570"})
    assert r.status_code == 302
    page = lead.get(f"/assessors/{aid}").get_data(as_text=True)
    assert "test2@nu.ac.th" in page and "Peer" in page and "2570" in page

    # หน้าจัดทีมแสดงสถานะการอบรม
    plan = lead.get("/planning").get_data(as_text=True)
    assert "ทะเบียนผู้ตรวจประเมิน" in plan

    # ลบออกจากทะเบียน
    lead.post(f"/assessors/{ids['ext.b@gmail.com']}", data={"action": "delete"})
    d = DB()
    assert not d.execute("SELECT 1 x FROM assessors WHERE email='ext.b@gmail.com'").fetchone()
    d.close()

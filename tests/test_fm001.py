"""ใบสมัคร FM-001: บันทึกร่าง → ตรวจความครบถ้วน → ส่ง → เลขที่ใบสมัครอัตโนมัติ → พิมพ์ → แนบฉบับลงนาม → อนุมัติแล้วล็อก"""
import io
import re

from conftest import login

import fm001

FAC = "คณะวิทยาศาสตร์"


def _pdf(name="ผลสำรวจ ESPReL.pdf"):
    return (io.BytesIO(b"%PDF-1.4 test"), name, "application/pdf")


def _app_row(name):
    from db import DB
    d = DB()
    r = d.execute("""SELECT ap.* FROM applications ap JOIN labs l ON l.id=ap.lab_id WHERE l.name=?""", (name,)).fetchone()
    d.close()
    return r


def test_validate_lists_missing_fields():
    d = fm001.parse({"name": "x"})
    errs = fm001.validate(d, 0)
    joined = " ".join(errs)
    for frag in ("3. คณะ", "8. การดำเนินงาน", "11. การรับรอง", "12. คะแนน", "คำรับรอง", "ไฟล์แนบผลการสำรวจ"):
        assert frag in joined
    full = fm001.parse(fm001.example_form("ห้อง A", FAC))
    assert fm001.validate(full, 1) == []
    assert full["scores"]["total"]["score"] == "247"          # รวมคะแนนอัตโนมัติ
    assert full["activities"] == ["research", "teaching"]
    assert full["commits"] == [1, 2, 3, 4]


def test_draft_submit_print_sign_and_lock(app):
    lab = login(app.test_client(), "labuser", "lab1234")
    lead = login(app.test_client(), "lead", "lead1234")
    name = "ห้องปฏิบัติการเคมี SC 1101"

    # 1) บันทึกร่าง (กรอกไม่ครบได้)
    r = lab.post("/lab/apply", data={"name": name, "faculty": FAC, "year_be": "2571", "action": "save"})
    assert r.status_code == 302
    ap = _app_row(name)
    assert ap["status"] == "draft" and ap["app_no"] is None
    page = lab.get(f"/lab/apply/{ap['id']}").get_data(as_text=True)
    assert name in page and "บันทึกร่าง" in page

    # ร่างไม่แสดงในหน้าจัดทีม
    assert name not in lead.get("/planning?year=2571").get_data(as_text=True)

    # 2) ส่งโดยยังไม่แนบผลสำรวจ → ยังเป็นร่าง
    data = fm001.example_form(name, FAC, "2-0100-5555-0", year_be="2571", lab_id=str(ap["lab_id"]))
    lab.post(f"/lab/apply/{ap['id']}", data=data)
    assert _app_row(name)["status"] == "draft"

    # 3) แนบไฟล์แล้วส่ง → pending + เลขที่ใบสมัครอัตโนมัติ
    data["file_survey"] = _pdf()
    r = lab.post(f"/lab/apply/{ap['id']}", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    ap = _app_row(name)
    assert ap["status"] == "pending" and ap["submitted_at"]
    assert re.fullmatch(r"\d{3}/2571", ap["app_no"])

    # ชื่อไฟล์ภาษาไทยคงอยู่
    page = lab.get(f"/lab/apply/{ap['id']}").get_data(as_text=True)
    assert "ผลสำรวจ ESPReL.pdf" in page

    # 4) หน้าพิมพ์ FM-001
    pr = lab.get(f"/application/{ap['id']}/print").get_data(as_text=True)
    for frag in (name, "2-0100-5555-0", ap["app_no"], "คำรับรอง", "ผู้มีอำนาจลงนาม", "logo_nrct", "qr_register",
                 "FM-001 Rev.08", "หน้าที่ 2/2", "วิภาดา"):
        assert frag in pr, frag
    assert pr.count("✓") >= 4 + 2 + 1        # คำรับรอง 4 + กิจกรรม 2 + ครั้งแรก/ไม่เคยรับรอง

    # 5) แนบฉบับลงนาม
    r = lab.post(f"/application/{ap['id']}/signed", data={"file_signed": _pdf("signed.pdf")},
                 content_type="multipart/form-data")
    assert r.status_code == 302
    plan = lead.get("/planning?year=2571").get_data(as_text=True)
    assert name in plan and "✓ ฉบับลงนาม" in plan

    # 6) หัวหน้าผู้ตรวจแก้เลขที่ได้
    lead.post(f"/application/{ap['id']}/number", data={"app_no": "NU-01/2571"})
    assert _app_row(name)["app_no"] == "NU-01/2571"

    # 7) อนุมัติแล้ว → ห้องแก้ไขไม่ได้
    lead.post("/planning", data={"year": "2571", "action": "approve", "app_id": str(ap["id"])})
    assert _app_row(name)["status"] == "approved"
    page = lab.get(f"/lab/apply/{ap['id']}").get_data(as_text=True)
    assert "ดูข้อมูลได้อย่างเดียว" in page
    lab.post(f"/lab/apply/{ap['id']}", data=dict(data, name="เปลี่ยนชื่อ", file_survey=_pdf()),
             content_type="multipart/form-data")
    assert _app_row(name) is not None


def test_other_users_cannot_open_application(app):
    lab = login(app.test_client(), "labuser", "lab1234")
    data = fm001.example_form("ห้องลับ X 9", FAC, year_be="2572", action="save")
    lab.post("/lab/apply", data=data)
    ap = _app_row("ห้องลับ X 9")
    # ผู้ใช้ห้องปฏิบัติการอีกคน (สร้างผ่านผู้ดูแล)
    admin = login(app.test_client(), "admin", "admin1234")
    admin.post("/users", data={"action": "add", "username": "otherlab", "full_name": "Other Lab", "role": "lab",
                               "password": "other1234"})
    other = login(app.test_client(), "otherlab", "other1234")
    assert other.get(f"/lab/apply/{ap['id']}").status_code == 403
    assert other.get(f"/application/{ap['id']}/print").status_code == 403

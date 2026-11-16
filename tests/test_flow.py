"""ทดสอบขั้นตอนทั้งระบบ: ลงทะเบียนห้อง → จัดทีม → ตรวจรอบ 1 → Report 1 → CAPA → รอบ 2 (เฉพาะ NC/OBS) → Report 2"""
import io
import re

from conftest import login
from PIL import Image

PNG = io.BytesIO()
Image.new("RGB", (40, 30), (200, 80, 60)).save(PNG, "PNG")
PNG = PNG.getvalue()


def test_full_workflow(app):
    from scoring import ITEMS
    lab = login(app.test_client(), "labuser", "lab1234")
    lead = login(app.test_client(), "lead", "lead1234")
    aud = login(app.test_client(), "auditor1", "audit1234")

    # 1) ห้องปฏิบัติการลงทะเบียน + สมัคร
    import fm001
    data = fm001.example_form("ห้องทดสอบ CH 101", "คณะเกษตรศาสตร์ ทรัพยากรธรรมชาติและสิ่งแวดล้อม", "2-0100-9999-0")
    data["file_survey"] = (io.BytesIO(b"%PDF-1.4 survey"), "ผลสำรวจ.pdf", "application/pdf")
    r = lab.post("/lab/apply", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    assert "ห้องทดสอบ CH 101" in lab.get("/lab").get_data(as_text=True)

    # 2) หัวหน้าผู้ตรวจอนุมัติ + จัดทีมอัตโนมัติ
    page = lead.get("/planning?year=2570").get_data(as_text=True)
    app_id = re.search(r'name="app_id" value="(\d+)"', page).group(1)
    lead.post("/planning", data={"year": "2570", "action": "approve", "app_id": app_id})
    page = lead.get("/planning?year=2570").get_data(as_text=True)
    assert 'name="plan_app"' in page
    form = {"year": "2570", "action": "create", "plan_app": app_id}
    for m in re.finditer(r'<select name="(lead_\d+|members_\d+|el_\d+_\d)">(.*?)</select>', page, re.S):
        sel = re.search(r'<option value="(\d+)" selected', m.group(2))
        if sel:
            form.setdefault(m.group(1), [])
            form[m.group(1)].append(sel.group(1)) if isinstance(form[m.group(1)], list) else None
    r = lead.post("/planning", data=form)
    assert r.status_code == 302
    from db import DB
    d = DB()
    aid = d.execute("SELECT audit_id FROM applications WHERE id=?", (int(app_id),)).fetchone()["audit_id"]
    team = d.execute("SELECT * FROM users u JOIN audit_team t ON t.user_id=u.id WHERE t.audit_id=?", (aid,)).fetchall()
    d.close()
    assert aid and len(team) >= 2
    # ผลประโยชน์ทับซ้อน: ไม่มีผู้ตรวจจากคณะเดียวกับห้อง
    assert all("เกษตร" not in (u["faculty"] or "") for u in team)

    # 3) ห้องยังไม่เห็นผลระหว่างตรวจรอบ 1
    assert "อยู่ระหว่างการตรวจประเมิน" in lab.get(f"/lab/audit/{aid}").get_data(as_text=True)
    assert lab.get(f"/audit/{aid}").status_code in (302, 403)

    # 4) ผู้ตรวจให้คะแนน: เต็มทุกข้อ แล้วให้ 0 หนึ่งข้อ + NC + รูป
    for el in range(1, 8):
        assert aud.post(f"/api/audit/{aid}/round/1/element/{el}/fill", json={"mode": "full"}).json["ok"]
    nc_item = "4-36"
    j = aud.post(f"/api/audit/{aid}/round/1/item/{nc_item}", json={"score": "0", "ftype": "NC",
                                                                  "comment": "ไม่มีระบบแสงสว่างฉุกเฉิน"}).json
    assert j["ok"] and j["element"]["NC"] == 1
    obs_item = "2-42"
    aud.post(f"/api/audit/{aid}/round/1/item/{obs_item}", json={"ftype": "OBS", "comment": "ควรเพิ่มเบอร์ติดต่อหน้าตู้"})
    j = aud.post(f"/api/audit/{aid}/round/1/item/{nc_item}/photo",
                 data={"file": (io.BytesIO(PNG), "e.png", "image/png")}, content_type="multipart/form-data").json
    assert j["ok"], j
    att = j["id"]
    assert aud.get(f"/file/{att}").status_code == 200
    page = aud.get(f"/audit/{aid}/round/1/element/4").get_data(as_text=True)
    assert 'class="star"' in page and "รูปหลักฐาน" in page

    # 5) รายงานครั้งที่ 1
    r = lead.post(f"/audit/{aid}/round/1/report", data={"report_date": "2027-01-10", "deadline": "2027-02-10"})
    assert r.status_code == 302
    assert aud.post(f"/api/audit/{aid}/round/1/item/1-5", json={"score": "0"}).status_code == 409
    pdf = lab.get(f"/audit/{aid}/round/1/report/view").get_data(as_text=True)
    assert "สรุปผลตรวจประเมินความปลอดภัยห้องปฏิบัติการ" in pdf and "<svg" in pdf
    assert lab.get(f"/file/{att}").status_code == 200   # ห้องเห็นรูปหลังออกรายงาน

    # 6) รอบ 2 ยังเริ่มไม่ได้ก่อนส่ง CAPA
    lead.post(f"/audit/{aid}/start-round2", data={})
    assert _status(aid) == "r1_reported"

    # 7) ห้องตอบ CAPA (ต้องครบทุกข้อ)
    lab.post(f"/lab/audit/{aid}/capa/submit")
    assert _status(aid) == "r1_reported"
    for it in (nc_item, obs_item):
        assert lab.post(f"/api/lab/audit/{aid}/capa/{it}", json={"root_cause": "ไม่มีผู้รับผิดชอบ",
                        "action": "ติดตั้งแล้ว", "owner": "นาย ก", "due_date": "2027-02-01"}).json["ok"]
    assert lab.post(f"/api/lab/audit/{aid}/capa/1-5", json={"action": "x"}).status_code == 400
    j = lab.post(f"/api/lab/audit/{aid}/capa/{nc_item}/file",
                 data={"file": (io.BytesIO(PNG), "fix.png", "image/png")}, content_type="multipart/form-data").json
    assert j["ok"]
    lab.post(f"/lab/audit/{aid}/capa/submit")
    assert _status(aid) == "capa_submitted"
    assert lab.post(f"/api/lab/audit/{aid}/capa/{nc_item}", json={"action": "แก้"}).status_code == 409

    # 8) รอบ 2: เฉพาะ NC/OBS
    lead.post(f"/audit/{aid}/start-round2", data={"r2_date": "2027-03-01"})
    assert aud.post(f"/api/audit/{aid}/round/2/item/1-5", json={"score": "0"}).status_code == 409
    j = aud.post(f"/api/audit/{aid}/round/2/item/{nc_item}", json={"score": "2", "followup": "resolved",
                                                                  "fnote": "ติดตั้งแล้ว"}).json
    assert j["ok"]
    aud.post(f"/api/audit/{aid}/round/2/item/{obs_item}", json={"followup": "resolved", "confirm": True})
    page = aud.get(f"/audit/{aid}/round/2/element/4").get_data(as_text=True)
    assert "CAPA ของห้องปฏิบัติการ" in page and "ติดตั้งแล้ว" in page
    r = lead.post(f"/audit/{aid}/round/2/report", data={"report_date": "2027-03-10"})
    assert r.status_code == 302
    d = DB()
    a = d.execute("SELECT status FROM audits WHERE id=?", (aid,)).fetchone()
    rep = d.execute("SELECT id FROM reports WHERE audit_id=? AND round=2", (aid,)).fetchone()
    d.close()
    assert a["status"] == "r2_reported"
    r = lab.get(f"/report/{rep['id']}/download")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    pdf2 = lab.get(f"/audit/{aid}/round/2/report/view").get_data(as_text=True)
    assert "ผลการติดตามการแก้ไข" in pdf2 and "แก้ไขแล้ว" in pdf2
    # คะแนนรอบ 2 ต้องเต็ม (รายการที่ไม่ใช่ follow ใช้คะแนนเดิม)
    from scoring import compute
    d = DB()
    rows = {r["item_id"]: r for r in d.execute("SELECT * FROM responses WHERE audit_id=? AND round=2", (aid,)).fetchall()}
    d.close()
    assert len(rows) == len(ITEMS)
    assert compute(rows)["total"]["pct"] == 100.0


def test_pages_render(app):
    c = login(app.test_client(), "admin", "admin1234")
    for u in ["/", "/planning", "/users", "/settings", "/import", "/audit/new", "/account", "/healthz", "/lab?as=lab"]:
        assert c.get(u).status_code == 200, u


def test_csrf_enforced(app):
    app.config["TESTING_NO_CSRF"] = False
    try:
        c = app.test_client()
        r = c.post("/login", data={"username": "admin", "password": "admin1234"})
        assert r.status_code == 302 and "/login" in r.headers["Location"]
        with c.session_transaction() as s:
            tok = s["csrf"]
        r = c.post("/login", data={"username": "admin", "password": "admin1234", "_csrf": tok})
        assert r.status_code == 302 and "/home" in r.headers["Location"]
    finally:
        app.config["TESTING_NO_CSRF"] = True


def test_import_excel(app):
    import os
    p = os.environ.get("ESPREL_SAMPLE_XLSX")
    if not p or not os.path.exists(p):
        import pytest
        pytest.skip("ไม่มีไฟล์ตัวอย่าง")
    c = login(app.test_client(), "lead", "lead1234")
    with open(p, "rb") as f:
        r = c.post("/import", data={"file": (f, "x.xlsx"), "rounds": "2"}, content_type="multipart/form-data")
    assert r.status_code == 302
    aid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    from db import DB
    from scoring import compute
    d = DB()
    r1 = {x["item_id"]: x for x in d.execute("SELECT * FROM responses WHERE audit_id=? AND round=1", (aid,)).fetchall()}
    r2 = {x["item_id"]: x for x in d.execute("SELECT * FROM responses WHERE audit_id=? AND round=2", (aid,)).fetchall()}
    d.close()
    assert (compute(r1)["total"]["max"], compute(r1)["total"]["got"]) == (501, 484)
    assert (compute(r2)["total"]["max"], compute(r2)["total"]["got"]) == (499, 497)


def _status(aid):
    from db import DB
    d = DB()
    try:
        return d.execute("SELECT status FROM audits WHERE id=?", (aid,)).fetchone()["status"]
    finally:
        d.close()

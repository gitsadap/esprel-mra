"""สร้างข้อมูลตัวอย่าง: นำเข้าห้อง AG 2312 จาก Excel แล้วเดินขั้นตอนครบ 2 รอบ + ใบสมัครตัวอย่าง
ใช้:  python scripts/seed_demo.py "<ไฟล์ Excel ตัวอย่าง>"   (ใช้กับฐานข้อมูลทดสอบเท่านั้น)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app  # noqa: E402
from db import DB, hash_pw  # noqa: E402

app.config["TESTING_NO_CSRF"] = True
xlsx = sys.argv[1]


def login(u, p):
    c = app.test_client()
    assert c.post("/login", data={"username": u, "password": p}).status_code == 302
    return c


lead, lab, aud = login("lead", "lead1234"), login("labuser", "lab1234"), login("auditor1", "audit1234")
with open(xlsx, "rb") as f:
    r = lead.post("/import", data={"file": (f, "x.xlsx"), "rounds": "1"}, content_type="multipart/form-data")
aid = int(r.headers["Location"].rstrip("/").split("/")[-1])
lead.post(f"/audit/{aid}/edit", data={
    "lab_name": "ห้องปฏิบัติการกลาง AG 2312", "espre_no": "2-0100-0163-0",
    "faculty": "คณะเกษตรศาสตร์ ทรัพยากรธรรมชาติและสิ่งแวดล้อม",
    "affiliation": "คณะเกษตรศาสตร์ ทรัพยากรธรรมชาติและสิ่งแวดล้อม มหาวิทยาลัยนเรศวร",
    "r1_date": "2026-04-29", "r2_date": "2026-07-10", "year_be": "2569", "deadline": "2026-06-15",
    "lead_user_id": "2", "team": ["2", "3", "4"], "lab_username": "labuser",
    **{f"assign_{e}": str([3, 3, 3, 4, 4, 2, 2][e - 1]) for e in range(1, 8)}})
lead.post(f"/audit/{aid}/round/1/report", data={"report_date": "2026-05-05", "deadline": "2026-06-15"})
d = DB()
follow = [r["item_id"] for r in d.execute("SELECT item_id FROM responses WHERE audit_id=? AND round=1 AND ftype IN ('NC','OBS')", (aid,)).fetchall()]
d.close()
plans = {"4-36": "ติดตั้งไฟฉุกเฉินชนิดชาร์จไฟเพิ่ม 2 จุด บริเวณประตูทางออกและโต๊ะปฏิบัติการ",
         "4-41": "จัดทำ SOP การแยกน้ำทิ้งปนเปื้อนสารเคมี และเก็บรวบรวมส่งกำจัดแทนการเทลงท่อ",
         "4-42": "ขอให้วิศวกรสิ่งแวดล้อมตรวจสอบระบบสุขาภิบาล และกำหนดตรวจทุก 1 ปี",
         "4-47": "ขอให้วิศวกรเครื่องกลตรวจระบบระบายอากาศ/ปรับอากาศ และกำหนดตรวจทุก 1 ปี",
         "4-56": "ขอให้วิศวกรไฟฟ้าตรวจระบบฉุกเฉินและการสื่อสาร และกำหนดตรวจทุก 1 ปี",
         "3-38": "ย้ายภาชนะของเสียไวไฟออกห่างแหล่งความร้อน และเพิ่มใน SOP"}
for it in follow:
    lab.post(f"/api/lab/audit/{aid}/capa/{it}", json={"root_cause": "ยังไม่มีแนวปฏิบัติ/ผู้รับผิดชอบชัดเจน",
             "action": plans.get(it, "ปรับปรุงตามข้อเสนอแนะของคณะผู้ตรวจ และจัดทำหลักฐาน"),
             "owner": "หัวหน้าห้องปฏิบัติการ", "due_date": "2026-06-10"})
lab.post(f"/lab/audit/{aid}/capa/submit")
lead.post(f"/audit/{aid}/start-round2", data={"r2_date": "2026-07-10"})
for it in follow:
    st = "partial" if it == "4-41" else "resolved"
    aud.post(f"/api/audit/{aid}/round/2/item/{it}", json={"followup": st, "confirm": True,
             "fnote": "ตรวจพบหลักฐานการแก้ไขครบถ้วน" if st == "resolved" else "มี SOP แล้ว แต่ยังไม่มีมาตรการตรวจผลการบำบัด"})
from scoring import ITEMS  # noqa: E402
for it in follow:
    if it != "4-41":
        aud.post(f"/api/audit/{aid}/round/2/item/{it}", json={"score": str(ITEMS[it]["max"])})
aud.post(f"/api/audit/{aid}/round/2/item/4-41", json={"score": "0", "ftype": "NC",
         "comment": "มี SOP แต่ยังไม่มีมาตรการตรวจผลการบำบัดที่แสดงว่าได้ปฏิบัติตาม SOP และไม่มีผลกระทบต่อสิ่งแวดล้อม"})
for e in range(1, 8):
    aud.post(f"/api/audit/{aid}/round/2/element/{e}/done", json={"done": True})
lead.post(f"/audit/{aid}/round/2/report", data={"report_date": "2026-07-31"})
# ใบสมัครตัวอย่างปีถัดไป
import io  # noqa: E402
import fm001  # noqa: E402


def apply_fm001(name, fac, email, espre=""):
    """ส่งใบสมัครผ่านหน้าสาธารณะ (ไม่เข้าสู่ระบบ) → ได้บัญชีห้องปฏิบัติการของห้องนั้นเอง"""
    d = fm001.example_form(name, fac, espre, head_name="หัวหน้าห้องปฏิบัติการ (ตัวอย่าง)", coord_email=email,
                           coord_name=f"ผู้ประสานงาน {name.split()[-1]} (ตัวอย่าง)")
    d["file_survey"] = (io.BytesIO(b"%PDF-1.4 demo survey"), "ESPReL-survey.pdf", "application/pdf")
    r = app.test_client().post("/apply", data=d, content_type="multipart/form-data")
    assert r.status_code == 200, r.status_code
    # บัญชีตัวอย่าง: ตั้งรหัสผ่านที่จำง่าย (ใช้จริงระบบสร้างรหัสสุ่มและส่งทางอีเมล)
    db = DB()
    db.execute("UPDATE users SET password_hash=?, must_change_pw=0 WHERE username=?", (hash_pw("lab1234"), email))
    db.commit()
    db.close()


DEMO_LABS = [("ห้องปฏิบัติการจุลชีววิทยาดิน AG 3105", "คณะเกษตรศาสตร์ ทรัพยากรธรรมชาติและสิ่งแวดล้อม", "ag3105@example.org"),
             ("ห้องปฏิบัติการเคมีวิเคราะห์ SC 2201", "คณะวิทยาศาสตร์", "sc2201@example.org"),
             ("ห้องปฏิบัติการวิศวกรรมสิ่งแวดล้อม EN 4102", "คณะวิศวกรรมศาสตร์", "en4102@example.org"),
             ("ห้องปฏิบัติการเภสัชเคมี PH 3304", "คณะเภสัชศาสตร์", "ph3304@example.org")]
print("demo audit", aid)
admin = login("admin", "admin1234")
for u, n, r, fac in [("prasert", "รศ.ดร.ประเสริฐ (ตัวอย่าง)", "lead", "คณะวิศวกรรมศาสตร์"),
                     ("wilai", "ผศ.ดร.วิไล (ตัวอย่าง)", "assessor", "คณะเภสัชศาสตร์"),
                     ("anan", "ดร.อนันต์ (ตัวอย่าง)", "assessor", "คณะวิทยาศาสตร์")]:
    admin.post("/users", data={"action": "add", "username": u, "full_name": n, "role": r, "faculty": fac, "password": "demo1234"})
for name, fac, email in DEMO_LABS:
    apply_fm001(name, fac, email)
d = DB()
ids = [r["id"] for r in d.execute("SELECT id FROM applications WHERE year_be=2570").fetchall()]
d.close()
lead.post("/planning", data={"year": "2570", "action": "approve", "app_id": [str(i) for i in ids[1:]]})
print("applications", ids)
print("บัญชีห้องปฏิบัติการตัวอย่าง (รหัสผ่าน lab1234): labuser (AG 2312), " + ", ".join(e for _, _, e in DEMO_LABS))

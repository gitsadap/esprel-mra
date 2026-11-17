"""ผู้สังเกตการณ์: ได้รับเชิญรายการตรวจ → ดูผล (อ่านอย่างเดียว) + บันทึกความเห็น · ให้คะแนน/แก้ไขไม่ได้ · เห็นเฉพาะห้องที่ได้รับเชิญ"""
import re

from conftest import login

import mailer


def _new_audit(lead, name, observers=()):
    r = lead.post("/audit/new", data={"lab_name": name, "r1_date": "2026-12-01", "observers": list(observers)})
    assert r.status_code == 302, r.data[:300]
    return int(re.search(r"/audit/(\d+)", r.headers["Location"]).group(1))


def test_observer_flow(app):
    admin = login(app.test_client(), "admin", "admin1234")
    lead = login(app.test_client(), "lead", "lead1234")

    # admin เพิ่มผู้สังเกตการณ์ภายนอก (ชื่อผู้ใช้เป็นอีเมล) → ระบบสร้างรหัสผ่านและส่งอีเมล
    mailer.OUTBOX.clear()
    admin.post("/users", data={"action": "add", "username": "Observer.One@example.org", "full_name": "คุณสังเกต หนึ่ง",
                               "role": "observer", "password": ""})
    msg = [m for m in mailer.OUTBOX if m["to"] == "observer.one@example.org"][-1]
    assert "ผู้สังเกตการณ์" in msg["body"]
    pw = re.search(r"รหัสผ่านชั่วคราว : (\S+)", msg["body"]).group(1)
    from db import DB
    d = DB()
    oid = d.execute("SELECT id FROM users WHERE username='observer.one@example.org'").fetchone()["id"]
    d.close()

    # หัวหน้าผู้ตรวจเชิญเข้าร่วมการตรวจห้อง A (ห้อง B ไม่ได้เชิญ)
    form = lead.get("/audit/new").get_data(as_text=True)
    assert "คุณสังเกต หนึ่ง" in form and 'name="observers"' in form
    aid = _new_audit(lead, "ห้องสังเกตการณ์ A", [str(oid)])
    other = _new_audit(lead, "ห้องไม่ได้เชิญ B")

    obs = app.test_client()
    obs.post("/login", data={"username": "observer.one@example.org", "password": pw})
    obs.post("/account", data={"old": pw, "new": "Observe-2026!", "confirm": "Observe-2026!"})

    # หน้าแรก → รายการที่สังเกตการณ์ (เห็นเฉพาะห้องที่ได้รับเชิญ)
    assert obs.get("/").headers["Location"].endswith("/observe")
    home = obs.get("/observe").get_data(as_text=True)
    assert "ห้องสังเกตการณ์ A" in home and "ห้องไม่ได้เชิญ B" not in home
    assert obs.get(f"/observe/{other}").status_code == 403
    assert obs.get(f"/lab/audit/{other}").status_code == 403

    # ดูผล (อ่านอย่างเดียว)
    assert obs.get(f"/observe/{aid}").status_code == 200
    res = obs.get(f"/observe/{aid}/round/1").get_data(as_text=True)
    assert "สรุปผลการตรวจประเมิน" in res and "data-sum=" not in res and "สร้าง Report" not in res

    # ให้คะแนน / หน้าผู้ตรวจ / จัดการ → ไม่ได้
    assert obs.get(f"/audit/{aid}").status_code == 302
    assert obs.post(f"/api/audit/{aid}/round/1/item/1-5", json={"score": "1"}).status_code == 403
    assert obs.get(f"/audit/{aid}/round/1/element/1").status_code == 302
    assert obs.get("/planning").status_code == 302 and obs.get("/users").status_code == 302

    # บันทึกความเห็น → คณะผู้ตรวจเห็นในหน้าการตรวจ
    obs.post(f"/observe/{aid}", data={"round": "1", "element": "3", "body": "ป้ายทางหนีไฟบางจุดถูกบัง"})
    assert "ป้ายทางหนีไฟบางจุดถูกบัง" in obs.get(f"/observe/{aid}").get_data(as_text=True)
    page = lead.get(f"/audit/{aid}").get_data(as_text=True)
    assert "ผู้สังเกตการณ์ และความเห็น" in page and "ป้ายทางหนีไฟบางจุดถูกบัง" in page and "องค์ 3" in page
    # ผู้สังเกตการณ์บันทึกในการตรวจที่ไม่ได้รับเชิญไม่ได้
    assert obs.post(f"/observe/{other}", data={"body": "x"}).status_code == 403

    # ลบความเห็นของตนเอง
    d = DB()
    nid = d.execute("SELECT id FROM observer_notes WHERE audit_id=?", (aid,)).fetchone()["id"]
    d.close()
    obs.post(f"/observe/{aid}", data={"action": "delete", "note_id": str(nid)})
    d = DB()
    assert not d.execute("SELECT 1 x FROM observer_notes WHERE id=?", (nid,)).fetchone()
    d.close()

    # เอาออกจากทีม → เข้าไม่ได้อีก
    edit = lead.get(f"/audit/{aid}/edit").get_data(as_text=True)
    assert re.search(rf'name="observers" value="{oid}" checked', edit)
    lead.post(f"/audit/{aid}/edit", data={"lab_name": "ห้องสังเกตการณ์ A", "r1_date": "2026-12-01"})
    assert obs.get(f"/observe/{aid}").status_code == 403


def test_invite_from_audit_page_and_image_shrink(app):
    import io

    from PIL import Image

    import storage
    from core import shrink_image
    admin = login(app.test_client(), "admin", "admin1234")
    lead = login(app.test_client(), "lead", "lead1234")
    admin.post("/users", data={"action": "add", "username": "obs2", "full_name": "คุณสังเกต สอง", "role": "observer"})
    assert "ผู้สังเกตการณ์" in admin.get("/users?role=observer").get_data(as_text=True)
    from db import DB
    d = DB()
    oid = d.execute("SELECT id FROM users WHERE username='obs2'").fetchone()["id"]
    d.close()
    aid = _new_audit(lead, "ห้องเชิญจากหน้าการตรวจ")
    page = lead.get(f"/audit/{aid}").get_data(as_text=True)
    assert "+ เชิญ" in page and "คุณสังเกต สอง" in page
    r = lead.post(f"/audit/{aid}/observers", data={"user_id": str(oid)}, follow_redirects=True)
    assert "เชิญ คุณสังเกต สอง เป็นผู้สังเกตการณ์แล้ว" in r.get_data(as_text=True)
    d = DB()
    assert d.execute("SELECT 1 x FROM audit_observers WHERE audit_id=? AND user_id=?", (aid, oid)).fetchone()
    d.close()
    lead.post(f"/audit/{aid}/observers", data={"action": "remove", "user_id": str(oid)})
    d = DB()
    assert not d.execute("SELECT 1 x FROM audit_observers WHERE audit_id=?", (aid,)).fetchone()
    d.close()

    # ย่อรูป: 4000x3000 + EXIF หมุน → ด้านยาว 1600, JPEG, ไม่มี EXIF
    im = Image.new("RGB", (4000, 3000), (120, 160, 200))
    exif = Image.Exif()
    exif[0x0112] = 6          # หมุน 90°
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95, exif=exif)
    out, ctype = shrink_image(b.getvalue(), "image/jpeg")
    small = Image.open(io.BytesIO(out))
    assert ctype == "image/jpeg" and small.size == (1200, 1600) and not small.getexif()
    assert len(out) < len(b.getvalue())
    # PNG โปร่งใสคงเป็น PNG · ไฟล์ที่ไม่ใช่รูปคืนเดิม
    p = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (0, 0, 0, 0)).save(p, "PNG")
    out, ctype = shrink_image(p.getvalue(), "image/png")
    assert ctype == "image/png" and Image.open(io.BytesIO(out)).size == (1600, 800)
    assert shrink_image(b"not an image", "image/heic") == (b"not an image", "image/heic")

    # อัปโหลดรูปหลักฐานจริงผ่าน API → เก็บแบบย่อแล้ว
    big = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 200, 30)).save(big, "PNG")
    r = lead.post(f"/api/audit/{aid}/round/1/item/1-5/photo", data={"file": (io.BytesIO(big.getvalue()), "x.png", "image/png")},
                  content_type="multipart/form-data")
    assert r.status_code == 200, r.data[:300]
    d = DB()
    att = d.execute("SELECT * FROM attachments WHERE audit_id=? ORDER BY id DESC", (aid,)).fetchone()
    d.close()
    assert att["content_type"] == "image/jpeg" and att["path"].endswith(".jpg") and att["filename"].endswith(".jpg")
    assert Image.open(io.BytesIO(storage.load(att["path"]))).size == (1600, 1067)

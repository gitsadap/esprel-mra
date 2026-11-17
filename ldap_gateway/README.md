# ESPReL LDAP gateway — API ใน NRED (intranet มน.)

```
ผู้ตรวจ ─► ESPReL MRA (Vercel) ──HTTPS + ลายเซ็น HMAC──► NRED /api/esprel/ldap-verify ──► LDAP/AD 10.10.10.71 (user@nu.local)
                     ◄── 200 {ok, ชื่อ, อีเมล, หน่วยงาน} + ลายเซ็นคำตอบ ──┘
ESPReL ตรวจลายเซ็นคำตอบ → ตรวจรายชื่อใน Supabase (users → ทะเบียนผู้ตรวจ) → เข้าระบบ
```

API ไม่เก็บรหัสผ่าน ไม่ออก token ไม่ต่อฐานข้อมูล — bind LDAP แบบเดียวกับหน้า login ของ NRED เท่านั้น

## ความปลอดภัย
| ภัย | การป้องกัน |
|---|---|
| ดักฟังรหัสผ่าน | HTTPS เท่านั้น (ปฏิเสธคำขอที่ไม่ใช่ https ตาม `X-Forwarded-Proto`) · ตั้ง proxy ไม่ให้ log body |
| คนอื่นเรียก API เพื่อเดารหัส | ทุกคำขอลงลายเซ็น HMAC-SHA256 ด้วยคีย์ลับ (คีย์ไม่ถูกส่งไปกับคำขอ) · ไม่มีลายเซ็น = 403 โดยไม่แตะ LDAP |
| ดักคำขอแล้วส่งซ้ำ (replay) | ลายเซ็นผูกเวลา (±60 วินาที) + nonce ใช้ครั้งเดียว |
| แก้ไขคำขอระหว่างทาง | ลายเซ็นครอบ SHA-256 ของ body |
| เซิร์ฟเวอร์/คำตอบปลอม | คำตอบลงลายเซ็นผูกกับ nonce ของคำขอ — แอป ESPReL ไม่เชื่อคำตอบที่ไม่มีลายเซ็นถูกต้อง |
| เดารหัสผ่าน | ผิด 8 ครั้ง/15 นาที ต่อชื่อผู้ใช้ · 30 ครั้ง/15 นาที ต่อ IP (ฝั่ง ESPReL จำกัดซ้ำอีกชั้น) |
| รหัสผ่านว่าง (anonymous bind) | ปฏิเสธทุกครั้งก่อนถึง LDAP |
| LDAP injection | ชื่อผู้ใช้รับเฉพาะ `[a-z0-9._-]` + escape ตอนค้นหา |
| ข้อมูลรั่ว | ข้อความผิดพลาดสั้น ๆ ไม่บอกเหตุผล · `/ldap-health` ไม่เปิดเผย IP/ค่าภายใน · log ไม่มีรหัสผ่าน · `Cache-Control: no-store` |
| คีย์อ่อน | คีย์ < 32 ตัวอักษร → API ปิดตัวเอง (503) |

จำกัด IP ผู้เรียกเพิ่มได้ด้วย `ESPREL_ALLOWED_IPS` (Vercel ไม่มี IP คงที่ในแผนทั่วไป จึงพึ่งลายเซ็นเป็นหลัก)

## ติดตั้งใน NRED
```bash
cd <โฟลเดอร์โปรเจค NRED>
python /path/to/ESPReL_MRA_v2/ldap_gateway/install_into_nred.py --check   # ดูว่าจะเปลี่ยนอะไร
python /path/to/ESPReL_MRA_v2/ldap_gateway/install_into_nred.py           # ติดตั้ง
git add app/routers/esprel_gateway.py main.py .env.example
git commit -m "Add ESPReL MRA LDAP gateway API" && git push
```
สคริปต์จะ: คัดลอก router → `app/routers/esprel_gateway.py`, เพิ่ม 2 บรรทัดใน `main.py`, สร้าง `ESPREL_GATEWAY_KEY` ใน `.env` (ไม่ commit)
ค่าอื่นใช้ค่าเริ่มต้นเหมือน NRED (`ldap://10.10.10.71`, `nu.local`) — เปลี่ยนได้ด้วย `ESPREL_LDAP_*` ใน `.env`

## เปิดให้ Vercel เรียกได้ (ขอกองเทคโนโลยีสารสนเทศ)
เปิด **เฉพาะ path `/api/esprel/`** ของ NRED ออกอินเทอร์เน็ตผ่าน HTTPS (reverse proxy ใน DMZ หรือ Cloudflare Tunnel) ตัวอย่าง nginx:
```nginx
location /api/esprel/ {
    proxy_pass http://<NRED ภายใน>;          # เช่น http://10.10.58.21
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    client_max_body_size 4k;
    access_log off;                          # ไม่บันทึกคำขอที่มีรหัสผ่าน
}
```

## ตั้งค่าฝั่ง ESPReL (Vercel → Environment Variables)
```
LDAP_GATEWAY_URL=https://<โดเมนที่เปิด>/api/esprel/ldap-verify
LDAP_GATEWAY_KEY=<ค่าเดียวกับ ESPREL_GATEWAY_KEY ใน .env ของ NRED>
```
ทดสอบจริงหลัง deploy (จากเครื่องที่มี .env): `python ldap_gateway/check_gateway.py` — health, ปฏิเสธคำขอไม่มีลายเซ็น, login บัญชี NU จริง
ตรวจ: `GET https://<โดเมน>/api/esprel/ldap-health` → `{"ok": true, "protocol": "v1"}` · หน้า `/healthz` ของ ESPReL → `"ldap": "gateway"`
นาฬิกาของเซิร์ฟเวอร์ NRED ต้องตรง (NTP) เพราะลายเซ็นมีอายุ 60 วินาที

## รันแยกจาก NRED (ทางเลือก)
```
pip install -r requirements.txt
ESPREL_GATEWAY_KEY=... uvicorn esprel_ldap_gateway:app --host 127.0.0.1 --port 8701
```

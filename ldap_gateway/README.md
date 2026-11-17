# ESPReL LDAP gateway (ติดตั้งบนเซิร์ฟเวอร์ภายใน มน.)

ขั้นตอนเข้าสู่ระบบของผู้ตรวจ (บัญชี มน.):

```
เบราว์เซอร์ → ESPReL MRA (Vercel) ──HTTPS + คีย์ลับ──► gateway บนเซิร์ฟเวอร์ภายใน ──► LDAP/AD 10.10.10.71 (user@nu.local)
                         ◄── ผ่าน/ไม่ผ่าน (+ชื่อ, อีเมล, หน่วยงาน) ──┘
ESPReL ตรวจต่อในฐานข้อมูล Supabase: มีในตาราง users? → เข้าได้ตามบทบาท
                                  ไม่มี แต่อยู่ในทะเบียนผู้ตรวจ (อีเมล @nu.ac.th ตรงกัน)? → เปิดบัญชีผู้ตรวจให้อัตโนมัติ
                                  ไม่มีทั้งสองที่ → ปฏิเสธ (แจ้งให้ติดต่อผู้ดูแล)
```

gateway ไม่เก็บรหัสผ่าน ไม่ออก token และไม่ต่อฐานข้อมูล — ทำหน้าที่ bind LDAP แบบเดียวกับระบบ NRED เท่านั้น

## ติดตั้ง

**แบบ 1 — เพิ่มเข้าระบบ NRED (FastAPI) ที่มีอยู่แล้ว**
1. คัดลอก `esprel_ldap_gateway.py` ไปที่ `app/routers/`
2. ใน `main.py` เพิ่ม
   ```python
   from app.routers.esprel_ldap_gateway import router as esprel_ldap_router
   app.include_router(esprel_ldap_router)
   ```
3. ตั้ง `ESPREL_GATEWAY_KEY` ใน `.env` ของ NRED (สร้างด้วย `python -c "import secrets;print(secrets.token_urlsafe(40))"`)
   แล้วรีสตาร์ต — endpoint คือ `https://<โดเมน NRED>/api/esprel/ldap-verify`

**แบบ 2 — รันแยก**
```
pip install -r requirements.txt
ESPREL_GATEWAY_KEY=... uvicorn esprel_ldap_gateway:app --host 127.0.0.1 --port 8701
```
nginx (HTTPS):
```
location /api/esprel/ { proxy_pass http://127.0.0.1:8701; proxy_set_header X-Forwarded-For $remote_addr; }
```

## ตั้งค่าฝั่งแอป ESPReL (Vercel → Environment Variables หรือ .env)
```
LDAP_GATEWAY_URL=https://<โดเมนเซิร์ฟเวอร์ภายใน>/api/esprel/ldap-verify
LDAP_GATEWAY_KEY=<คีย์เดียวกับ ESPREL_GATEWAY_KEY>
```
ตรวจสถานะ: `GET https://<โดเมน>/api/esprel/ldap-health` · หน้า `/healthz` ของ ESPReL แสดง `"ldap": "gateway"`

## ความปลอดภัย
- ต้องเปิดผ่าน **HTTPS เท่านั้น** (มีรหัสผ่านในคำขอ) · ใช้คีย์ลับยาว ๆ · จำกัด IP ได้ด้วย `ESPREL_ALLOWED_IPS`
- ปฏิเสธรหัสผ่านว่างทุกครั้ง (AD บางเครื่องถือว่า bind แบบไม่ระบุตัวตน "ผ่าน") — แนะนำเพิ่มการตรวจนี้ในหน้า login ของ NRED ด้วย
- จำกัดการเดารหัส 8 ครั้ง/15 นาที ต่อชื่อผู้ใช้ (ฝั่งแอป ESPReL จำกัดซ้ำอีกชั้น)
- ถ้า Vercel เรียกเซิร์ฟเวอร์ภายในไม่ได้ (เปิดเฉพาะเครือข่าย มน.) ให้ติดตั้งแอป ESPReL บนเซิร์ฟเวอร์ภายในแทน แล้วใช้
  `LDAP_URL=ldap://10.10.10.71` + `LDAP_UPN_SUFFIX=@nu.local` (bind ตรง ไม่ต้องใช้ gateway)

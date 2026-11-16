#!/bin/bash
# ดับเบิลคลิกเพื่อรัน ESPReL MRA ในเครื่อง (โหมดทดสอบ: SQLite + บัญชีทดสอบ)
cd "$(dirname "$0")"
PY=python3
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
echo "กำลังติดตั้ง/ตรวจสอบแพ็กเกจ ..."
pip install -q --upgrade pip >/dev/null 2>&1
pip install -q -r requirements.txt || { echo "ติดตั้งแพ็กเกจไม่สำเร็จ"; read -r; exit 1; }
export PORT=${PORT:-8080}
# ปิดโปรแกรมรอบก่อนที่ยังเปิดค้างอยู่ (เฉพาะ ESPReL MRA ที่ใช้ port เดียวกัน)
for PID in $(lsof -ti tcp:$PORT 2>/dev/null); do
  if ps -o command= -p "$PID" 2>/dev/null | grep -q "app.py"; then
    echo "ปิดโปรแกรมรอบก่อนที่ยังเปิดอยู่ ..."; kill "$PID" 2>/dev/null; sleep 1
  else
    echo "port $PORT ถูกโปรแกรมอื่นใช้อยู่ — ปิดโปรแกรมนั้นก่อน หรือรันด้วย PORT=8090"; read -r; exit 1
  fi
done
# ครั้งแรก (หรือฐานข้อมูลว่าง): สร้างข้อมูลตัวอย่างจากไฟล์ Excel ห้อง AG 2312 (ถ้ามีในโฟลเดอร์แม่)
if grep -q "^DATABASE_URL=postgres" .env 2>/dev/null && grep -q "^SUPABASE_DB_PASSWORD=..*" .env 2>/dev/null; then
  echo "ใช้ฐานข้อมูล Supabase (ตาม .env)"
elif [ ! -s instance/esprel.db ]; then
  X=$(ls ../2.ตรวจประเมิน*.xlsx 2>/dev/null | head -1)
  if [ -n "$X" ]; then echo "สร้างข้อมูลตัวอย่าง ..."; python scripts/seed_demo.py "$X" >/dev/null; fi
fi
( sleep 3; open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null ) &
echo ""
if grep -q "^SUPABASE_DB_PASSWORD=..*" .env 2>/dev/null; then
  echo "  ฐานข้อมูลจริง (Supabase): เข้าสู่ระบบด้วย admin / รหัสผ่านในบรรทัด ADMIN_INITIAL_PASSWORD ของไฟล์ .env"
else
  echo "  บัญชีทดสอบ: admin/admin1234 · lead/lead1234 · auditor1/audit1234"
  echo "  ห้องปฏิบัติการ (รหัส lab1234): labuser · ag3105@example.org · sc2201@example.org · en4102@example.org · ph3304@example.org"
fi
echo "  สมัครแบบไม่ต้อง login: http://localhost:$PORT/apply  (ยังไม่ตั้งค่าอีเมล → แสดงรหัสผ่านบนจอแทน)"
echo "  ปิดโปรแกรม: กด Ctrl+C หรือปิดหน้าต่างนี้"
python app.py

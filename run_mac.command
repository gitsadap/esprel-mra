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
# ครั้งแรก: สร้างข้อมูลตัวอย่างจากไฟล์ Excel ห้อง AG 2312 (ถ้ามีในโฟลเดอร์แม่)
if [ ! -f instance/esprel.db ]; then
  X=$(ls ../2.ตรวจประเมิน*.xlsx 2>/dev/null | head -1)
  if [ -n "$X" ]; then echo "สร้างข้อมูลตัวอย่าง ..."; python scripts/seed_demo.py "$X" >/dev/null; fi
fi
( sleep 2; open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null ) &
echo ""
echo "  บัญชีทดสอบ: admin/admin1234 · lead/lead1234 · auditor1/audit1234"
echo "  ห้องปฏิบัติการ (รหัส lab1234): labuser · ag3105@example.org · sc2201@example.org · en4102@example.org · ph3304@example.org"
echo "  สมัครแบบไม่ต้อง login: http://localhost:$PORT/apply  (โหมดทดสอบแสดงรหัสผ่านบนจอแทนการส่งอีเมล)"
echo "  ปิดโปรแกรม: กด Ctrl+C หรือปิดหน้าต่างนี้"
python app.py

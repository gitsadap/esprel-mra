"""ทดสอบ API ของ NRED จากเครื่องตัวเอง (หลัง deploy) — ใช้โค้ดฝั่งเดียวกับแอป ESPReL

    python ldap_gateway/check_gateway.py            # อ่าน LDAP_GATEWAY_URL / LDAP_GATEWAY_KEY จาก .env
    python ldap_gateway/check_gateway.py --url https://.../api/esprel/ldap-verify

ตรวจ 3 อย่าง: 1) health  2) คำขอไม่มีลายเซ็นต้องถูกปฏิเสธ  3) login ด้วยบัญชี NU จริง (พิมพ์รหัสผ่าน ไม่แสดงบนจอ)
"""
import getpass
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth_ldap  # noqa: E402
import config  # noqa: E402


def main():
    if "--url" in sys.argv:
        config.LDAP_GATEWAY_URL = sys.argv[sys.argv.index("--url") + 1]
    url = config.LDAP_GATEWAY_URL
    if not url or not config.LDAP_GATEWAY_KEY:
        raise SystemExit("ตั้ง LDAP_GATEWAY_URL และ LDAP_GATEWAY_KEY ใน .env ก่อน")
    base = url.rsplit("/", 1)[0]
    ok = True
    r = requests.get(base + "/ldap-health", timeout=10)
    print(f"1) health            HTTP {r.status_code} {r.text[:80]}")
    ok &= r.status_code == 200 and r.json().get("ok") is True
    r = requests.post(url, json={"username": "x", "password": "y"}, timeout=10)
    print(f"2) ไม่มีลายเซ็น       HTTP {r.status_code} (ต้องเป็น 403)")
    ok &= r.status_code == 403
    user = input("3) ชื่อผู้ใช้ NU (Enter = ข้าม): ").strip()
    if user:
        pw = getpass.getpass("   รหัสผ่าน: ")
        try:
            info = auth_ldap._via_gateway(auth_ldap.normalize_username(user), pw)
            print("   ผลลัพธ์:", "ผ่าน — " + str(info) if info else "ไม่ผ่าน (รหัสผ่านไม่ถูกต้อง)")
            ok &= info is not None
        except auth_ldap.LDAPError as ex:
            print("   ผิดพลาด:", ex)
            ok = False
    print("\nสรุป:", "พร้อมใช้งาน ✓" if ok else "ยังมีจุดต้องแก้ ✗")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

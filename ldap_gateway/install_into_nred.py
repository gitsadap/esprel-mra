"""ติดตั้ง ESPReL LDAP gateway เข้าโปรเจค NRED (FastAPI) — รันในโฟลเดอร์โปรเจค NRED:

    python /path/to/install_into_nred.py            # ติดตั้ง/อัปเดต
    python /path/to/install_into_nred.py --check    # ดูว่าจะเปลี่ยนอะไร (ไม่เขียนไฟล์)

สิ่งที่ทำ (รันซ้ำได้ ไม่ซ้ำซ้อน)
  1) คัดลอก esprel_ldap_gateway.py → app/routers/esprel_gateway.py
  2) main.py: เพิ่ม import และ app.include_router(esprel_gateway_router)
  3) .env: เพิ่ม ESPREL_GATEWAY_KEY (สุ่มใหม่ 48 ตัว) ถ้ายังไม่มี  ·  .env.example: เพิ่มตัวอย่างค่า ESPREL_*
จากนั้น commit/push ตามปกติ แล้วนำค่า ESPREL_GATEWAY_KEY ไปใส่ LDAP_GATEWAY_KEY บน Vercel
"""
import ast
import os
import secrets
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMPORT_LINE = "from app.routers.esprel_gateway import router as esprel_gateway_router"
INCLUDE_LINE = "app.include_router(esprel_gateway_router)"
ENV_EXAMPLE = """
# --- ESPReL MRA: LDAP gateway (/api/esprel/ldap-verify) ---
ESPREL_GATEWAY_KEY=change-me-to-a-random-string-at-least-32-chars
ESPREL_LDAP_SERVER=ldap://10.10.10.71
ESPREL_LDAP_DOMAIN=nu.local
# ESPREL_LDAP_BASE_DN=DC=nu,DC=local
# ESPREL_LDAP_REQUIRE_TLS=0
# ESPREL_ALLOWED_IPS=
"""


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def patch_main(src):
    if IMPORT_LINE in src and INCLUDE_LINE in src:
        return src, False
    tree = ast.parse(src)
    lines = src.split("\n")
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    router_imports = [n for n in imports if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app.routers")]
    anchor_imp = (router_imports or imports)[-1].end_lineno if (router_imports or imports) else 0
    includes, app_assign = [], None
    for n in tree.body:
        if (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "include_router"):
            includes.append(n)
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "FastAPI"
                and any(getattr(t, "id", "") == "app" for t in n.targets)):
            app_assign = n
    if includes:
        anchor_inc = includes[-1].end_lineno
    elif app_assign is not None:
        anchor_inc = app_assign.end_lineno
    else:
        raise SystemExit("ไม่พบ app = FastAPI(...) ใน main.py — เพิ่มสองบรรทัดนี้เอง:\n  " + IMPORT_LINE + "\n  " + INCLUDE_LINE)
    if INCLUDE_LINE not in src:
        lines.insert(anchor_inc, INCLUDE_LINE)
    if IMPORT_LINE not in src:
        lines.insert(anchor_imp, IMPORT_LINE)
    return "\n".join(lines), True


def main(check=False):
    root = os.getcwd()
    main_py = os.path.join(root, "main.py")
    routers = os.path.join(root, "app", "routers")
    if not os.path.exists(main_py) or not os.path.isdir(routers):
        raise SystemExit("ต้องรันในโฟลเดอร์โปรเจค NRED (มี main.py และ app/routers/)")
    changes = []
    dst = os.path.join(routers, "esprel_gateway.py")
    src_file = os.path.join(HERE, "esprel_ldap_gateway.py")
    if not os.path.exists(dst) or _read(dst) != _read(src_file):
        changes.append("app/routers/esprel_gateway.py")
        if not check:
            shutil.copyfile(src_file, dst)
    new, changed = patch_main(_read(main_py))
    if changed:
        changes.append("main.py")
        if not check:
            with open(main_py, "w", encoding="utf-8") as fh:
                fh.write(new)
    env = os.path.join(root, ".env")
    key = None
    env_text = _read(env)
    if "ESPREL_GATEWAY_KEY=" not in env_text:
        key = secrets.token_urlsafe(36)
        changes.append(".env (ESPREL_GATEWAY_KEY ใหม่)")
        if not check:
            with open(env, "a", encoding="utf-8") as fh:
                fh.write(f"\n# ESPReL MRA LDAP gateway — ค่าเดียวกับ LDAP_GATEWAY_KEY บน Vercel (ห้าม commit)\n"
                         f"ESPREL_GATEWAY_KEY={key}\n")
    ex = os.path.join(root, ".env.example")
    if os.path.exists(ex) and "ESPREL_GATEWAY_KEY" not in _read(ex):
        changes.append(".env.example")
        if not check:
            with open(ex, "a", encoding="utf-8") as fh:
                fh.write(ENV_EXAMPLE)
    print(("จะเปลี่ยน: " if check else "เปลี่ยนแล้ว: ") + (", ".join(changes) or "ไม่มี (ติดตั้งแล้ว)"))
    if key and not check:
        print("\nคีย์ใหม่ถูกเขียนใน .env ของ NRED แล้ว — คัดลอกบรรทัด ESPREL_GATEWAY_KEY ไปตั้งเป็น LDAP_GATEWAY_KEY บน Vercel")
    print("ตรวจสอบ: GET https://<โดเมน NRED>/api/esprel/ldap-health → {\"ok\": true, ...}")


if __name__ == "__main__":
    main(check="--check" in sys.argv)

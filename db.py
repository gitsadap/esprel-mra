"""ชั้นฐานข้อมูล: Supabase Postgres (psycopg) หรือ SQLite สำหรับพัฒนาในเครื่อง
โค้ดส่วนอื่นเขียน SQL แบบเดียว ใช้ ? เป็น placeholder และ RETURNING id เมื่อต้องการ id ใหม่"""
import os
import re
import sqlite3
from datetime import datetime

from flask import g
from werkzeug.security import generate_password_hash

import config


def hash_pw(password):
    """pbkdf2:sha256 ใช้ได้กับ Python ทุกรุ่น (Python บน macOS ไม่มี hashlib.scrypt)"""
    return generate_password_hash(password, method="pbkdf2:sha256")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


IS_PG = bool(config.DATABASE_URL)

# --------------------------------------------------------------------------- schema
# {PK} = คอลัมน์ id อัตโนมัติ (แปลงตามชนิดฐานข้อมูล)
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id {PK},
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT,
  full_name TEXT NOT NULL,
  email TEXT,
  faculty TEXT,
  role TEXT NOT NULL DEFAULT 'assessor',
  auth_source TEXT NOT NULL DEFAULT 'local',
  active INTEGER NOT NULL DEFAULT 1,
  last_login TEXT,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS labs(
  id {PK},
  name TEXT NOT NULL,
  espre_no TEXT,
  faculty TEXT,
  affiliation TEXT,
  location TEXT,
  head_name TEXT,
  contact TEXT,
  phone TEXT,
  email TEXT,
  department TEXT,
  organization TEXT,
  fax TEXT,
  owner_user_id INTEGER REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'approved',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS lab_members(
  lab_id INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(lab_id, user_id)
);
CREATE TABLE IF NOT EXISTS audits(
  id {PK},
  lab_id INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
  year_be INTEGER NOT NULL,
  r1_date TEXT,
  r2_date TEXT,
  status TEXT NOT NULL DEFAULT 'r1',
  lead_user_id INTEGER REFERENCES users(id),
  deadline TEXT,
  r1_report_at TEXT,
  r2_report_at TEXT,
  r1_report_date TEXT,
  r2_report_date TEXT,
  capa_submitted_at TEXT,
  capa_return_note TEXT,
  notes TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS applications(
  id {PK},
  lab_id INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
  year_be INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  note TEXT,
  audit_id INTEGER REFERENCES audits(id) ON DELETE SET NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT,
  app_no TEXT,
  form TEXT,
  submitted_at TEXT,
  updated_at TEXT,
  review_note TEXT,
  reviewed_at TEXT,
  reviewed_by INTEGER,
  UNIQUE(lab_id, year_be)
);
CREATE TABLE IF NOT EXISTS app_files(
  id {PK},
  application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  filename TEXT,
  content_type TEXT,
  size INTEGER,
  uploaded_by INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_team(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  PRIMARY KEY(audit_id, user_id)
);
CREATE TABLE IF NOT EXISTS assignments(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  element INTEGER NOT NULL,
  user_id INTEGER REFERENCES users(id),
  PRIMARY KEY(audit_id, element)
);
CREATE TABLE IF NOT EXISTS responses(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  score TEXT,
  ftype TEXT,
  comment TEXT,
  followup TEXT,
  fnote TEXT,
  carried INTEGER DEFAULT 0,
  updated_by INTEGER,
  updated_at TEXT,
  PRIMARY KEY(audit_id, round, item_id)
);
CREATE TABLE IF NOT EXISTS capa(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  root_cause TEXT,
  action TEXT,
  owner TEXT,
  due_date TEXT,
  updated_by INTEGER,
  updated_at TEXT,
  PRIMARY KEY(audit_id, item_id)
);
CREATE TABLE IF NOT EXISTS attachments(
  id {PK},
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  round INTEGER NOT NULL DEFAULT 1,
  item_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'finding',
  path TEXT NOT NULL,
  filename TEXT,
  content_type TEXT,
  size INTEGER,
  uploaded_by INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS element_status(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  element INTEGER NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  done_by INTEGER,
  done_at TEXT,
  summary TEXT,
  PRIMARY KEY(audit_id, round, element)
);
CREATE TABLE IF NOT EXISTS reports(
  id {PK},
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  filename TEXT NOT NULL,
  path TEXT,
  created_by INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log(
  id {PK},
  audit_id INTEGER,
  user_id INTEGER,
  action TEXT NOT NULL,
  round INTEGER,
  item_id TEXT,
  detail TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS mail_log(
  id {PK},
  to_addr TEXT NOT NULL,
  subject TEXT,
  kind TEXT,
  status TEXT NOT NULL,
  error TEXT,
  user_id INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS ix_attach_item ON attachments(audit_id, item_id);
CREATE INDEX IF NOT EXISTS ix_log_audit ON audit_log(audit_id);
CREATE INDEX IF NOT EXISTS ix_audits_year ON audits(year_be);
"""

TABLES = ["users", "labs", "lab_members", "audits", "applications", "app_files", "audit_team", "assignments", "responses",
          "capa", "attachments", "element_status", "reports", "audit_log", "mail_log", "settings"]


def schema_sql(pg):
    s = SCHEMA.replace("{PK}", "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT")
    if pg:
        # Supabase เปิด API ให้ทุกตารางใน schema public — เปิด RLS โดยไม่มี policy เพื่อปิดการเข้าถึงผ่าน API
        # (แอปเชื่อมต่อด้วย connection string ของ postgres ซึ่งไม่ถูก RLS จำกัด)
        s += "\n" + "\n".join(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;" for t in TABLES)
    return s


DEFAULT_SETTINGS = {
    "org_name": "มหาวิทยาลัยนเรศวร",
    "activity_name": "กิจกรรมส่งเสริมการยกระดับห้องปฏิบัติการ ESPReL มหาวิทยาลัยนเรศวร",
    "team_title": "คณะผู้ตรวจประเมินตามมาตรฐานความปลอดภัย ESPReL มหาวิทยาลัยนเรศวร",
    "upgrade_form_url": "https://shorturl.nu.ac.th/orhhc3",
    "next_steps_r1": (
        "เมื่อได้รับรายงานสรุปผลตรวจประเมินความปลอดภัยห้องปฏิบัติการตามมาตรฐาน ESPReL Checklist "
        "และรายงานข้อบกพร่อง/ข้อสังเกตและข้อเสนอแนะ ครั้งที่ 1 ประจำปี {year} แล้ว "
        "ให้ห้องปฏิบัติการเข้าสู่ระบบ ESPReL MRA เพื่อตอบแผนการแก้ไข (CAPA) ของทุกข้อบกพร่องและข้อสังเกต "
        "พร้อมแนบหลักฐาน ภายในวันที่ {deadline} คณะผู้ตรวจประเมินจะเข้าตรวจติดตามครั้งที่ 2 "
        "เฉพาะข้อที่เป็นข้อบกพร่อง (NC) และข้อสังเกต (OBS) หลังจากห้องปฏิบัติการส่ง CAPA แล้ว"
    ),
    "next_steps_r2": "",
    "qr_image": "",
    "team_size": "3",
    "fm_return_note": ("กรุณาส่งกลับมาที่คุณวิภาดา บุญส่งแท้ กองการวิจัยและนวัตกรรม มหาวิทยาลัยนเรศวร "
                       "อีเมล wiphadab@nu.ac.th ภายในวันที่ 9 มกราคม 2569 หากมีข้อสงสัยติดต่อได้ที่ "
                       "โทรศัพท์ 05 596 8640 หรือ 083 539 5453"),
    "fm_revision": "FM-001 Rev.08",
    "fm_rev_date": "Date: 6/01/2568",
}


# --------------------------------------------------------------------------- adapter
class Result:
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _dict_factory(cursor, row):
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


_Q = re.compile(r"\?")


class DB:
    def __init__(self):
        if IS_PG:
            import psycopg
            from psycopg.rows import dict_row
            # prepare_threshold=None: จำเป็นกับ Supabase transaction pooler (port 6543)
            self.con = psycopg.connect(config.DATABASE_URL, row_factory=dict_row, prepare_threshold=None,
                                       connect_timeout=10)
        else:
            os.makedirs(os.path.dirname(config.SQLITE_PATH), exist_ok=True)
            self.con = sqlite3.connect(config.SQLITE_PATH, timeout=20)
            self.con.row_factory = _dict_factory
            self.con.execute("PRAGMA foreign_keys = ON")
            self.con.execute("PRAGMA busy_timeout = 20000")

    def execute(self, sql, params=()):
        if IS_PG:
            sql = _Q.sub("%s", sql)
            cur = self.con.execute(sql, tuple(params))
            rows = cur.fetchall() if cur.description else []
            return Result(rows, cur.rowcount)
        cur = self.con.execute(sql, tuple(params))
        rows = cur.fetchall() if cur.description else []
        return Result(rows, cur.rowcount)

    def insert(self, sql, params=()):
        """INSERT ... แล้วคืนค่า id ใหม่"""
        return self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()["id"]

    def commit(self):
        self.con.commit()

    def rollback(self):
        self.con.rollback()

    def close(self):
        try:
            self.con.close()
        except Exception:  # noqa
            pass


def get_db():
    if "db" not in g:
        if not IS_PG and _sqlite_missing():
            # ไฟล์ฐานข้อมูลถูกลบ/ย้ายระหว่างที่โปรแกรมทำงาน → สร้างตารางและบัญชีตั้งต้นใหม่ แทนที่จะเกิดข้อผิดพลาดทุกหน้า
            init_db(force=True)
        g.db = DB()
    return g.db


def _sqlite_missing():
    try:
        return os.path.getsize(config.SQLITE_PATH) == 0
    except OSError:
        return True


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        if _e is not None:
            try:
                db.rollback()
            except Exception:  # noqa
                pass
        db.close()


# คอลัมน์ที่เพิ่มภายหลัง — เพิ่มให้ฐานข้อมูลเดิมอัตโนมัติ
LATER_COLUMNS = [
    ("applications", "app_no", "TEXT"), ("applications", "form", "TEXT"),
    ("applications", "submitted_at", "TEXT"), ("applications", "updated_at", "TEXT"),
    ("labs", "department", "TEXT"), ("labs", "organization", "TEXT"), ("labs", "fax", "TEXT"),
    ("users", "must_change_pw", "INTEGER NOT NULL DEFAULT 0"),
    ("applications", "review_note", "TEXT"), ("applications", "reviewed_at", "TEXT"),
    ("applications", "reviewed_by", "INTEGER"),
]


def _ensure_columns(db):
    for table, col, typ in LATER_COLUMNS:
        if IS_PG:
            db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
        else:
            cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def init_db(force=False):
    """สร้างตาราง (SQLite อัตโนมัติ / Postgres เมื่อ AUTO_MIGRATE=1 หรือสั่ง flask init-db) และข้อมูลตั้งต้น"""
    db = DB()
    try:
        if force or config.AUTO_MIGRATE:
            if IS_PG:
                db.con.execute(schema_sql(True))
            else:
                db.con.executescript(schema_sql(False))
                try:
                    db.con.execute("PRAGMA journal_mode = DELETE")
                except sqlite3.DatabaseError:
                    pass
            _ensure_columns(db)
            db.commit()
        for k, v in DEFAULT_SETTINGS.items():
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))
        if not db.execute("SELECT id FROM users LIMIT 1").fetchone():
            db.execute("INSERT INTO users(username,password_hash,full_name,role,auth_source,created_at) VALUES(?,?,?,?,?,?)",
                       ("admin", hash_pw(config.ADMIN_INITIAL_PASSWORD), "ผู้ดูแลระบบ", "admin", "local", now()))
            if config.SEED_DEMO_USERS:
                demo = [("lead", "lead1234", "หัวหน้าผู้ตรวจประเมิน (ตัวอย่าง)", "lead", "คณะวิทยาศาสตร์"),
                        ("auditor1", "audit1234", "ผู้ตรวจประเมิน 1 (ตัวอย่าง)", "assessor", "คณะวิศวกรรมศาสตร์"),
                        ("auditor2", "audit1234", "ผู้ตรวจประเมิน 2 (ตัวอย่าง)", "assessor", "คณะแพทยศาสตร์"),
                        ("labuser", "lab1234", "หัวหน้าห้องปฏิบัติการ (ตัวอย่าง)", "lab",
                         "คณะเกษตรศาสตร์ ทรัพยากรธรรมชาติและสิ่งแวดล้อม")]
                for u, p, n, r, fac in demo:
                    db.execute("INSERT INTO users(username,password_hash,full_name,role,faculty,auth_source,created_at) "
                               "VALUES(?,?,?,?,?,?,?)", (u, hash_pw(p), n, r, fac, "local", now()))
        db.commit()
    finally:
        db.close()


def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else DEFAULT_SETTINGS.get(key, "")


def all_settings():
    s = dict(DEFAULT_SETTINGS)
    for r in get_db().execute("SELECT key,value FROM settings"):
        s[r["key"]] = r["value"]
    return s


def log(audit_id, action, round_=None, item_id=None, detail=None):
    """บันทึกประวัติการแก้ไข (audit trail)"""
    uid = g.user["id"] if getattr(g, "user", None) else None
    get_db().execute("INSERT INTO audit_log(audit_id,user_id,action,round,item_id,detail,created_at) VALUES(?,?,?,?,?,?,?)",
                     (audit_id, uid, action, round_, item_id, (detail or "")[:2000], now()))

-- ESPReL MRA schema (Supabase Postgres)
-- รันใน Supabase → SQL Editor ครั้งเดียว หรือ: supabase db push

CREATE TABLE IF NOT EXISTS users(
  id BIGSERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT,
  full_name TEXT NOT NULL,
  email TEXT,
  faculty TEXT,
  role TEXT NOT NULL DEFAULT 'assessor',
  auth_source TEXT NOT NULL DEFAULT 'local',
  active INTEGER NOT NULL DEFAULT 1,
  last_login TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS labs(
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  espre_no TEXT,
  faculty TEXT,
  affiliation TEXT,
  location TEXT,
  head_name TEXT,
  contact TEXT,
  phone TEXT,
  email TEXT,
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
  id BIGSERIAL PRIMARY KEY,
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
  id BIGSERIAL PRIMARY KEY,
  lab_id INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
  year_be INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  note TEXT,
  audit_id INTEGER REFERENCES audits(id) ON DELETE SET NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT,
  UNIQUE(lab_id, year_be)
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
  id BIGSERIAL PRIMARY KEY,
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
  id BIGSERIAL PRIMARY KEY,
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  filename TEXT NOT NULL,
  path TEXT,
  created_by INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log(
  id BIGSERIAL PRIMARY KEY,
  audit_id INTEGER,
  user_id INTEGER,
  action TEXT NOT NULL,
  round INTEGER,
  item_id TEXT,
  detail TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS ix_attach_item ON attachments(audit_id, item_id);
CREATE INDEX IF NOT EXISTS ix_log_audit ON audit_log(audit_id);
CREATE INDEX IF NOT EXISTS ix_audits_year ON audits(year_be);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE labs ENABLE ROW LEVEL SECURITY;
ALTER TABLE lab_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_team ENABLE ROW LEVEL SECURITY;
ALTER TABLE assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE capa ENABLE ROW LEVEL SECURITY;
ALTER TABLE attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE element_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

-- Storage bucket แบบ private สำหรับรูปหลักฐาน/รายงาน (เข้าถึงผ่านแอปด้วย service role เท่านั้น)
insert into storage.buckets (id, name, public) values ('esprel', 'esprel', false) on conflict (id) do nothing;

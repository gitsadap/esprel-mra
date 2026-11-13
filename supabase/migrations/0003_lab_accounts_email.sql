-- บัญชีห้องปฏิบัติการจากใบสมัคร (อีเมล + รหัสผ่านที่ระบบสร้าง) และประวัติการส่งอีเมล
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_pw INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS mail_log(
  id BIGSERIAL PRIMARY KEY,
  to_addr TEXT NOT NULL,
  subject TEXT,
  kind TEXT,
  status TEXT NOT NULL,
  error TEXT,
  user_id INTEGER,
  created_at TEXT
);
ALTER TABLE mail_log ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS ix_users_email ON users(lower(email));

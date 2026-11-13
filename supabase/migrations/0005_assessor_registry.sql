-- ทะเบียนผู้ตรวจประเมิน + ประวัติการอบรม (ESPReL / Peer)
CREATE TABLE IF NOT EXISTS assessors(
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  full_name TEXT NOT NULL,
  name_key TEXT,
  affiliation TEXT,
  faculty TEXT,
  org_type TEXT NOT NULL DEFAULT 'nu',
  email TEXT,
  phone TEXT,
  note TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS assessor_trainings(
  id BIGSERIAL PRIMARY KEY,
  assessor_id INTEGER NOT NULL REFERENCES assessors(id) ON DELETE CASCADE,
  course TEXT NOT NULL,
  year_be INTEGER,
  note TEXT,
  created_at TEXT,
  UNIQUE(assessor_id, course, year_be)
);
CREATE INDEX IF NOT EXISTS ix_assessor_user ON assessors(user_id);
ALTER TABLE assessors ENABLE ROW LEVEL SECURITY;
ALTER TABLE assessor_trainings ENABLE ROW LEVEL SECURITY;

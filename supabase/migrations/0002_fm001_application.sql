-- FM-001 ใบสมัครรับการตรวจประเมิน: คอลัมน์และตารางไฟล์แนบเพิ่มเติม
ALTER TABLE applications ADD COLUMN IF NOT EXISTS app_no TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS form TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS submitted_at TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS updated_at TEXT;
ALTER TABLE labs ADD COLUMN IF NOT EXISTS department TEXT;
ALTER TABLE labs ADD COLUMN IF NOT EXISTS organization TEXT;
ALTER TABLE labs ADD COLUMN IF NOT EXISTS fax TEXT;
CREATE TABLE IF NOT EXISTS app_files(
  id BIGSERIAL PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  filename TEXT,
  content_type TEXT,
  size INTEGER,
  uploaded_by INTEGER,
  created_at TEXT
);
ALTER TABLE app_files ENABLE ROW LEVEL SECURITY;

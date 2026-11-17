-- ผู้สังเกตการณ์รายการตรวจ (ดูผล + บันทึกความเห็น ไม่ให้คะแนน)
CREATE TABLE IF NOT EXISTS audit_observers(
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT,
  PRIMARY KEY(audit_id, user_id)
);
CREATE TABLE IF NOT EXISTS observer_notes(
  id BIGSERIAL PRIMARY KEY,
  audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  round INTEGER NOT NULL DEFAULT 1,
  element INTEGER,
  body TEXT NOT NULL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_observer_user ON audit_observers(user_id);
CREATE INDEX IF NOT EXISTS ix_observer_notes ON observer_notes(audit_id);
ALTER TABLE audit_observers ENABLE ROW LEVEL SECURITY;
ALTER TABLE observer_notes ENABLE ROW LEVEL SECURITY;

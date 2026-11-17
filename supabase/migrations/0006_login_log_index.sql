-- นับการเข้าสู่ระบบผิด (จำกัดการเดารหัสผ่าน)
CREATE INDEX IF NOT EXISTS ix_log_action ON audit_log(action, created_at);

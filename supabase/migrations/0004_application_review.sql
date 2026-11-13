-- เหตุผลการไม่อนุมัติ/ส่งกลับใบสมัคร (แจ้งห้องปฏิบัติการ)
ALTER TABLE applications ADD COLUMN IF NOT EXISTS review_note TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS reviewed_at TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS reviewed_by INTEGER;

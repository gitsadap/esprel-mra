"""ส่งอีเมลผ่าน SMTP (ของมหาวิทยาลัย/หน่วยงาน)

- ไม่ตั้งค่า SMTP_HOST → ไม่ส่งจริง เก็บไว้ใน OUTBOX (หน่วยความจำ) สำหรับโหมดทดสอบ/ชุดทดสอบ
- บันทึกประวัติการส่งใน mail_log (ไม่เก็บเนื้อหาอีเมล เพราะอาจมีรหัสผ่าน)
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import config

OUTBOX = []          # โหมดทดสอบ: [{to, subject, body}]


def enabled():
    return bool(config.SMTP_HOST)


def _log(to, subject, kind, status, error=None, user_id=None):
    try:
        from db import get_db, now
        db = get_db()
        db.execute("""INSERT INTO mail_log(to_addr,subject,kind,status,error,user_id,created_at) VALUES(?,?,?,?,?,?,?)""",
                   (to, subject[:300], kind, status, (error or "")[:500] or None, user_id, now()))
        db.commit()
    except Exception:  # noqa: BLE001 — การบันทึกประวัติต้องไม่ทำให้งานหลักล้ม
        pass


def send(to, subject, body, kind="general", user_id=None, reply_to=None):
    """ส่งอีเมลข้อความธรรมดา คืน (status, error)  status = sent | dev | failed"""
    to = (to or "").strip()
    if not to or "@" not in to:
        return "failed", "ไม่มีอีเมลผู้รับ"
    if not enabled():
        OUTBOX.append({"to": to, "subject": subject, "body": body, "kind": kind})
        del OUTBOX[:-50]
        _log(to, subject, kind, "dev", "SMTP ยังไม่ได้ตั้งค่า", user_id)
        return "dev", None
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if config.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
            if config.SMTP_SECURITY == "starttls":
                server.starttls(context=ctx)
        with server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
            server.send_message(msg)
        _log(to, subject, kind, "sent", None, user_id)
        return "sent", None
    except (OSError, smtplib.SMTPException) as ex:
        _log(to, subject, kind, "failed", str(ex), user_id)
        return "failed", str(ex)

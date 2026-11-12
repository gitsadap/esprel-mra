"""ที่เก็บไฟล์ (รูปหลักฐาน, รายงาน, QR): Supabase Storage (bucket แบบ private) หรือโฟลเดอร์ในเครื่องตอนพัฒนา
ทุกไฟล์ดาวน์โหลดผ่านแอปซึ่งตรวจสิทธิ์ก่อนเสมอ"""
import os

import config

USE_SUPABASE = bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)
LOCAL_DIR = os.path.join(config.INSTANCE, "files")


def _headers(extra=None):
    h = {"Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}", "apikey": config.SUPABASE_SERVICE_KEY}
    if extra:
        h.update(extra)
    return h


def _safe(path):
    path = path.replace("\\", "/").lstrip("/")
    if ".." in path.split("/"):
        raise ValueError("invalid path")
    return path


def save(path, data, content_type="application/octet-stream"):
    path = _safe(path)
    if USE_SUPABASE:
        import requests
        url = f"{config.SUPABASE_URL}/storage/v1/object/{config.STORAGE_BUCKET}/{path}"
        r = requests.post(url, data=data, headers=_headers({"Content-Type": content_type, "x-upsert": "true"}),
                          timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"อัปโหลดไฟล์ไม่สำเร็จ ({r.status_code}): {r.text[:200]}")
        return path
    full = os.path.join(LOCAL_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return path


def load(path):
    path = _safe(path)
    if USE_SUPABASE:
        import requests
        url = f"{config.SUPABASE_URL}/storage/v1/object/{config.STORAGE_BUCKET}/{path}"
        r = requests.get(url, headers=_headers(), timeout=30)
        if r.status_code >= 300:
            raise FileNotFoundError(path)
        return r.content
    with open(os.path.join(LOCAL_DIR, path), "rb") as f:
        return f.read()


def signed_url(path, expires=3600):
    """URL ชั่วคราวสำหรับแสดงรูป (เฉพาะ Supabase) — คืน None เมื่อเก็บไฟล์ในเครื่อง"""
    if not USE_SUPABASE:
        return None
    import requests
    path = _safe(path)
    url = f"{config.SUPABASE_URL}/storage/v1/object/sign/{config.STORAGE_BUCKET}/{path}"
    r = requests.post(url, json={"expiresIn": expires}, headers=_headers(), timeout=15)
    if r.status_code >= 300:
        return None
    return f"{config.SUPABASE_URL}/storage/v1{r.json().get('signedURL')}"


def delete(path):
    path = _safe(path)
    if USE_SUPABASE:
        import requests
        url = f"{config.SUPABASE_URL}/storage/v1/object/{config.STORAGE_BUCKET}"
        requests.delete(url, json={"prefixes": [path]}, headers=_headers(), timeout=15)
        return
    try:
        os.remove(os.path.join(LOCAL_DIR, path))
    except OSError:
        pass

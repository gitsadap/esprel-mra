"""ทะเบียนผู้ตรวจประเมิน: อ่านไฟล์ Excel รายชื่อผู้อบรม (ESPReL / Peer) และจัดรูปข้อมูล

รูปแบบไฟล์ที่รองรับ (ปรับตามหัวคอลัมน์อัตโนมัติ ไม่ต้องเรียงคอลัมน์ตายตัว):
  แถวหัวตาราง: ลำดับที่ | รายชื่อ | สังกัด | อีเมล์ | เบอร์โทรศัพท์ | ESPReL | Peer | หมายเหตุ
  - คอลัมน์ ESPReL / Peer ที่มีเครื่องหมาย (/, ✓, x, 1, มี) = ผ่านการอบรมหลักสูตรนั้น
  - ชีตที่ไม่มีคอลัมน์หลักสูตร แต่ชื่อชีต/หัวเรื่องมีคำว่า Peer หรือ ESPReL = ทุกคนในชีตผ่านหลักสูตรนั้น
  - ปี พ.ศ. อ่านจากหัวเรื่อง/ชื่อชีต/ชื่อไฟล์ (เช่น "Peer ปี2569") ถ้าไม่มี ใช้ปีที่ระบุตอนนำเข้า
"""
import re

COURSES = {"ESPReL": "อบรมมาตรฐานความปลอดภัยห้องปฏิบัติการ (ESPReL)",
           "Peer": "อบรมผู้ตรวจประเมิน (Peer evaluation)"}
TITLE_RE = re.compile(
    r"^((ศาสตราจารย์|รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|ศ\.|รศ\.|ผศ\.|อาจารย์|อ\.|ดร\.|"
    r"นายแพทย์|แพทย์หญิง|นพ\.|พญ\.|ทันตแพทย์หญิง|ทันตแพทย์|ทพ\.|ทพญ\.|เภสัชกรหญิง|เภสัชกร|ภก\.|ภญ\.|"
    r"นางสาว|น\.ส\.|นาย|นาง|คุณ)\s*)+")
NU_UNITS = ("คณะ", "กอง", "วิทยาลัย", "สถาบัน", "สำนัก", "บัณฑิตวิทยาลัย", "โรงพยาบาล", "อุทยาน")


def clean(s):
    return re.sub(r"\s+", " ", str(s or "").replace("​", "")).strip()


def strip_titles(name):
    return TITLE_RE.sub("", clean(name)).strip()


def name_key(name):
    """คีย์จับคู่ชื่อซ้ำ: ตัดคำนำหน้า/ตำแหน่ง และช่องว่างทั้งหมด"""
    return re.sub(r"\s+", "", strip_titles(name)).lower()


def faculty_of(affiliation):
    """ชื่อคณะ/หน่วยงาน (ตัด 'มหาวิทยาลัยนเรศวร' ท้ายชื่อ) ใช้กันผลประโยชน์ทับซ้อนเวลาจัดทีม"""
    a = clean(affiliation)
    a = re.sub(r"\s*มหาวิทยาลัยนเรศวร\s*$", "", a)
    return a or None


def org_type(affiliation, email):
    a, e = clean(affiliation), clean(email).lower()
    if e.endswith("@nu.ac.th") or "นเรศวร" in a:
        return "nu"
    if "มหาวิทยาลัย" in a or "กรม" in a or a.startswith("ศูนย์") or ".go.th" in e:
        return "external"
    if a.startswith(NU_UNITS):
        return "nu"
    return "external" if a else "nu"


def ldap_username(email):
    """บัญชี NU (LDAP) = ส่วนหน้าของอีเมล @nu.ac.th"""
    e = clean(email).lower()
    return e.split("@")[0] if e.endswith("@nu.ac.th") else None


def clean_phone(p):
    p = clean(p)
    digits = re.sub(r"\D", "", p)
    if len(digits) == 10 and digits.startswith("0") and digits[1] in "689":
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return p or None


def _truthy(v):
    s = clean(v).lower()
    return bool(s) and s not in ("-", "0", "no", "ไม่", "ไม่มี", "x̶", "false")


def _year(text):
    m = re.search(r"(25[6-9]\d)", text or "")
    return int(m.group(1)) if m else None


def _course_in(text):
    t = (text or "").lower()
    return [c for c in COURSES if c.lower() in t]


def parse_workbook(fileobj, filename="", default_year=None):
    """อ่านทุกชีต คืน (records, warnings)
    record = {full_name, affiliation, faculty, org_type, email, phone, note, trainings:{(course, year)}, sources:[...]}
    รายชื่อซ้ำระหว่างชีต (อีเมลหรือชื่อเดียวกัน) รวมเป็นคนเดียว"""
    import openpyxl
    wb = openpyxl.load_workbook(fileobj, data_only=True, read_only=True)
    people, warns = [], []
    by_email, by_name = {}, {}
    file_year = _year(filename)
    for ws in wb.worksheets:
        rows = [[clean(c) for c in r] for r in ws.iter_rows(values_only=True)]
        hdr_i = next((i for i, r in enumerate(rows[:15]) if any(c in ("รายชื่อ", "ชื่อ-สกุล", "ชื่อ - สกุล", "ชื่อ") for c in r)), None)
        if hdr_i is None:
            continue
        hdr = rows[hdr_i]
        title = " ".join(" ".join(c for c in r if c) for r in rows[:hdr_i]) + " " + ws.title

        def col(*names, hdr=hdr):
            for i, h in enumerate(hdr):
                hl = h.lower()
                if any(n.lower() in hl for n in names):
                    return i
            return None
        c_name = col("รายชื่อ", "ชื่อ")
        c_aff, c_mail = col("สังกัด", "หน่วยงาน"), col("อีเมล", "e-mail", "email")
        c_tel, c_note = col("โทร"), col("หมายเหตุ")
        c_courses = {c: col(c) for c in COURSES if col(c) is not None}
        sheet_courses = [] if c_courses else _course_in(title)
        year = _year(title) or file_year or default_year
        for r in rows[hdr_i + 1:]:
            def get(i, r=r):
                return r[i] if i is not None and i < len(r) else ""
            name = get(c_name)
            if not name or name in ("รายชื่อ",):
                continue
            rec = {"full_name": name, "affiliation": get(c_aff), "email": get(c_mail).lower(), "phone": clean_phone(get(c_tel)),
                   "note": get(c_note) or None, "trainings": set(), "sources": [ws.title]}
            for c, i in c_courses.items():
                if _truthy(get(i)):
                    rec["trainings"].add((c, year))
            for c in sheet_courses:
                rec["trainings"].add((c, year))
            nk = name_key(name)
            ex = by_email.get(rec["email"]) if rec["email"] else None
            ex = ex or by_name.get(nk)
            if ex:
                if ex["email"] and rec["email"] and ex["email"] != rec["email"]:
                    warns.append(f"{name}: อีเมลต่างกันระหว่างชีต ({ex['email']} / {rec['email']}) — ใช้ {ex['email']}")
                ex["trainings"] |= rec["trainings"]
                ex["sources"].append(ws.title)
                for k in ("affiliation", "email", "phone", "note"):
                    if not ex.get(k) and rec.get(k):
                        ex[k] = rec[k]
                if len(rec["affiliation"]) > len(ex["affiliation"] or ""):   # ชื่อสังกัดที่ครบกว่า
                    ex["affiliation"] = rec["affiliation"]
                continue
            people.append(rec)
            if rec["email"]:
                by_email[rec["email"]] = rec
            by_name[nk] = rec
    for p in people:
        p["faculty"] = faculty_of(p["affiliation"])
        p["org_type"] = org_type(p["affiliation"], p["email"])
        if not p["email"]:
            warns.append(f"{p['full_name']}: ไม่มีอีเมล")
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", p["email"]):
            warns.append(f"{p['full_name']}: อีเมลไม่ถูกต้อง ({p['email']})")
        elif p["org_type"] == "nu" and not p["email"].endswith("@nu.ac.th"):
            warns.append(f"{p['full_name']}: บุคลากร มน. แต่ใช้อีเมลภายนอก ({p['email']}) — "
                         "ถ้าจะให้เข้าระบบด้วยบัญชี NU ให้แก้เป็นอีเมล @nu.ac.th")
        if not p["trainings"]:
            warns.append(f"{p['full_name']}: ไม่พบข้อมูลการอบรม")
    return people, warns

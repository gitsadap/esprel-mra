"""แบบฟอร์ม FM-001 ใบสมัครรับการตรวจประเมินความปลอดภัยในห้องปฏิบัติการ (ESPReL Checklist) Rev.08"""
import json

ACTIVITIES = [
    ("research", "การวิจัย"),
    ("teaching", "การเรียนการสอน"),
    ("testing", "การวิเคราะห์ทดสอบ"),
    ("innovation", "นวัตกรรม"),
    ("invention", "สิ่งประดิษฐ์"),
    ("production", "การผลิตสินค้า"),
    ("other", "อื่น ๆ"),
]
COMMITMENTS = [
    "สนับสนุนการดำเนินกิจกรรมต่าง ๆ เพื่อให้บรรลุผลของโครงการฯ",
    "ส่งบุคลากรเข้าร่วมอบรมเชิงปฏิบัติการ เรื่องมาตรฐานความปลอดภัยห้องปฏิบัติการ (ESPReL) "
    "และหลักสูตรการอบรมอื่น ๆ ที่เกี่ยวข้อง",
    "ยินดีเข้าร่วมส่งผลงาน Poster Presentation เพื่อรับการพิจารณาผลการดำเนินงานยกระดับความปลอดภัยห้องปฏิบัติการ"
    "ในงานประชุมวิชาการเครือข่ายภูมิภาคและ/หรือระดับชาติ ที่จัดโดยสำนักงานการวิจัยแห่งชาติ (วช.)",
    "ร่วมพัฒนาเพื่อยกระดับความปลอดภัยห้องปฏิบัติการอย่างต่อเนื่อง",
]
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
               "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
NRCT_REGISTER_URL = "https://labsafety.nrct.go.th/esprel/memgroup/createpublic"
NRCT_MANUAL_URL = "https://labsafety.nrct.go.th/articles/view/khumux-kar-chi-ngan-websit-esprel-checklist-sahrab-phu-chi-ngan-2"
FILE_KINDS = {"survey": "ผลการสำรวจสภาพความปลอดภัย (ESPReL Checklist) จากระบบ วช.",
              "signed": "ใบสมัคร FM-001 ที่ลงนามโดยผู้มีอำนาจลงนามแล้ว",
              "other": "เอกสารประกอบอื่น ๆ"}
# ช่องที่เก็บในตาราง labs (ข้อ 1–7)
LAB_FIELDS = ["name", "department", "faculty", "organization", "phone", "fax", "head_name", "espre_no"]


def load(app_row):
    try:
        return json.loads(app_row["form"] or "{}") if app_row else {}
    except (TypeError, ValueError):
        return {}


def blank_scores():
    return {str(i): {"score": "", "pct": ""} for i in range(1, 8)} | {"total": {"score": "", "pct": ""}}


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse(form):
    """อ่านค่าจากฟอร์ม HTML → dict ที่เก็บเป็น JSON"""
    g = lambda k: (form.get(k) or "").strip()  # noqa: E731
    d = {k: g(k) for k in LAB_FIELDS}
    d.update({
        "first_time": g("first_time") or "1",
        "last_assessed": g("last_assessed"),
        "activities": [k for k, _ in ACTIVITIES if form.get("act_" + k)],
        "activities_other": g("activities_other"),
        "scope": g("scope"),
        "staff_count": g("staff_count"),
        "certified": g("certified"),
        "cert_detail": g("cert_detail"),
        "survey_month": g("survey_month"),
        "survey_year": g("survey_year"),
        "coord_name": g("coord_name"), "coord_position": g("coord_position"),
        "coord_phone": g("coord_phone"), "coord_fax": g("coord_fax"),
        "coord_email": g("coord_email"), "coord_line": g("coord_line"),
        "commits": [i for i in range(1, len(COMMITMENTS) + 1) if form.get(f"commit_{i}")],
        "signer_name": g("signer_name"), "signer_position": g("signer_position"),
        "year_be": g("year_be"),
    })
    scores = blank_scores()
    tot = 0.0
    have = False
    for i in range(1, 8):
        s, p = g(f"score_{i}"), g(f"pct_{i}")
        scores[str(i)] = {"score": s, "pct": p}
        if _num(s) is not None:
            tot += _num(s)
            have = True
    scores["total"] = {"score": (f"{tot:g}" if have else ""), "pct": g("pct_total")}
    d["scores"] = scores
    return d


def validate(d, n_survey_files):
    """ตรวจความครบถ้วนก่อนส่งใบสมัคร คืนรายการข้อความที่ยังขาด"""
    err = []
    req = [("name", "1. ชื่อห้องปฏิบัติการ"), ("faculty", "3. คณะ/สถาบัน/ศูนย์/หน่วยงาน"),
           ("organization", "4. มหาวิทยาลัย/องค์กร"), ("phone", "5. โทรศัพท์"),
           ("head_name", "6. ชื่อหัวหน้าห้องปฏิบัติการ"), ("espre_no", "7. เลขทะเบียนห้องปฏิบัติการ (ESPReL)"),
           ("scope", "9. ขอบเขตสถานที่ในการตรวจประเมิน"), ("staff_count", "10. จำนวนบุคลากร"),
           ("survey_month", "12. เดือนที่สำรวจครั้งล่าสุด"), ("survey_year", "12. ปี พ.ศ. ที่สำรวจครั้งล่าสุด"),
           ("coord_name", "13. ชื่อผู้ประสานงาน"), ("coord_phone", "13. โทรศัพท์ผู้ประสานงาน"),
           ("coord_email", "13. อีเมลผู้ประสานงาน"),
           ("signer_name", "ชื่อผู้มีอำนาจลงนาม"), ("signer_position", "ตำแหน่งผู้มีอำนาจลงนาม")]
    for k, label in req:
        if not d.get(k):
            err.append(label)
    if d.get("first_time") == "0" and not d.get("last_assessed"):
        err.append("วันที่ขอรับการตรวจประเมินครั้งล่าสุด")
    if not d.get("activities"):
        err.append("8. การดำเนินงานของห้องปฏิบัติการ (เลือกอย่างน้อย 1 ข้อ)")
    if "other" in d.get("activities", []) and not d.get("activities_other"):
        err.append("8. อธิบายการดำเนินงานอื่น ๆ")
    if d.get("staff_count") and (_num(d["staff_count"]) is None or _num(d["staff_count"]) < 0):
        err.append("10. จำนวนบุคลากรต้องเป็นตัวเลข")
    if d.get("certified") not in ("0", "1"):
        err.append("11. การรับรองมาตรฐาน")
    if d.get("certified") == "1" and not d.get("cert_detail"):
        err.append("11. ชื่อมาตรฐานและหน่วยงานที่รับรอง")
    for i in range(1, 8):
        sc = d["scores"][str(i)]
        if _num(sc["score"]) is None or _num(sc["pct"]) is None:
            err.append(f"12. คะแนนและร้อยละขององค์ประกอบที่ {i}")
            break
    if len(d.get("commits", [])) < len(COMMITMENTS):
        err.append("คำรับรอง (ต้องยอมรับครบทุกข้อ)")
    if n_survey_files < 1:
        err.append("ไฟล์แนบผลการสำรวจสภาพความปลอดภัย (ESPReL Checklist) จากระบบ วช.")
    return err


def example_form(name, faculty, espre_no="", **over):
    """ข้อมูลใบสมัครที่กรอกครบ (ใช้ในชุดทดสอบและข้อมูลตัวอย่าง)"""
    d = {"name": name, "faculty": faculty, "espre_no": espre_no or "2-0100-0000-0", "department": "",
         "organization": "มหาวิทยาลัยนเรศวร", "phone": "0 5596 2700", "fax": "", "head_name": "หัวหน้าห้องปฏิบัติการ",
         "first_time": "1", "act_research": "1", "act_teaching": "1", "scope": f"{name} ชั้น 3",
         "staff_count": "12", "certified": "0", "survey_month": "ตุลาคม", "survey_year": "2569",
         "coord_name": "ผู้ประสานงาน", "coord_position": "นักวิทยาศาสตร์", "coord_phone": "081 000 0000",
         "coord_email": "coordinator@example.org", "signer_name": "ผู้มีอำนาจลงนาม", "signer_position": "คณบดี",
         "year_be": "2570", "action": "submit"}
    for i, (sc, p) in enumerate([(30, 75), (60, 70), (25, 80), (70, 72), (40, 66), (12, 80), (10, 83)], 1):
        d[f"score_{i}"], d[f"pct_{i}"] = str(sc), str(p)
    d["pct_total"] = "72.5"
    for i in range(1, len(COMMITMENTS) + 1):
        d[f"commit_{i}"] = "1"
    d.update(over)
    return d

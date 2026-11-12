"""อ่านแบบประเมิน ESPReL (Excel) -> โครงสร้าง checklist และคะแนน/ข้อเสนอแนะ
ใช้ทั้งตอนสร้าง data/checklist.json และตอนนำเข้าไฟล์ Excel ของห้องปฏิบัติการ"""
import re
import openpyxl

ELEMENT_TITLES = {
    1: "การบริหารระบบการจัดการด้านความปลอดภัย",
    2: "ระบบการจัดการสารเคมี",
    3: "ระบบการจัดการของเสีย",
    4: "ลักษณะทางกายภาพของห้องปฏิบัติการ อุปกรณ์และเครื่องมือ",
    5: "ระบบการป้องกันและแก้ไขภัยอันตราย",
    6: "การให้ความรู้พื้นฐานเกี่ยวกับด้านความปลอดภัยในห้องปฏิบัติการ",
    7: "การจัดการข้อมูลและเอกสาร",
}
ELEMENT_SHORT = {1: "บริหารความปลอดภัย", 2: "จัดการสารเคมี", 3: "จัดการของเสีย",
                 4: "กายภาพ/อุปกรณ์", 5: "ป้องกันภัยอันตราย", 6: "ให้ความรู้", 7: "ข้อมูลและเอกสาร"}

CODE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s*")


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _max_from_formula(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    m = re.search(r",\s*(\d+)\s*\)\s*\)\s*$", s)
    if m and "IF(" in s.upper():
        return int(m.group(1))
    try:
        return int(float(s))
    except ValueError:
        return None


def element_sheets(wb):
    out = {}
    for name in wb.sheetnames:
        m = re.match(r"^คะแนนองค์\s*(\d)$", name.strip())
        if m:
            out[int(m.group(1))] = wb[name]
    return out


def comment_cols(ws):
    """หา column ข้อเสนอแนะ ครั้งที่ 1 และ 2 (บางชีทมีคอลัมน์เกิน)"""
    heads = []
    for r in (1, 2):
        for c in range(7, ws.max_column + 1):
            v = ws.cell(r, c).value
            if v and "ข้อเสนอแนะ" in str(v):
                heads.append((c, str(v)))
    heads.sort()
    c1 = next((c for c, t in heads if "1" in t), 7)
    c2 = next((c for c, t in heads if "2" in t and c > c1), c1 + 1)
    return c1, c2


def parse_structure(path):
    wb = openpyxl.load_workbook(path)  # formulas
    sheets = element_sheets(wb)
    elements = []
    for e in range(1, 8):
        ws = sheets[e]
        nodes = []
        section = None
        last_code = str(e)
        for r in range(3, ws.max_row + 1):
            b = _clean(ws.cell(r, 2).value)
            if not b:
                continue
            if b.startswith("รวม"):
                break
            cval = ws.cell(r, 3).value
            cstr = str(cval or "")
            is_sum = "SUM(" in cstr.upper()
            mx = None if is_sum else _max_from_formula(cval)
            m = CODE_RE.match(b)
            code = m.group(1) if m else None
            depth = code.count(".") if code else None
            if code and depth == 0:
                continue
            # element title row (e.g. "1. การบริหาร...")
            if re.match(r"^\d\.\s", b) and not code:
                continue
            if code and depth == 1:  # section e.g. 2.1
                section = {"code": code, "title": CODE_RE.sub("", b), "row": r}
                nodes.append({"kind": "section", "row": r, "code": code,
                              "text": CODE_RE.sub("", b), "id": f"{e}-{r}"})
                last_code = code
                if mx is not None:  # section that is itself a scored item (6.1, 6.2)
                    nodes.append({"kind": "item", "row": r, "code": code, "text": CODE_RE.sub("", b),
                                  "max": mx, "id": f"{e}-{r}", "section": code, "self": True})
                continue
            if code:
                last_code = code
            if mx is not None:
                nodes.append({"kind": "item", "row": r, "code": code,
                              "text": CODE_RE.sub("", b) if code else b.lstrip("-– ").strip(),
                              "bullet": not code, "parent": last_code,
                              "max": mx, "id": f"{e}-{r}", "section": section["code"] if section else str(e)})
            else:
                kind = "option" if b.startswith("-") else "heading"
                nodes.append({"kind": kind, "row": r, "code": code,
                              "text": CODE_RE.sub("", b) if code else b.lstrip("-– ").strip(),
                              "parent": last_code, "id": f"{e}-{r}",
                              "section": section["code"] if section else str(e)})
        sections = [n for n in nodes if n["kind"] == "section"]
        elements.append({"no": e, "title": ELEMENT_TITLES[e], "short": ELEMENT_SHORT[e],
                         "sections": [{"code": s["code"], "title": s["text"]} for s in sections],
                         "nodes": nodes})
    return elements


def _score_val(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip().upper().replace("/", "").replace(" ", "")
    if s in ("NA", "N.A.", "N.A"):
        return "NA"
    try:
        return str(int(float(s)))
    except ValueError:
        return None


def parse_lab_workbook(path, checklist):
    """อ่านข้อมูลห้องปฏิบัติการ + คะแนน 2 รอบ + ข้อเสนอแนะ จากไฟล์ Excel แบบเดียวกับต้นแบบ"""
    wb = openpyxl.load_workbook(path, data_only=True)
    info = {}
    if "สรุปคะแนน" in wb.sheetnames:
        ws = wb["สรุปคะแนน"]
        def after_colon(v):
            v = _clean(v)
            v = re.sub(r"^\d\)\s*", "", v)
            return v.split(":", 1)[1].strip() if ":" in v else re.sub(r"^.*\(ESPReL no\.\)\s*", "", v)
        info["name"] = after_colon(ws["A1"].value)
        info["espre_no"] = _clean(re.sub(r"^.*\(ESPReL no\.\)\s*", "", _clean(ws["A2"].value)))
        info["affiliation"] = after_colon(ws["A3"].value)
        r1 = _clean(ws["B8"].value or ws["B51"].value)
        r2 = _clean(ws["E8"].value or ws["E51"].value)
        info["r1_date_text"] = r1.replace("ครั้งที่ 1", "").strip()
        info["r2_date_text"] = r2.replace("ครั้งที่ 2", "").strip()
    sheets = element_sheets(wb)
    responses = {1: {}, 2: {}}
    for el in checklist:
        ws = sheets.get(el["no"])
        if ws is None:
            continue
        c1, c2 = comment_cols(ws)
        nodes = el["nodes"]
        [n["id"] for n in nodes if n["kind"] == "item"]
        last_item = None
        pending_heading_comments = {1: [], 2: []}
        for n in nodes:
            r = n["row"]
            g1 = _clean(ws.cell(r, c1).value)
            g2 = _clean(ws.cell(r, c2).value)
            g1 = "" if g1 in ("-", "") else g1
            g2 = "" if g2 in ("-", "") else g2
            if n["kind"] == "item":
                d = _score_val(ws.cell(r, 4).value)
                f = _score_val(ws.cell(r, 6).value)
                responses[1][n["id"]] = {"score": d, "comment": g1}
                responses[2][n["id"]] = {"score": f, "comment": g2}
                for rnd in (1, 2):
                    if pending_heading_comments[rnd]:
                        c = responses[rnd][n["id"]]
                        c["comment"] = "\n".join(pending_heading_comments[rnd] + ([c["comment"]] if c["comment"] else []))
                        pending_heading_comments[rnd] = []
                last_item = n["id"]
            elif n["kind"] == "item" or (not g1 and not g2):
                continue
            else:
                # comment on a label row: options attach to previous item; headings to next item
                for rnd, g in ((1, g1), (2, g2)):
                    if not g:
                        continue
                    if n["kind"] == "option" and last_item:
                        c = responses[rnd][last_item]
                        c["comment"] = (c["comment"] + "\n" + g).strip()
                    else:
                        pending_heading_comments[rnd].append(g)
    return info, responses

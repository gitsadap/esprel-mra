"""จัดทีมผู้ตรวจประเมินอัตโนมัติจากจำนวนห้องที่สมัครในปีนั้น
หลักการ: (1) ไม่ให้ผู้ตรวจตรวจห้องในคณะ/หน่วยงานเดียวกับตน (ผลประโยชน์ทับซ้อน)
         (2) กระจายภาระงานให้เท่ากันที่สุด  (3) ทุกทีมมีหัวหน้าผู้ตรวจ 1 คน
         (4) แบ่ง 7 องค์ประกอบในทีมตามจำนวนข้อให้ใกล้เคียงกัน"""
from scoring import CHECKLIST


def _norm(s):
    return (s or "").strip().lower()


def conflict(user, lab):
    uf, lf = _norm(user.get("faculty")), _norm(lab.get("faculty") or lab.get("affiliation"))
    return bool(uf) and bool(lf) and (uf in lf or lf in uf)


def split_elements(team_ids):
    """แบ่งองค์ประกอบ 1–7 ให้สมาชิกทีม โดยถ่วงตามจำนวนข้อ"""
    if not team_ids:
        return {}
    sizes = {e["no"]: sum(1 for n in e["nodes"] if n["kind"] == "item") for e in CHECKLIST}
    load = dict.fromkeys(team_ids, 0)
    out = {}
    for el in sorted(sizes, key=lambda e: -sizes[e]):
        u = min(team_ids, key=lambda x: (load[x], team_ids.index(x)))
        out[el] = u
        load[u] += sizes[el]
    return dict(sorted(out.items()))


def propose(labs, assessors, team_size=3, existing_load=None):
    """labs: [{id, name, faculty}], assessors: [{id, full_name, faculty, role}]
    คืน (proposals, load) — proposals: [{lab, lead, members, elements, warnings}]"""
    load = {a["id"]: 0 for a in assessors}
    if existing_load:
        for k, v in existing_load.items():
            if k in load:
                load[k] += v
    leads = [a for a in assessors if a["role"] in ("lead", "admin")]
    if not leads:
        leads = list(assessors)
    proposals = []
    for lab in sorted(labs, key=lambda x: (_norm(x.get("faculty")), x["name"])):
        warnings = []
        ok_leads = [a for a in leads if not conflict(a, lab)]
        if not ok_leads:
            ok_leads = leads
            if leads:
                warnings.append("หัวหน้าผู้ตรวจทุกคนอยู่คณะเดียวกับห้องนี้")
        lead = min(ok_leads, key=lambda a: (load[a["id"]], a["id"])) if ok_leads else None
        team = [lead] if lead else []
        if lead:
            load[lead["id"]] += 1
        pool = [a for a in assessors if a not in team and not conflict(a, lab)]
        while len(team) < team_size and pool:
            m = min(pool, key=lambda a: (load[a["id"]], a["role"] != "assessor", a["id"]))
            team.append(m)
            load[m["id"]] += 1
            pool.remove(m)
        if len(team) < team_size:
            warnings.append(f"ผู้ตรวจที่ไม่มีผลประโยชน์ทับซ้อนไม่พอ (ได้ {len(team)}/{team_size} คน)")
        ids = [t["id"] for t in team]
        proposals.append({"lab": lab, "lead": lead, "members": team, "elements": split_elements(ids),
                          "warnings": warnings})
    return proposals, load

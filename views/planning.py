"""ใบสมัคร & จัดทีมผู้ตรวจ, ไม่อนุมัติพร้อมเหตุผล, ทะเบียนผู้ตรวจประเมิน"""
import io

from flask import abort, flash, g, redirect, render_template, request, url_for

import assessors as assessors_mod
import db as dbm
from core import EMAIL_RE, gen_password, mail_credentials, roles, sync_nu_accounts
from db import get_db, hash_pw, log, now
from planner import propose
from views.audits import save_team, staff_users
from views.lab import mail_review


# ---------- ทะเบียนผู้ตรวจประเมิน
def _assessor_rows(where="", args=()):
    db = get_db()
    rows = [dict(r) for r in db.execute(
        f"""SELECT a.*, u.username, u.role user_role, u.auth_source, u.last_login, u.active user_active
            FROM assessors a LEFT JOIN users u ON u.id=a.user_id {where}
            ORDER BY a.active DESC, a.org_type DESC, a.faculty, a.full_name""", args).fetchall()]
    tr = {}
    for t in db.execute("SELECT * FROM assessor_trainings ORDER BY year_be, course").fetchall():
        tr.setdefault(t["assessor_id"], []).append(dict(t))
    work = {}
    for w in db.execute("""SELECT t.user_id, COUNT(DISTINCT t.audit_id) n, MAX(a.year_be) last_year
                           FROM audit_team t JOIN audits a ON a.id=t.audit_id GROUP BY t.user_id""").fetchall():
        work[w["user_id"]] = w
    for r in rows:
        r["trainings"] = tr.get(r["id"], [])
        r["courses"] = {t["course"] for t in r["trainings"]}
        w = work.get(r["user_id"])
        r["n_audits"] = w["n"] if w else 0
        r["last_audit_year"] = w["last_year"] if w else None
    return rows


def _add_training(aid, course, year):
    db = get_db()
    if course not in assessors_mod.COURSES:
        return False
    q = "SELECT id FROM assessor_trainings WHERE assessor_id=? AND course=? AND " + ("year_be=?" if year else "year_be IS NULL")
    if db.execute(q, (aid, course, year) if year else (aid, course)).fetchone():
        return False
    db.execute("INSERT INTO assessor_trainings(assessor_id,course,year_be,created_at) VALUES(?,?,?,?)",
               (aid, course, year or None, now()))
    return True


def _ensure_account(a):
    """เปิดสิทธิ์เข้าระบบให้ผู้ตรวจ: มน. (@nu.ac.th) → บัญชี NU/LDAP · ภายนอก → อีเมล + รหัสผ่านที่ระบบสร้าง (ส่งทางอีเมล)
    คืน (ข้อความ, ระดับ)"""
    db = get_db()
    email = (a["email"] or "").strip().lower()
    if a["user_id"] and db.execute("SELECT 1 x FROM users WHERE id=?", (a["user_id"],)).fetchone():
        return f"{a['full_name']}: มีบัญชีอยู่แล้ว", "ok"
    if not email or not EMAIL_RE.match(email):
        return f"{a['full_name']}: ไม่มีอีเมลที่ถูกต้อง — แก้ไขข้อมูลก่อนเปิดสิทธิ์", "error"
    ldap_un = assessors_mod.ldap_username(email)
    u = db.execute("""SELECT * FROM users WHERE lower(username)=? OR lower(username)=?
                      OR (lower(email)=? AND role<>'lab') ORDER BY id LIMIT 1""",
                   (ldap_un or email, email, email)).fetchone()
    msg, level = None, "ok"
    if u:
        uid = u["id"]
        if u["role"] == "lab":
            db.execute("UPDATE users SET role='assessor' WHERE id=?", (uid,))
        msg = f"{a['full_name']}: ผูกกับบัญชีเดิม {u['username']}"
    elif ldap_un:
        uid = db.insert("""INSERT INTO users(username,full_name,email,faculty,role,auth_source,created_at)
                           VALUES(?,?,?,?,'assessor','ldap',?)""", (ldap_un, a["full_name"], email, a["faculty"], now()))
        msg = f"{a['full_name']}: เปิดสิทธิ์แล้ว — เข้าสู่ระบบด้วยบัญชี NU “{ldap_un}”"
    else:
        pw = gen_password()
        uid = db.insert("""INSERT INTO users(username,password_hash,full_name,email,faculty,role,auth_source,must_change_pw,created_at)
                           VALUES(?,?,?,?,?,'assessor','local',1,?)""",
                        (email, hash_pw(pw), a["full_name"], email, a["faculty"], now()))
        db.commit()
        st, err = mail_credentials(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), pw)
        if st == "sent":
            msg = f"{a['full_name']}: เปิดสิทธิ์แล้ว — ส่งชื่อผู้ใช้/รหัสผ่านไปที่ {email}"
        elif st == "dev":
            msg = f"{a['full_name']}: เปิดสิทธิ์แล้ว (โหมดทดสอบ) — ชื่อผู้ใช้ {email} รหัสผ่านชั่วคราว {pw}"
        else:
            msg, level = f"{a['full_name']}: เปิดสิทธิ์แล้วแต่ส่งอีเมลไม่สำเร็จ ({err}) — รหัสผ่านชั่วคราว {pw}", "error"
    db.execute("UPDATE users SET faculty=COALESCE(?, faculty), active=1 WHERE id=? AND role IN ('assessor','lead')",
               (a["faculty"], uid))
    db.execute("UPDATE assessors SET user_id=?, updated_at=? WHERE id=?", (uid, now(), a["id"]))
    return msg, level


def _flash_nu_sync(db):
    done = sync_nu_accounts(db)
    if done:
        flash("เปลี่ยนเป็นบัญชี NU (LDAP) แล้ว: " + ", ".join(done) + " — เข้าสู่ระบบด้วยชื่อผู้ใช้นี้และรหัสผ่าน NU", "ok")


def register(app):
    # ---------- PLANNING: ใบสมัคร + จัดทีมผู้ตรวจ
    @app.route("/planning", methods=["GET", "POST"])
    @roles("lead", "admin")
    def planning():
        db = get_db()
        year = request.values.get("year", type=int) or dbm.datetime.now().year + 543
        if request.method == "POST":
            act = request.form.get("action")
            if act == "approve":
                ids = [int(x) for x in request.form.getlist("app_id")]
                done = []
                for i in ids:
                    n = db.execute("""UPDATE applications SET status='approved', reviewed_at=?, reviewed_by=?
                                      WHERE id=? AND status IN ('pending','rejected')""", (now(), g.user["id"], i)).rowcount
                    if n:
                        db.execute("UPDATE labs SET status='approved' WHERE id=(SELECT lab_id FROM applications WHERE id=?)", (i,))
                        done.append(i)
                db.commit()
                for i in done:
                    mail_review(i, approved=True)
                flash(f"อนุมัติ {len(done)} ใบสมัคร และแจ้งห้องปฏิบัติการทางอีเมลแล้ว", "ok")
            elif act == "team_size":
                db.execute("INSERT INTO settings(key,value) VALUES('team_size',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (str(max(1, min(7, request.form.get("team_size", type=int) or 3))),))
                db.commit()
            elif act == "create":
                n = 0
                for app_id in [int(x) for x in request.form.getlist("plan_app")]:
                    ap = db.execute("SELECT * FROM applications WHERE id=? AND status='approved' AND audit_id IS NULL",
                                    (app_id,)).fetchone()
                    if not ap:
                        continue
                    lead = request.form.get(f"lead_{app_id}", type=int)
                    members = [int(x) for x in request.form.getlist(f"members_{app_id}") if x]
                    emap = {e: request.form.get(f"el_{app_id}_{e}", type=int) for e in range(1, 8)}
                    aid = db.insert("""INSERT INTO audits(lab_id,year_be,r1_date,status,lead_user_id,created_at)
                                       VALUES(?,?,?,'r1',?,?)""",
                                    (ap["lab_id"], ap["year_be"], request.form.get(f"date_{app_id}") or None, lead, now()))
                    save_team(aid, lead, members, emap)
                    db.execute("UPDATE applications SET status='assigned', audit_id=? WHERE id=?", (aid, app_id))
                    log(aid, "created_from_plan")
                    n += 1
                db.commit()
                flash(f"สร้างการตรวจประเมิน {n} ห้อง พร้อมทีมผู้ตรวจแล้ว", "ok")
            return redirect(url_for("planning", year=year))
        _flash_nu_sync(db)
        apps = db.execute(
            """SELECT ap.*, l.name lab_name, l.faculty, l.affiliation, l.espre_no, l.head_name, l.phone, l.email,
                      u.full_name applicant,
                      (SELECT COUNT(*) FROM app_files f WHERE f.application_id=ap.id AND f.kind='signed') n_signed,
                      (SELECT COUNT(*) FROM app_files f WHERE f.application_id=ap.id AND f.kind='survey') n_survey
               FROM applications ap JOIN labs l ON l.id=ap.lab_id
               LEFT JOIN users u ON u.id=ap.created_by WHERE ap.year_be=? AND ap.status<>'draft'
               ORDER BY ap.status, l.faculty, l.name""",
            (year,)).fetchall()
        assessors = [dict(u) for u in staff_users() if u["role"] in ("lead", "assessor")] or \
            [dict(u) for u in staff_users()]
        existing = {}
        for r in db.execute("""SELECT t.user_id, COUNT(*) n FROM audit_team t JOIN audits a ON a.id=t.audit_id
                               WHERE a.year_be=? GROUP BY t.user_id""", (year,)).fetchall():
            existing[r["user_id"]] = r["n"]
        to_plan = [dict(id=ap["id"], name=ap["lab_name"], faculty=ap["faculty"], affiliation=ap["affiliation"])
                   for ap in apps if ap["status"] == "approved" and not ap["audit_id"]]
        team_size = int(dbm.get_setting("team_size") or 3)
        proposals, load = propose(to_plan, assessors, team_size, existing)
        years = sorted({r["year_be"] for r in db.execute("SELECT DISTINCT year_be FROM applications").fetchall()} | {year},
                       reverse=True)
        n_draft = db.execute("SELECT COUNT(*) n FROM applications WHERE year_be=? AND status='draft'", (year,)).fetchone()["n"]
        qual = {}
        for r in db.execute("""SELECT a.user_id, t.course FROM assessors a LEFT JOIN assessor_trainings t ON t.assessor_id=a.id
                               WHERE a.user_id IS NOT NULL""").fetchall():
            qual.setdefault(r["user_id"], set())
            if r["course"]:
                qual[r["user_id"]].add(r["course"])
        return render_template("planning.html", year=year, years=years, apps=apps, proposals=proposals, n_draft=n_draft, qual=qual,
                               assessors=assessors, load=load, existing=existing, team_size=team_size)

    @app.route("/assessors", methods=["GET", "POST"])
    @roles("lead", "admin")
    def assessors_page():
        db = get_db()
        this_year = dbm.datetime.now().year + 543
        if request.method == "POST":
            act = request.form.get("action")
            if act == "import":
                f = request.files.get("file")
                if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
                    flash("กรุณาเลือกไฟล์ Excel (.xlsx)", "error")
                    return redirect(url_for("assessors_page"))
                try:
                    people, warns = assessors_mod.parse_workbook(
                        io.BytesIO(f.read()), f.filename, request.form.get("year", type=int) or this_year)
                except Exception as ex:  # noqa: BLE001
                    flash(f"อ่านไฟล์ไม่สำเร็จ: {ex}", "error")
                    return redirect(url_for("assessors_page"))
                added = updated = 0
                for p in people:
                    ex_row = None
                    if p["email"]:
                        ex_row = db.execute("SELECT * FROM assessors WHERE lower(email)=?", (p["email"],)).fetchone()
                    if not ex_row:
                        ex_row = db.execute("SELECT * FROM assessors WHERE name_key=?",
                                            (assessors_mod.name_key(p["full_name"]),)).fetchone()
                    if ex_row:
                        aid = ex_row["id"]
                        # เติมเฉพาะช่องที่ยังว่าง (ไม่ทับข้อมูลที่แก้ไขในระบบแล้ว)
                        db.execute("""UPDATE assessors SET affiliation=COALESCE(NULLIF(affiliation,''), ?),
                                      faculty=COALESCE(NULLIF(faculty,''), ?), email=COALESCE(NULLIF(email,''), ?),
                                      phone=COALESCE(NULLIF(phone,''), ?), note=COALESCE(NULLIF(note,''), ?), updated_at=?
                                      WHERE id=?""", (p["affiliation"], p["faculty"], p["email"] or None, p["phone"],
                                                      p["note"], now(), aid))
                    else:
                        aid = db.insert("""INSERT INTO assessors(full_name,name_key,affiliation,faculty,org_type,email,phone,note,
                                           created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                        (p["full_name"], assessors_mod.name_key(p["full_name"]), p["affiliation"], p["faculty"],
                                         p["org_type"], p["email"] or None, p["phone"], p["note"], now(), now()))
                        added += 1
                    n_new = sum(_add_training(aid, c, y) for c, y in sorted(p["trainings"], key=lambda t: (t[0], t[1] or 0)))
                    if ex_row and n_new:
                        updated += 1
                db.execute("INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                           (g.user["id"], "assessor_import", f"{f.filename}: +{added} ~{updated}", now()))
                db.commit()
                flash(f"นำเข้า {len(people)} รายชื่อ: เพิ่มใหม่ {added} คน · เพิ่มประวัติการอบรม {updated} คน · "
                      f"ซ้ำ/ไม่เปลี่ยนแปลง {len(people) - added - updated} คน", "ok")
                for w in warns[:12]:
                    flash("⚠ " + w, "error")
            elif act == "accounts":
                ids = [int(x) for x in request.form.getlist("aid")]
                if not ids:
                    flash("เลือกรายชื่อที่จะเปิดสิทธิ์เข้าระบบก่อน", "error")
                for i in ids:
                    a = db.execute("SELECT * FROM assessors WHERE id=?", (i,)).fetchone()
                    if a:
                        msg, level = _ensure_account(a)
                        db.commit()
                        flash(msg, level)
            return redirect(url_for("assessors_page", **request.args.to_dict()))
        _flash_nu_sync(db)
        rows = _assessor_rows()
        q = (request.args.get("q") or "").strip().lower()
        flt = request.args.get("f", "")
        tests = {"peer": lambda r: "Peer" in r["courses"], "esprel_only": lambda r: "Peer" not in r["courses"],
                 "nu": lambda r: r["org_type"] == "nu", "external": lambda r: r["org_type"] == "external",
                 "account": lambda r: bool(r["user_id"]), "noaccount": lambda r: not r["user_id"],
                 "inactive": lambda r: not r["active"]}
        shown = [r for r in rows if (not q or q in " ".join(str(r.get(k) or "") for k in
                                                                ("full_name", "affiliation", "email", "phone")).lower())
                 and tests.get(flt, lambda r: True)(r)]
        stats = {"all": len(rows), "peer": sum("Peer" in r["courses"] for r in rows),
                 "esprel": sum("ESPReL" in r["courses"] for r in rows),
                 "external": sum(r["org_type"] == "external" for r in rows),
                 "account": sum(bool(r["user_id"]) for r in rows),
                 "faculties": len({r["faculty"] for r in rows if r["faculty"] and r["org_type"] == "nu"})}
        return render_template("assessors.html", rows=shown, stats=stats, q=q, f=flt, this_year=this_year,
                               COURSES=assessors_mod.COURSES)

    @app.route("/assessors/new", methods=["GET", "POST"])
    @app.route("/assessors/<int:aid>", methods=["GET", "POST"])
    @roles("lead", "admin")
    def assessor_edit(aid=None):
        db = get_db()
        a = db.execute("SELECT * FROM assessors WHERE id=?", (aid,)).fetchone() if aid else None
        if aid and not a:
            abort(404)
        if request.method == "POST":
            act = request.form.get("action", "save")
            if act == "delete" and a:
                db.execute("DELETE FROM assessors WHERE id=?", (aid,))
                db.commit()
                flash(f"ลบ {a['full_name']} ออกจากทะเบียนแล้ว (บัญชีผู้ใช้ในระบบยังอยู่ — ปิดได้ที่เมนู “ผู้ใช้”)", "ok")
                return redirect(url_for("assessors_page"))
            if act == "account" and a:
                msg, level = _ensure_account(a)
                db.commit()
                flash(msg, level)
                return redirect(url_for("assessor_edit", aid=aid))
            f = request.form
            name = assessors_mod.clean(f.get("full_name"))
            email = assessors_mod.clean(f.get("email")).lower() or None
            if not name:
                flash("กรุณาระบุชื่อ-สกุล", "error")
                return redirect(request.url)
            if email and not EMAIL_RE.match(email):
                flash("อีเมลไม่ถูกต้อง", "error")
                return redirect(request.url)
            dup = db.execute("SELECT id FROM assessors WHERE (lower(email)=? OR name_key=?) AND id<>?",
                             (email or "-", assessors_mod.name_key(name), aid or 0)).fetchone()
            if dup:
                flash("มีรายชื่อนี้ (ชื่อหรืออีเมลเดียวกัน) ในทะเบียนแล้ว", "error")
                return redirect(url_for("assessor_edit", aid=dup["id"]))
            aff = assessors_mod.clean(f.get("affiliation"))
            nu_un = assessors_mod.ldap_username(email)
            org = "nu" if nu_un else (f.get("org_type") if f.get("org_type") in ("nu", "external")
                                      else assessors_mod.org_type(aff, email))
            vals = (name, assessors_mod.name_key(name), aff, assessors_mod.clean(f.get("faculty")) or assessors_mod.faculty_of(aff),
                    org,
                    email, assessors_mod.clean_phone(f.get("phone")), assessors_mod.clean(f.get("note")) or None,
                    1 if f.get("active") else 0, now())
            if a:
                db.execute("""UPDATE assessors SET full_name=?, name_key=?, affiliation=?, faculty=?, org_type=?, email=?, phone=?,
                              note=?, active=?, updated_at=? WHERE id=?""", vals + (aid,))
                if a["user_id"]:
                    db.execute("UPDATE users SET full_name=?, faculty=?, email=COALESCE(?, email) WHERE id=? AND role<>'lab'",
                               (name, vals[3], email, a["user_id"]))
                    if not vals[8]:
                        db.execute("UPDATE users SET active=0 WHERE id=? AND role='assessor'", (a["user_id"],))
            else:
                aid = db.insert("""INSERT INTO assessors(full_name,name_key,affiliation,faculty,org_type,email,phone,note,active,
                                   updated_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", vals + (now(),))
            for t in f.getlist("del_training"):
                db.execute("DELETE FROM assessor_trainings WHERE id=? AND assessor_id=?", (int(t), aid))
            if f.get("new_course"):
                _add_training(aid, f.get("new_course"), f.get("new_year", type=int))
            db.commit()
            flash("บันทึกข้อมูลผู้ตรวจแล้ว", "ok")
            _flash_nu_sync(db)
            return redirect(url_for("assessor_edit", aid=aid))
        _flash_nu_sync(db)
        row = _assessor_rows("WHERE a.id=?", (aid,))[0] if a else None
        audits = []
        if a and a["user_id"]:
            audits = db.execute("""SELECT a.id, a.year_be, a.status, l.name lab_name,
                                          CASE WHEN a.lead_user_id=t.user_id THEN 1 ELSE 0 END is_lead
                                   FROM audit_team t JOIN audits a ON a.id=t.audit_id JOIN labs l ON l.id=a.lab_id
                                   WHERE t.user_id=? ORDER BY a.year_be DESC, l.name""", (a["user_id"],)).fetchall()
        return render_template("assessor_form.html", a=row, audits=audits, COURSES=assessors_mod.COURSES,
                               this_year=dbm.datetime.now().year + 543)

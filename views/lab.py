"""พอร์ทัลห้องปฏิบัติการ: ใบสมัคร FM-001 (สาธารณะ/เข้าสู่ระบบ), ไฟล์แนบ, ผลการตรวจ, CAPA"""
import json
import re
from datetime import timedelta

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import config
import db as dbm
import fm001
import mailer
import storage
from core import (
    DOC_TYPES,
    EMAIL_RE,
    base_url,
    can_view_audit,
    content_disposition,
    follow_items,
    gen_password,
    get_attachments,
    get_capa,
    get_responses,
    is_lab_member,
    is_lead,
    is_staff,
    lab_may_see_results,
    load_audit,
    login_required,
    mail_credentials,
    roles,
    save_upload,
    summary_for,
)
from db import get_db, hash_pw, log, now
from scoring import ITEMS, thai_date


# ---------- FM-001 ใบสมัครรับการตรวจประเมิน
def _app_files(app_id):
    out = {}
    for r in get_db().execute("SELECT * FROM app_files WHERE application_id=? ORDER BY id", (app_id,)).fetchall():
        out.setdefault(r["kind"], []).append(r)
    return out


def _load_application(app_id):
    ap = get_db().execute(
        """SELECT ap.*, l.name lab_name, l.owner_user_id FROM applications ap JOIN labs l ON l.id=ap.lab_id
           WHERE ap.id=?""", (app_id,)).fetchone()
    if not ap:
        abort(404)
    if not (is_staff() or is_lab_member(ap["lab_id"])):
        abort(403)
    return ap


def _auto_app_no(app_id, year_be):
    """เลขที่ใบสมัครอัตโนมัติ รูปแบบ ลำดับ/ปี เช่น 001/2569 (หัวหน้าผู้ตรวจแก้ไขได้)"""
    db = get_db()
    cur = db.execute("SELECT app_no FROM applications WHERE id=?", (app_id,)).fetchone()
    if cur and cur["app_no"]:
        return
    seq = 0
    for r in db.execute("SELECT app_no FROM applications WHERE year_be=? AND app_no IS NOT NULL", (year_be,)).fetchall():
        m = re.match(r"\s*(\d+)", r["app_no"] or "")
        if m:
            seq = max(seq, int(m.group(1)))
    db.execute("UPDATE applications SET app_no=? WHERE id=?", (f"{seq + 1:03d}/{year_be}", app_id))


def _check_uploads(kinds):
    """ตรวจชนิด/ขนาดไฟล์ก่อนบันทึกข้อมูลใด ๆ คืนรายการข้อผิดพลาด"""
    errs = []
    for kind in kinds:
        for fs in request.files.getlist(f"file_{kind}"):
            if not fs or not fs.filename:
                continue
            if (fs.mimetype or "").lower() not in DOC_TYPES:
                errs.append(f"{fs.filename}: รองรับเฉพาะ PDF หรือรูปภาพ JPG/PNG")
            fs.stream.seek(0, 2)
            if fs.stream.tell() > 8 * 1024 * 1024:
                errs.append(f"{fs.filename}: ไฟล์ใหญ่เกิน 8 MB")
            fs.stream.seek(0)
    return errs


def _save_app_files(app_id, kinds, uid):
    db = get_db()
    for kind in kinds:
        for fs in request.files.getlist(f"file_{kind}"):
            if not fs or not fs.filename:
                continue
            try:
                path, name, ctype, size = save_upload(fs, f"applications/{app_id}/{kind}", DOC_TYPES)
            except (ValueError, RuntimeError) as ex:
                flash(f"{fs.filename}: {ex}", "error")
                continue
            db.execute("""INSERT INTO app_files(application_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                          VALUES(?,?,?,?,?,?,?,?)""", (app_id, kind, path, name, ctype, size, uid, now()))


def _lab_recipients(lab_id):
    rows = get_db().execute(
        """SELECT DISTINCT u.id, u.full_name, COALESCE(u.email, u.username) addr FROM users u
           WHERE u.active=1 AND (u.id=(SELECT owner_user_id FROM labs WHERE id=?)
                 OR u.id IN (SELECT user_id FROM lab_members WHERE lab_id=?))""", (lab_id, lab_id)).fetchall()
    return [r for r in rows if r["addr"] and "@" in r["addr"]]


def mail_review(ap_id, approved):
    """แจ้งผลการพิจารณาใบสมัครให้ห้องปฏิบัติการทางอีเมล"""
    ap = get_db().execute("""SELECT ap.*, l.name lab_name FROM applications ap JOIN labs l ON l.id=ap.lab_id
                             WHERE ap.id=?""", (ap_id,)).fetchone()
    link = f"{base_url()}{url_for('lab_apply', app_id=ap_id)}"
    no = ap["app_no"] or ""
    for r in _lab_recipients(ap["lab_id"]):
        if approved:
            subj = f"[ESPReL MRA มน.] ใบสมัคร {no} ได้รับการอนุมัติ · {ap['lab_name']}"
            body = (f"เรียน {r['full_name']}\n\nใบสมัครรับการตรวจประเมิน FM-001 ของ “{ap['lab_name']}” "
                    f"ปี {ap['year_be']} เลขที่ {no} ได้รับการอนุมัติแล้ว\n"
                    "คณะผู้ตรวจประเมินจะแจ้งกำหนดการตรวจประเมินผ่านระบบต่อไป\n\n"
                    f"ดูสถานะ: {link}\n\nระบบ ESPReL MRA มหาวิทยาลัยนเรศวร (อีเมลอัตโนมัติ)")
        else:
            subj = f"[ESPReL MRA มน.] ใบสมัคร {no} ไม่อนุมัติ · โปรดแก้ไขและส่งใหม่"
            body = (f"เรียน {r['full_name']}\n\nใบสมัครรับการตรวจประเมิน FM-001 ของ “{ap['lab_name']}” "
                    f"ปี {ap['year_be']} เลขที่ {no} ยังไม่ได้รับการอนุมัติ\n\n"
                    f"เหตุผล / สิ่งที่ต้องแก้ไข:\n{ap['review_note']}\n\n"
                    f"แก้ไขใบสมัครหรือแนบเอกสารเพิ่ม แล้วกด “ส่งใบสมัครอีกครั้ง” ได้ที่\n{link}\n\n"
                    "ระบบ ESPReL MRA มหาวิทยาลัยนเรศวร (อีเมลอัตโนมัติ)")
        mailer.send(r["addr"], subj, body, kind="review", user_id=r["id"])


def _notify_leads(lab_name, app_no, year_be):
    rows = get_db().execute("SELECT * FROM users WHERE role IN ('lead','admin') AND active=1 AND email IS NOT NULL").fetchall()
    for u in rows:
        mailer.send(u["email"], f"[ESPReL MRA มน.] ใบสมัครใหม่ {app_no}: {lab_name}",
                    f"มีใบสมัคร FM-001 ใหม่ ปี {year_be}\n  ห้องปฏิบัติการ: {lab_name}\n  เลขที่ใบสมัคร: {app_no}\n\n"
                    f"พิจารณาได้ที่ {base_url()}{url_for('planning', year=year_be)}", kind="notify", user_id=u["id"])


def register(app):
    # ---------- LAB PORTAL
    @app.route("/lab")
    @login_required
    def lab_home():
        db = get_db()
        if is_staff() and request.args.get("as") != "lab":
            return redirect(url_for("dashboard"))
        labs = db.execute(
            """SELECT * FROM labs WHERE owner_user_id=? OR id IN (SELECT lab_id FROM lab_members WHERE user_id=?)
               ORDER BY name""", (g.user["id"], g.user["id"])).fetchall()
        items = []
        for lab in labs:
            apps = [{"ap": ap, "signed": db.execute("SELECT COUNT(*) n FROM app_files WHERE application_id=? AND kind='signed'",
                                                     (ap["id"],)).fetchone()["n"]}
                    for ap in db.execute("SELECT * FROM applications WHERE lab_id=? ORDER BY year_be DESC", (lab["id"],)).fetchall()]
            audits = db.execute("SELECT * FROM audits WHERE lab_id=? ORDER BY year_be DESC", (lab["id"],)).fetchall()
            aud = []
            for a in audits:
                r1, r2 = summary_for(a["id"])
                need = sum(1 for r in get_responses(a["id"], 1).values() if r["ftype"] in ("NC", "OBS"))
                aud.append({"a": a, "r1": r1, "r2": r2, "need": need})
            items.append({"lab": lab, "apps": apps, "audits": aud})
        return render_template("lab_home.html", items=items, this_year=dbm.datetime.now().year + 543)

    @app.route("/application/<int:app_id>/reject", methods=["POST"])
    @roles("lead", "admin")
    def application_reject(app_id):
        """ไม่อนุมัติ/ส่งกลับ พร้อมเหตุผล → ห้องปฏิบัติการเห็นในระบบ + ได้รับอีเมล แก้ไขแล้วส่งใหม่ได้"""
        db = get_db()
        ap = _load_application(app_id)
        reason = (request.form.get("reason") or "").strip()
        if len(reason) < 5:
            flash("กรุณาระบุเหตุผลการไม่อนุมัติ/สิ่งที่ต้องแก้ไข เพื่อแจ้งห้องปฏิบัติการ", "error")
        elif ap["status"] not in ("pending", "approved", "rejected"):
            flash("ใบสมัครนี้จัดทีมตรวจแล้ว — ไม่สามารถเปลี่ยนเป็นไม่อนุมัติได้", "error")
        else:
            db.execute("""UPDATE applications SET status='rejected', review_note=?, reviewed_at=?, reviewed_by=?
                          WHERE id=?""", (reason[:3000], now(), g.user["id"], app_id))
            db.execute("INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                       (g.user["id"], f"app_reject:{app_id}", reason[:2000], now()))
            db.commit()
            mail_review(app_id, approved=False)
            flash(f"ไม่อนุมัติใบสมัคร {ap['app_no'] or ''} ของ {ap['lab_name']} และแจ้งเหตุผลให้ห้องปฏิบัติการแล้ว", "ok")
        return redirect(request.referrer or url_for("planning", year=ap["year_be"]))

    @app.route("/apply", methods=["GET", "POST"])
    def apply_public():
        """ใบสมัคร FM-001 สำหรับทุกคน (ไม่ต้องเข้าสู่ระบบ) → สร้างบัญชีห้องปฏิบัติการ + ส่งรหัสผ่านทางอีเมล"""
        if g.user:
            return redirect(url_for("lab_apply", **request.args))
        db = get_db()
        this_year = dbm.datetime.now().year + 543
        if request.method == "GET":
            d = {"organization": "มหาวิทยาลัยนเรศวร", "first_time": "1", "scores": fm001.blank_scores()}
            return render_template("fm001_form.html", ap=None, d=d, lab=None, files={}, fm=fm001, this_year=this_year,
                                   editable=True, year_be=request.args.get("year", type=int) or this_year, public=True)
        d = fm001.parse(request.form)
        year_be = request.form.get("year_be", type=int) or this_year
        d["coord_email"] = d["coord_email"].lower()

        def again(msg):
            flash(msg, "error")
            return render_template("fm001_form.html", ap=None, d=d, lab=None, files={}, fm=fm001, this_year=this_year,
                                   editable=True, year_be=year_be, public=True), 400
        if request.form.get("website"):          # honeypot กันบอท
            return again("ส่งใบสมัครไม่สำเร็จ")
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        since = (dbm.datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        n_recent = db.execute("SELECT COUNT(*) n FROM audit_log WHERE action='public_apply' AND detail=? AND created_at>?",
                              (ip, since)).fetchone()["n"]
        if n_recent >= config.PUBLIC_APPLY_PER_HOUR:
            return again("ส่งใบสมัครจากเครื่องนี้บ่อยเกินไป กรุณาลองใหม่ภายหลัง")
        n_survey = sum(1 for f in request.files.getlist("file_survey") if f and f.filename)
        errs = fm001.validate(d, n_survey)
        if d["coord_email"] and not EMAIL_RE.match(d["coord_email"]):
            errs.append("13. อีเมลผู้ประสานงานไม่ถูกต้อง")
        errs += _check_uploads(("survey", "other"))
        if errs:
            return again("ยังส่งไม่ได้ — กรุณาตรวจสอบ: " + " · ".join(errs)
                         + (" (โปรดแนบไฟล์อีกครั้ง)" if n_survey else ""))
        email = d["coord_email"]
        u = db.execute("SELECT * FROM users WHERE lower(username)=? OR lower(email)=? ORDER BY id LIMIT 1",
                       (email, email)).fetchone()
        if u:
            dup = db.execute("""SELECT ap.app_no FROM applications ap JOIN labs l ON l.id=ap.lab_id
                                WHERE l.owner_user_id=? AND lower(l.name)=? AND ap.year_be=?""",
                             (u["id"], d["name"].lower(), year_be)).fetchone()
            if dup:
                return render_template("apply_done.html", dup=True, app_no=dup["app_no"], lab_name=d["name"],
                                       email=email, status=None, password=None, year_be=year_be)
        new_pw = None
        if not u:
            new_pw = gen_password()
            uid = db.insert("""INSERT INTO users(username,password_hash,full_name,email,faculty,role,auth_source,
                               must_change_pw,created_at) VALUES(?,?,?,?,?,'lab','local',1,?)""",
                            (email, hash_pw(new_pw), d["coord_name"] or email, email, d["faculty"] or None, now()))
            u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        affil = " ".join(x for x in (d["department"], d["faculty"], d["organization"]) if x)
        # สร้างห้องใหม่เสมอ (ไม่ผูกกับห้องเดิมจากข้อมูลที่กรอก เพื่อไม่ให้ผู้อื่นเข้าถึงผลการตรวจของห้องอื่นได้)
        lid = db.insert("""INSERT INTO labs(name,espre_no,faculty,department,organization,phone,fax,head_name,email,contact,
                           affiliation,owner_user_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                        (d["name"], d["espre_no"], d["faculty"], d["department"], d["organization"], d["phone"], d["fax"],
                         d["head_name"], email, f'{d["coord_name"]} {d["coord_phone"]}'.strip(), affil, u["id"], now()))
        db.execute("INSERT INTO lab_members(lab_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (lid, u["id"]))
        app_id = db.insert("""INSERT INTO applications(lab_id,year_be,status,created_by,created_at,form,submitted_at,updated_at)
                              VALUES(?,?,'pending',?,?,?,?,?)""",
                           (lid, year_be, u["id"], now(), json.dumps(d, ensure_ascii=False), now(), now()))
        _auto_app_no(app_id, year_be)
        _save_app_files(app_id, ("survey", "other"), u["id"])
        db.execute("INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)", (u["id"], "public_apply", ip, now()))
        db.commit()
        app_no = db.execute("SELECT app_no FROM applications WHERE id=?", (app_id,)).fetchone()["app_no"]
        if new_pw:
            status, _ = mail_credentials(u, new_pw, d["name"], app_no)
        else:
            status, _ = mailer.send(email, f"[ESPReL MRA มน.] ได้รับใบสมัคร {app_no} · {d['name']}",
                                    f"เรียน {u['full_name']}\n\nระบบได้รับใบสมัคร FM-001 ของ “{d['name']}” เลขที่ {app_no} แล้ว\n"
                                    f"ห้องนี้ถูกเพิ่มในบัญชีเดิมของท่าน (ชื่อผู้ใช้ {u['username']})\n"
                                    f"เข้าสู่ระบบ: {base_url()}/login  (ลืมรหัสผ่าน: {base_url()}/forgot)\n\n"
                                    "ระบบ ESPReL MRA มหาวิทยาลัยนเรศวร (อีเมลอัตโนมัติ)", kind="apply", user_id=u["id"])
        _notify_leads(d["name"], app_no, year_be)
        show_pw = new_pw if (status == "dev" and config.MAIL_DEV_SHOW) else None
        return render_template("apply_done.html", dup=False, app_no=app_no, lab_name=d["name"], email=email,
                               username=u["username"], status=status, password=show_pw, existing=not new_pw, year_be=year_be)


    @app.route("/lab/apply", methods=["GET", "POST"])
    @app.route("/lab/apply/<int:app_id>", methods=["GET", "POST"])
    @login_required
    def lab_apply(app_id=None):
        db = get_db()
        this_year = dbm.datetime.now().year + 543
        ap = _load_application(app_id) if app_id else None
        lab_id = ap["lab_id"] if ap else (request.values.get("lab_id", type=int))
        lab = db.execute("SELECT * FROM labs WHERE id=?", (lab_id,)).fetchone() if lab_id else None
        if lab and not (is_lab_member(lab["id"]) or is_staff()):
            abort(403)
        editable = (not ap or ap["status"] in ("draft", "pending", "rejected")) and (not lab or is_lab_member(lab["id"]) or is_lead())
        if request.method == "POST":
            if not editable:
                flash("ใบสมัครนี้อนุมัติแล้ว แก้ไขไม่ได้", "error")
                return redirect(url_for("lab_apply", app_id=app_id))
            d = fm001.parse(request.form)
            year_be = request.form.get("year_be", type=int) or (ap["year_be"] if ap else this_year)
            if not d["name"]:
                flash("กรุณาระบุชื่อห้องปฏิบัติการ", "error")
                return render_template("fm001_form.html", ap=ap, d=d, lab=lab, files=_app_files(app_id) if app_id else {},
                                       fm=fm001, this_year=this_year, editable=True, year_be=year_be)
            labvals = (d["name"], d["espre_no"], d["faculty"], d["department"], d["organization"], d["phone"],
                       d["fax"], d["head_name"], d["coord_email"] or None, f'{d["coord_name"]} {d["coord_phone"]}'.strip())
            if lab:
                db.execute("""UPDATE labs SET name=?, espre_no=?, faculty=?, department=?, organization=?, phone=?, fax=?,
                              head_name=?, email=COALESCE(?, email), contact=? WHERE id=?""", labvals + (lab["id"],))
                lid = lab["id"]
            else:
                lid = db.insert("""INSERT INTO labs(name,espre_no,faculty,department,organization,phone,fax,head_name,email,contact,
                                   affiliation,owner_user_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                                labvals + (" ".join(x for x in (d["department"], d["faculty"], d["organization"]) if x),
                                           g.user["id"], now()))
                db.execute("INSERT INTO lab_members(lab_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (lid, g.user["id"]))
            if d["department"] or d["faculty"]:
                db.execute("UPDATE labs SET affiliation=? WHERE id=?",
                           (" ".join(x for x in (d["department"], d["faculty"], d["organization"]) if x), lid))
            if not ap:
                dup = db.execute("SELECT id FROM applications WHERE lab_id=? AND year_be=?", (lid, year_be)).fetchone()
                if dup:
                    app_id = dup["id"]
                    ap = _load_application(app_id)
                    if ap["status"] not in ("draft", "pending", "rejected"):
                        db.commit()
                        flash(f"ห้องนี้มีใบสมัครปี {year_be} ที่ดำเนินการแล้ว", "error")
                        return redirect(url_for("lab_apply", app_id=app_id))
                else:
                    app_id = db.insert("""INSERT INTO applications(lab_id,year_be,status,created_by,created_at) VALUES(?,?,'draft',?,?)""",
                                       (lid, year_be, g.user["id"], now()))
            else:
                db.execute("UPDATE applications SET year_be=? WHERE id=?", (year_be, app_id))
            # ไฟล์แนบ
            for kind in ("survey", "signed", "other"):
                for fs in request.files.getlist(f"file_{kind}"):
                    if not fs or not fs.filename:
                        continue
                    try:
                        path, name, ctype, size = save_upload(fs, f"applications/{app_id}/{kind}", DOC_TYPES)
                    except (ValueError, RuntimeError) as ex:
                        flash(f"{fs.filename}: {ex}", "error")
                        continue
                    db.execute("""INSERT INTO app_files(application_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                                  VALUES(?,?,?,?,?,?,?,?)""", (app_id, kind, path, name, ctype, size, g.user["id"], now()))
            n_survey = db.execute("SELECT COUNT(*) n FROM app_files WHERE application_id=? AND kind='survey'",
                                  (app_id,)).fetchone()["n"]
            submit = request.form.get("action") == "submit"
            status = ap["status"] if ap else "draft"
            resubmit = False
            if submit:
                errs = fm001.validate(d, n_survey)
                if errs:
                    db.execute("UPDATE applications SET form=?, updated_at=? WHERE id=?",
                               (json.dumps(d, ensure_ascii=False), now(), app_id))
                    db.commit()
                    flash("บันทึกร่างแล้ว แต่ยังส่งไม่ได้ — กรุณากรอก: " + " · ".join(errs), "error")
                    return redirect(url_for("lab_apply", app_id=app_id))
                resubmit = status == "rejected"
                status = "pending"
                _auto_app_no(app_id, year_be)
                if resubmit:
                    db.execute("INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                               (g.user["id"], f"app_resubmit:{app_id}", "", now()))
            db.execute("UPDATE applications SET form=?, status=?, updated_at=?, submitted_at=COALESCE(submitted_at, ?) WHERE id=?",
                       (json.dumps(d, ensure_ascii=False), status, now(), now() if submit else None, app_id))
            db.commit()
            if submit and resubmit:
                app_no = db.execute("SELECT app_no FROM applications WHERE id=?", (app_id,)).fetchone()["app_no"]
                _notify_leads(d["name"] + " (ส่งใหม่หลังแก้ไข)", app_no, year_be)
                flash("ส่งใบสมัครที่แก้ไขแล้วอีกครั้ง — รอหัวหน้าผู้ตรวจพิจารณา", "ok")
                return redirect(url_for("lab_apply", app_id=app_id))
            if submit:
                flash(f"ส่งใบสมัคร FM-001 ปี {year_be} แล้ว — พิมพ์ใบสมัคร ให้ผู้มีอำนาจลงนาม แล้วแนบไฟล์ที่ลงนามกลับเข้าระบบ", "ok")
                return redirect(url_for("lab_apply", app_id=app_id))
            flash("บันทึกร่างใบสมัครแล้ว", "ok")
            return redirect(url_for("lab_apply", app_id=app_id))
        # GET
        if ap:
            d = fm001.load(ap)
            year_be = ap["year_be"]
        else:
            d = {"organization": "มหาวิทยาลัยนเรศวร", "first_time": "1", "scores": fm001.blank_scores(),
                 "head_name": g.user["full_name"] if g.user["role"] == "lab" else "",
                 "coord_email": g.user["email"] or ""}
            year_be = request.args.get("year", type=int) or this_year
        if lab:
            for k in fm001.LAB_FIELDS:
                if not d.get(k):
                    d[k] = lab[k] or ""
            if not ap:
                last = db.execute("SELECT r2_date, r1_date FROM audits WHERE lab_id=? ORDER BY year_be DESC LIMIT 1",
                                  (lab["id"],)).fetchone()
                if last:
                    d["first_time"] = "0"
                    d["last_assessed"] = thai_date(last["r2_date"] or last["r1_date"]) if (last["r2_date"] or last["r1_date"]) else ""
        d.setdefault("scores", fm001.blank_scores())
        return render_template("fm001_form.html", ap=ap, d=d, lab=lab, files=_app_files(app_id) if app_id else {},
                               fm=fm001, this_year=this_year, editable=editable, year_be=year_be)

    @app.route("/application/<int:app_id>/signed", methods=["POST"])
    @login_required
    def application_signed(app_id):
        """แนบใบสมัครที่ลงนามแล้ว (ทำได้ทุกสถานะ)"""
        ap = _load_application(app_id)
        db = get_db()
        n = 0
        for fs in request.files.getlist("file_signed"):
            if fs and fs.filename:
                try:
                    path, name, ctype, size = save_upload(fs, f"applications/{app_id}/signed", DOC_TYPES)
                except (ValueError, RuntimeError) as ex:
                    flash(str(ex), "error")
                    continue
                db.execute("""INSERT INTO app_files(application_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?)""", (app_id, "signed", path, name, ctype, size, g.user["id"], now()))
                n += 1
        db.commit()
        if n:
            flash("แนบใบสมัครที่ลงนามแล้ว", "ok")
        return redirect(request.referrer or url_for("lab_apply", app_id=ap["id"]))

    @app.route("/application/file/<int:file_id>")
    @login_required
    def application_file(file_id):
        f = get_db().execute("SELECT * FROM app_files WHERE id=?", (file_id,)).fetchone()
        if not f:
            abort(404)
        _load_application(f["application_id"])
        url = storage.signed_url(f["path"])
        if url:
            return redirect(url)
        return Response(storage.load(f["path"]), mimetype=f["content_type"] or "application/octet-stream",
                        headers={"Content-Disposition": content_disposition(f["filename"])})

    @app.route("/application/file/<int:file_id>/delete", methods=["POST"])
    @login_required
    def application_file_delete(file_id):
        db = get_db()
        f = db.execute("SELECT * FROM app_files WHERE id=?", (file_id,)).fetchone()
        if not f:
            abort(404)
        ap = _load_application(f["application_id"])
        if not is_lead() and (ap["status"] not in ("draft", "pending", "rejected") and f["kind"] != "signed"):
            abort(403)
        storage.delete(f["path"])
        db.execute("DELETE FROM app_files WHERE id=?", (file_id,))
        db.commit()
        flash("ลบไฟล์แนบแล้ว", "ok")
        return redirect(request.referrer or url_for("lab_apply", app_id=ap["id"]))

    @app.route("/application/<int:app_id>/print")
    @login_required
    def application_print(app_id):
        ap = _load_application(app_id)
        lab = get_db().execute("SELECT * FROM labs WHERE id=?", (ap["lab_id"],)).fetchone()
        return render_template("fm001_print.html", ap=ap, d=fm001.load(ap), lab=lab, fm=fm001,
                               files=_app_files(app_id), s=dbm.all_settings())

    @app.route("/application/<int:app_id>/number", methods=["POST"])
    @roles("lead", "admin")
    def application_number(app_id):
        _load_application(app_id)
        get_db().execute("UPDATE applications SET app_no=? WHERE id=?", (request.form.get("app_no", "").strip() or None, app_id))
        get_db().commit()
        flash("บันทึกเลขที่ใบสมัครแล้ว", "ok")
        return redirect(request.referrer or url_for("planning"))

    @app.route("/lab/audit/<int:audit_id>")
    @login_required
    def lab_audit(audit_id):
        a = load_audit(audit_id)
        if not can_view_audit(a):
            abort(403)
        r1, r2 = summary_for(audit_id)
        resp = {1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)}
        follow = [iid for iid in sorted(resp[1], key=lambda x: tuple(map(int, x.split("-"))))
                  if resp[1][iid]["ftype"] in ("NC", "OBS")]
        reports = get_db().execute("SELECT * FROM reports WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
        return render_template("lab_audit.html", a=a, r1=r1, r2=r2, resp=resp, follow=follow, capa=get_capa(audit_id),
                               atts=get_attachments(audit_id), visible=lab_may_see_results(a) or is_staff(),
                               capa_open=a["status"] == "r1_reported" and is_lab_member(a["lab_id"]),
                               reports=reports)

    @app.route("/api/lab/audit/<int:audit_id>/capa/<item_id>", methods=["POST"])
    @login_required
    def api_capa(audit_id, item_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]):
            return jsonify(ok=False, error="ไม่มีสิทธิ์"), 403
        if a["status"] != "r1_reported":
            return jsonify(ok=False, error="ส่ง CAPA แล้ว หรือยังไม่ถึงขั้นตอนตอบ CAPA"), 409
        if item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="ข้อนี้ไม่ต้องตอบ CAPA"), 400
        d = request.get_json(force=True) or {}
        db = get_db()
        row = db.execute("SELECT * FROM capa WHERE audit_id=? AND item_id=?", (audit_id, item_id)).fetchone()
        vals = dict(row) if row else {"root_cause": None, "action": None, "owner": None, "due_date": None}
        for k in ("root_cause", "action", "owner", "due_date"):
            if k in d:
                vals[k] = (d[k] or "").strip() or None
        db.execute("""INSERT INTO capa(audit_id,item_id,root_cause,action,owner,due_date,updated_by,updated_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(audit_id,item_id) DO UPDATE SET root_cause=excluded.root_cause,
                      action=excluded.action, owner=excluded.owner, due_date=excluded.due_date,
                      updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                   (audit_id, item_id, vals["root_cause"], vals["action"], vals["owner"], vals["due_date"],
                    g.user["id"], now()))
        db.commit()
        return jsonify(ok=True)

    @app.route("/api/lab/audit/<int:audit_id>/capa/<item_id>/file", methods=["POST"])
    @login_required
    def api_capa_file(audit_id, item_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
            return jsonify(ok=False, error="ไม่มีสิทธิ์อัปโหลด"), 403
        if item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="ข้อนี้ไม่ต้องตอบ CAPA"), 400
        fs = request.files.get("file")
        if not fs:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 400
        try:
            path, name, ctype, size = save_upload(fs, f"audits/{audit_id}/capa/{item_id}", DOC_TYPES)
        except (ValueError, RuntimeError) as ex:
            return jsonify(ok=False, error=str(ex)), 400
        db = get_db()
        att_id = db.insert("""INSERT INTO attachments(audit_id,round,item_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (audit_id, 1, item_id, "capa", path, name, ctype, size, g.user["id"], now()))
        db.commit()
        return jsonify(ok=True, id=att_id, url=url_for("file_view", att_id=att_id), name=name, ctype=ctype)

    @app.route("/lab/audit/<int:audit_id>/capa/submit", methods=["POST"])
    @login_required
    def capa_submit(audit_id):
        a = load_audit(audit_id)
        if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
            abort(403)
        capa = get_capa(audit_id)
        missing = [ITEMS[i]["ref"] for i in follow_items(audit_id) if not (capa.get(i) and capa[i]["action"])]
        if missing:
            flash("ยังไม่ได้ระบุการแก้ไขในข้อ: " + ", ".join(sorted(missing)), "error")
            return redirect(url_for("lab_audit", audit_id=audit_id))
        db = get_db()
        db.execute("UPDATE audits SET status='capa_submitted', capa_submitted_at=?, capa_return_note=NULL WHERE id=?",
                   (now(), audit_id))
        log(audit_id, "capa_submit")
        db.commit()
        flash("ส่ง CAPA เรียบร้อย คณะผู้ตรวจจะนัดตรวจติดตามครั้งที่ 2", "ok")
        return redirect(url_for("lab_audit", audit_id=audit_id))

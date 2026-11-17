"""ภาพรวมผู้ตรวจ, สร้าง/แก้ไขการตรวจประเมินและทีม, หน้าสรุปห้องปฏิบัติการ"""

from flask import flash, g, redirect, render_template, request, url_for

import db as dbm
import storage
from core import (
    EMAIL_RE,
    STAFF,
    STATUS_TH,
    assignments,
    current_round,
    element_status,
    gen_password,
    get_attachments,
    get_capa,
    get_responses,
    is_lead,
    load_audit,
    login_required,
    mail_credentials,
    observer_notes,
    observers,
    r2_progress,
    roles,
    round_editable,
    summary_for,
    team,
)
from db import get_db, hash_pw, log, now
from views.observe import observer_users, save_observers


# ---------- create / edit audit (ด้วยมือ)
def staff_users():
    return get_db().execute(
        "SELECT * FROM users WHERE active=1 AND role IN ('admin','lead','assessor') ORDER BY role, full_name").fetchall()


def _attach_lab_user(lab_id, username):
    username = (username or "").strip().lower()
    if not username:
        return
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE lower(username)=? OR (lower(email)=? AND role='lab')",
                   (username, username)).fetchone()
    if u:
        uid = u["id"]
    elif "@" in username and EMAIL_RE.match(username):
        # บัญชีห้องปฏิบัติการใหม่ (อีเมล + รหัสผ่านที่ระบบสร้าง) แล้วส่งรหัสผ่านทางอีเมล
        pw = gen_password()
        lab = db.execute("SELECT name FROM labs WHERE id=?", (lab_id,)).fetchone()
        uid = db.insert("""INSERT INTO users(username,password_hash,full_name,email,role,auth_source,must_change_pw,created_at)
                           VALUES(?,?,?,?,'lab','local',1,?)""", (username, hash_pw(pw), username, username, now()))
        db.commit()
        st, err = mail_credentials(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), pw,
                                   lab["name"] if lab else None)
        if st == "sent":
            flash(f"สร้างบัญชีห้องปฏิบัติการ {username} และส่งรหัสผ่านทางอีเมลแล้ว", "ok")
        else:
            flash(f"สร้างบัญชีห้องปฏิบัติการ {username} แล้ว — {'โหมดทดสอบ' if st == 'dev' else 'ส่งอีเมลไม่สำเร็จ'}: "
                  f"รหัสผ่านชั่วคราว {pw}", "ok" if st == "dev" else "error")
    else:
        flash("ผูกบัญชีห้องปฏิบัติการ: ใส่อีเมลของผู้ประสานงานห้อง (ระบบจะสร้างบัญชีและส่งรหัสผ่านให้)", "error")
        return
    db.execute("INSERT INTO lab_members(lab_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (lab_id, uid))


def save_team(audit_id, lead, member_ids, element_map):
    db = get_db()
    member_ids = set(member_ids)
    if lead:
        member_ids.add(lead)
    member_ids |= {u for u in element_map.values() if u}
    db.execute("DELETE FROM audit_team WHERE audit_id=?", (audit_id,))
    for uid in member_ids:
        db.execute("INSERT INTO audit_team(audit_id,user_id) VALUES(?,?)", (audit_id, uid))
    db.execute("DELETE FROM assignments WHERE audit_id=?", (audit_id,))
    for e, uid in element_map.items():
        if uid:
            db.execute("INSERT INTO assignments(audit_id,element,user_id) VALUES(?,?,?)", (audit_id, e, uid))


def _save_audit_form(audit_id=None):
    db = get_db()
    f = request.form
    name = f.get("lab_name", "").strip()
    if not name:
        flash("กรุณาระบุชื่อห้องปฏิบัติการ", "error")
        return None
    labvals = (name, f.get("espre_no", "").strip(), f.get("faculty", "").strip(), f.get("affiliation", "").strip(),
               f.get("location", "").strip(), f.get("head_name", "").strip(), f.get("contact", "").strip())
    r1_date = f.get("r1_date") or None
    year_be = f.get("year_be", type=int) or ((int(r1_date[:4]) if r1_date else dbm.datetime.now().year) + 543)
    lead = f.get("lead_user_id", type=int) or (g.user["id"] if is_lead() else None)
    if audit_id:
        a = load_audit(audit_id)
        lab_id = a["lab_id"]
        db.execute("UPDATE labs SET name=?,espre_no=?,faculty=?,affiliation=?,location=?,head_name=?,contact=? WHERE id=?",
                   labvals + (lab_id,))
        db.execute("""UPDATE audits SET year_be=?, r1_date=?, r2_date=?, lead_user_id=?, deadline=?, notes=?,
                      r1_report_date=?, r2_report_date=? WHERE id=?""",
                   (year_be, r1_date, f.get("r2_date") or None, lead, f.get("deadline") or None,
                    f.get("notes"), f.get("r1_report_date") or None, f.get("r2_report_date") or None, audit_id))
    else:
        lab_id = f.get("lab_id", type=int)
        if lab_id:
            db.execute("UPDATE labs SET name=?,espre_no=?,faculty=?,affiliation=?,location=?,head_name=?,contact=? WHERE id=?",
                       labvals + (lab_id,))
        else:
            lab_id = db.insert("""INSERT INTO labs(name,espre_no,faculty,affiliation,location,head_name,contact,status,created_at)
                                  VALUES(?,?,?,?,?,?,?,'approved',?)""", labvals + (now(),))
        audit_id = db.insert("""INSERT INTO audits(lab_id,year_be,r1_date,r2_date,status,lead_user_id,deadline,notes,created_at)
                                VALUES(?,?,?,?,'r1',?,?,?,?)""",
                             (lab_id, year_be, r1_date, f.get("r2_date") or None, lead,
                              f.get("deadline") or None, f.get("notes"), now()))
    _attach_lab_user(lab_id, f.get("lab_username"))
    save_team(audit_id, lead, [int(x) for x in f.getlist("team") if x],
               {e: f.get(f"assign_{e}", type=int) for e in range(1, 8)})
    save_observers(audit_id, f.getlist("observers"))
    log(audit_id, "edit_audit")
    db.commit()
    return audit_id


def register(app):
    # ---------- dashboard (ผู้ตรวจ)
    @app.route("/")
    @login_required
    def dashboard():
        if g.user["role"] == "lab":
            return redirect(url_for("lab_home"))
        if g.user["role"] == "observer":
            return redirect(url_for("observe_home"))
        db = get_db()
        year = request.args.get("year", type=int)
        years = [r["year_be"] for r in db.execute("SELECT DISTINCT year_be FROM audits ORDER BY year_be DESC")]
        q = "SELECT a.*, l.name lab_name, l.espre_no, l.affiliation FROM audits a JOIN labs l ON l.id=a.lab_id"
        args = ()
        if year:
            q += " WHERE a.year_be=?"
            args = (year,)
        q += " ORDER BY a.year_be DESC, l.name"
        audits, mine = [], []
        stats = {"labs": 0, "done": 0, "nc_open": 0, "avg": [], "status": dict.fromkeys(STATUS_TH, 0)}
        for a in db.execute(q, args).fetchall():
            r1, r2 = summary_for(a["id"])
            rnd = current_round(a)
            cur = r2 if rnd == 2 else r1
            st = element_status(a["id"], rnd)
            nc_open = 0
            if a["status"] != "r1":
                r2resp = get_responses(a["id"], 2)
                for iid, r in get_responses(a["id"], 1).items():
                    if r["ftype"] == "NC":
                        f = r2resp.get(iid)
                        if not f or f["followup"] != "resolved":
                            nc_open += 1
            else:
                nc_open = r1["total"]["NC"]
            asg = assignments(a["id"])
            my_elements = sorted(e for e, v in asg.items() if v["user_id"] == g.user["id"])
            row = dict(a=a, r1=r1, r2=r2, rnd=rnd, cur=cur, done=sum(1 for s in st.values() if s["done"]),
                       nc_open=nc_open, my_elements=my_elements)
            audits.append(row)
            if my_elements and a["status"] in ("r1", "r2"):
                mine.append(row)
            stats["labs"] += 1
            stats["status"][a["status"]] = stats["status"].get(a["status"], 0) + 1
            stats["done"] += a["status"] == "r2_reported"
            stats["nc_open"] += nc_open
            if cur["total"]["answered"]:
                stats["avg"].append(cur["total"]["pct"])
        stats["avg"] = round(sum(stats["avg"]) / len(stats["avg"]), 1) if stats["avg"] else 0
        pending = db.execute("SELECT COUNT(*) n FROM applications WHERE status IN ('pending','approved')").fetchone()["n"]
        return render_template("dashboard.html", audits=audits, stats=stats, years=years, year=year, mine=mine,
                               pending=pending)

    @app.route("/audit/new", methods=["GET", "POST"])
    @roles("lead", "admin")
    def audit_new():
        if request.method == "POST":
            aid = _save_audit_form()
            if aid:
                flash("สร้างรายการตรวจประเมินเรียบร้อย", "ok")
                return redirect(url_for("audit_view", audit_id=aid))
        labs = get_db().execute("SELECT * FROM labs ORDER BY name").fetchall()
        return render_template("audit_form.html", audit=None, users=staff_users(), team_ids=set(), asg={},
                               labs=labs, lab_users=[], observer_users=observer_users(), observer_ids=set())

    @app.route("/audit/<int:audit_id>/edit", methods=["GET", "POST"])
    @roles("lead", "admin")
    def audit_edit(audit_id):
        a = load_audit(audit_id)
        if request.method == "POST" and _save_audit_form(audit_id):
            flash("บันทึกข้อมูลเรียบร้อย", "ok")
            return redirect(url_for("audit_view", audit_id=audit_id))
        lab_users = get_db().execute(
            """SELECT u.* FROM users u WHERE u.id IN (SELECT user_id FROM lab_members WHERE lab_id=?)
               OR u.id=(SELECT owner_user_id FROM labs WHERE id=?)""", (a["lab_id"], a["lab_id"])).fetchall()
        return render_template("audit_form.html", audit=a, users=staff_users(),
                               team_ids={u["id"] for u in team(audit_id)}, asg=assignments(audit_id), labs=[],
                               lab_users=lab_users, observer_users=observer_users(),
                               observer_ids={u["id"] for u in observers(audit_id)})

    @app.route("/audit/<int:audit_id>/delete", methods=["POST"])
    @roles("admin", "lead")
    def audit_delete(audit_id):
        load_audit(audit_id)
        db = get_db()
        for att in db.execute("SELECT path FROM attachments WHERE audit_id=?", (audit_id,)).fetchall():
            storage.delete(att["path"])
        db.execute("UPDATE applications SET audit_id=NULL, status='approved' WHERE audit_id=?", (audit_id,))
        db.execute("DELETE FROM audits WHERE id=?", (audit_id,))
        log(audit_id, "delete_audit")
        db.commit()
        flash("ลบรายการตรวจประเมินแล้ว", "ok")
        return redirect(url_for("dashboard"))

    # ---------- lab summary (ผู้ตรวจ)
    @app.route("/audit/<int:audit_id>")
    @roles(*STAFF)
    def audit_view(audit_id):
        a = load_audit(audit_id)
        r1, r2 = summary_for(audit_id)
        rnd = request.args.get("round", type=int) or current_round(a)
        resp = {1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)}
        reports = get_db().execute(
            "SELECT r.*, u.full_name FROM reports r LEFT JOIN users u ON u.id=r.created_by "
            "WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
        follow = {iid for iid, r in resp[1].items() if r["ftype"] in ("NC", "OBS")}
        logs = get_db().execute(
            """SELECT l.*, u.full_name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id
               WHERE audit_id=? ORDER BY l.id DESC LIMIT 30""", (audit_id,)).fetchall()
        return render_template("audit.html", a=a, r1=r1, r2=r2, rnd=rnd, resp=resp, capa=get_capa(audit_id),
                               st={1: element_status(audit_id, 1), 2: element_status(audit_id, 2)},
                               asg=assignments(audit_id), team=team(audit_id), reports=reports,
                               editable=round_editable(a, rnd), follow=follow,
                               r2prog=r2_progress(audit_id, follow), atts=get_attachments(audit_id), logs=logs,
                               obs=observers(audit_id), obs_notes=observer_notes(audit_id))

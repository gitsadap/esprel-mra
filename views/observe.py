"""ผู้สังเกตการณ์: ร่วมการตรวจรายห้องที่ได้รับเชิญ — ดูผลการตรวจ/รายงาน และบันทึกความเห็นถึงคณะผู้ตรวจ (ไม่ให้คะแนน ไม่แก้ไขผล)"""
from flask import abort, flash, g, redirect, render_template, request, url_for

from core import (
    STATUS_TH,
    current_round,
    element_status,
    get_attachments,
    get_capa,
    get_responses,
    is_observer,
    is_staff,
    load_audit,
    login_required,
    observer_notes,
    observers,
    roles,
    summary_for,
    team,
)
from db import get_db, log, now
from scoring import ELEMENTS

MAX_NOTE = 4000


def observed_audits(user_id):
    return get_db().execute(
        """SELECT a.*, l.name lab_name, l.faculty, l.espre_no, u.full_name lead_name
           FROM audit_observers o JOIN audits a ON a.id=o.audit_id JOIN labs l ON l.id=a.lab_id
           LEFT JOIN users u ON u.id=a.lead_user_id
           WHERE o.user_id=? ORDER BY a.year_be DESC, l.name""", (user_id,)).fetchall()


def save_observers(audit_id, user_ids):
    """กำหนดผู้สังเกตการณ์ของรายการตรวจ (เฉพาะบัญชีบทบาทผู้สังเกตการณ์ที่เปิดใช้งาน)"""
    db = get_db()
    valid = {r["id"] for r in db.execute("SELECT id FROM users WHERE role='observer' AND active=1").fetchall()}
    want = {int(u) for u in user_ids if str(u).isdigit()} & valid
    have = {r["user_id"] for r in db.execute("SELECT user_id FROM audit_observers WHERE audit_id=?", (audit_id,))}
    for uid in have - want:
        db.execute("DELETE FROM audit_observers WHERE audit_id=? AND user_id=?", (audit_id, uid))
    for uid in want - have:
        db.execute("INSERT INTO audit_observers(audit_id,user_id,created_at) VALUES(?,?,?)", (audit_id, uid, now()))


def observer_users():
    return get_db().execute("SELECT * FROM users WHERE role='observer' AND active=1 ORDER BY full_name").fetchall()


def _guard(audit_id):
    a = load_audit(audit_id)
    if not (is_observer(audit_id) or is_staff()):
        abort(403)
    return a


def register(app):
    @app.route("/audit/<int:audit_id>/observers", methods=["POST"])
    @roles("lead", "admin")
    def audit_observers(audit_id):
        """เชิญ/เอาออก ผู้สังเกตการณ์ จากการ์ดคณะผู้ตรวจในหน้าการตรวจ"""
        load_audit(audit_id)
        db = get_db()
        uid = request.form.get("user_id", type=int)
        cur = [u["id"] for u in observers(audit_id)]
        if request.form.get("action") == "remove":
            save_observers(audit_id, [u for u in cur if u != uid])
            flash("เอาผู้สังเกตการณ์ออกจากการตรวจนี้แล้ว", "ok")
        elif uid:
            save_observers(audit_id, cur + [uid])
            u = db.execute("SELECT full_name FROM users WHERE id=? AND role='observer'", (uid,)).fetchone()
            if u:
                flash(f"เชิญ {u['full_name']} เป็นผู้สังเกตการณ์แล้ว — เข้าสู่ระบบแล้วจะเห็นการตรวจนี้ที่เมนู “การตรวจที่สังเกตการณ์”", "ok")
        log(audit_id, "observers")
        db.commit()
        return redirect(url_for("audit_view", audit_id=audit_id) + "#team")

    @app.route("/observe")
    @login_required
    def observe_home():
        if g.user["role"] != "observer":
            return redirect(url_for("home"))
        return render_template("observe_home.html", audits=observed_audits(g.user["id"]), STATUS_TH=STATUS_TH)

    @app.route("/observe/<int:audit_id>", methods=["GET", "POST"])
    @login_required
    def observe_audit(audit_id):
        a = _guard(audit_id)
        db = get_db()
        if request.method == "POST":
            if not is_observer(audit_id):
                abort(403)
            if request.form.get("action") == "delete":
                db.execute("DELETE FROM observer_notes WHERE id=? AND audit_id=? AND user_id=?",
                           (request.form.get("note_id", type=int), audit_id, g.user["id"]))
                db.commit()
                flash("ลบบันทึกแล้ว", "ok")
                return redirect(url_for("observe_audit", audit_id=audit_id))
            body = (request.form.get("body") or "").strip()
            rnd = request.form.get("round", type=int) if request.form.get("round", type=int) in (1, 2) else current_round(a)
            el = request.form.get("element", type=int)
            if not body:
                flash("กรุณาพิมพ์ความเห็น", "error")
            elif len(body) > MAX_NOTE:
                flash(f"ความเห็นยาวเกิน {MAX_NOTE} ตัวอักษร", "error")
            else:
                db.execute("""INSERT INTO observer_notes(audit_id,user_id,round,element,body,created_at)
                              VALUES(?,?,?,?,?,?)""", (audit_id, g.user["id"], rnd, el if el in ELEMENTS else None, body, now()))
                log(audit_id, "observer_note", rnd)
                db.commit()
                flash("บันทึกความเห็นแล้ว — คณะผู้ตรวจจะเห็นในหน้าการตรวจ", "ok")
            return redirect(url_for("observe_audit", audit_id=audit_id))
        r1, r2 = summary_for(audit_id)
        reports = db.execute("SELECT * FROM reports WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
        return render_template("observe_audit.html", a=a, r1=r1, r2=r2, rnd=current_round(a), team=team(audit_id),
                               obs=observers(audit_id), notes=observer_notes(audit_id), reports=reports,
                               STATUS_TH=STATUS_TH)

    @app.route("/observe/<int:audit_id>/round/<int:rnd>")
    @login_required
    def observe_results(audit_id, rnd):
        """ผลการตรวจแบบอ่านอย่างเดียว (สไลด์สรุปเดียวกับการประชุมปิด)"""
        a = _guard(audit_id)
        if rnd not in (1, 2) or (rnd == 2 and a["status"] not in ("r2", "r2_reported")):
            abort(404)
        r1, r2 = summary_for(audit_id)
        return render_template("closing.html", a=a, rnd=rnd, r1=r1, r2=r2, observer=True,
                               resp={1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)},
                               st=element_status(audit_id, rnd), asg={}, team=team(audit_id),
                               capa=get_capa(audit_id), atts=get_attachments(audit_id))

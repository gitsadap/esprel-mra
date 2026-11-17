"""ให้คะแนนรายข้อ (API), รูปหลักฐาน, ยืนยันองค์ประกอบ, สร้างรายงาน, รอบ 2, ประชุมปิด"""
import io
import secrets

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

import storage
from core import (
    IMG_TYPES,
    STAFF,
    assignments,
    can_view_audit,
    element_status,
    ensure_round2,
    follow_items,
    get_attachments,
    get_capa,
    get_responses,
    is_lab_member,
    is_observer,
    is_staff,
    lab_may_see_results,
    load_audit,
    login_required,
    r2_progress,
    roles,
    round_editable,
    save_upload,
    summary_for,
    team,
)
from db import get_db, log, now
from scoring import ELEMENTS, FOLLOWUP, FTYPES, ITEMS, compute, level_of


def _el_json(summ, el):
    e = summ["elements"][el]
    out = {k: e[k] for k in ("no", "max", "got", "pct", "answered", "count", "progress", "NC", "OBS", "SUG")}
    out.update(sections=e["sections"], level=level_of(e["pct"]))
    return out


def register(app):
    # ---------- assessment form
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>")
    @roles(*STAFF)
    def assess(audit_id, rnd, el):
        if el not in ELEMENTS or rnd not in (1, 2):
            abort(404)
        a = load_audit(audit_id)
        if rnd == 2 and a["status"] not in ("r2", "r2_reported"):
            flash("ยังไม่ได้เริ่มการตรวจรอบที่ 2 (ต้องรอห้องปฏิบัติการส่ง CAPA)", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        resp = get_responses(audit_id, rnd)
        r1resp = get_responses(audit_id, 1) if rnd == 2 else {}
        follow = {iid for iid, r in r1resp.items() if r["ftype"] in ("NC", "OBS")} if rnd == 2 else set()
        summ = compute(resp)
        groups = []
        for n in ELEMENTS[el]["nodes"]:
            if n["kind"] == "section":
                groups.append({"sec": n, "nodes": []})
                continue
            if not groups:
                groups.append({"sec": None, "nodes": []})
            if n["kind"] == "option" and groups[-1]["nodes"] and groups[-1]["nodes"][-1]["kind"] == "item":
                opts = groups[-1]["nodes"][-1].setdefault("_opts", [])
                if n["text"] not in opts:
                    opts.append(n["text"])
                continue
            groups[-1]["nodes"].append(dict(n))
        return render_template("assess.html", a=a, rnd=rnd, el=ELEMENTS[el], resp=resp, r1resp=r1resp,
                               summ=summ, es=summ["elements"][el], st=element_status(audit_id, rnd),
                               asg=assignments(audit_id), groups=groups, editable=round_editable(a, rnd),
                               follow=follow, capa=get_capa(audit_id) if rnd == 2 else {},
                               atts=get_attachments(audit_id), r2prog=r2_progress(audit_id) if rnd == 2 else {})

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/item/<item_id>", methods=["POST"])
    @roles(*STAFF)
    def api_item(audit_id, rnd, item_id):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว (ออกรายงานแล้ว)"), 409
        it = ITEMS.get(item_id)
        if not it:
            return jsonify(ok=False, error="ไม่พบรายการ"), 404
        if rnd == 2 and item_id not in follow_items(audit_id):
            return jsonify(ok=False, error="รอบ 2 ตรวจเฉพาะข้อที่เป็น NC/OBS ในรอบ 1"), 409
        data = request.get_json(force=True) or {}
        db = get_db()
        row = db.execute("SELECT * FROM responses WHERE audit_id=? AND round=? AND item_id=?",
                         (audit_id, rnd, item_id)).fetchone()
        vals = dict(row) if row else {"score": None, "ftype": None, "comment": None, "followup": None,
                                      "fnote": None, "carried": 0}
        before = {k: vals.get(k) for k in ("score", "ftype", "comment", "followup")}
        if "score" in data:
            s = data["score"]
            if s in (None, ""):
                vals["score"] = None
            elif s == "NA":
                vals["score"] = "NA"
            else:
                try:
                    v = int(s)
                except (TypeError, ValueError):
                    return jsonify(ok=False, error="คะแนนไม่ถูกต้อง"), 400
                if v < 0 or v > it["max"]:
                    return jsonify(ok=False, error="คะแนนเกินคะแนนเต็ม"), 400
                vals["score"] = str(v)
            vals["carried"] = 0
        if "ftype" in data:
            vals["ftype"] = data["ftype"] if data["ftype"] in FTYPES else None
        if "comment" in data:
            vals["comment"] = (data["comment"] or "").strip() or None
        if "followup" in data:
            vals["followup"] = data["followup"] if data["followup"] in FOLLOWUP else None
        if "fnote" in data:
            vals["fnote"] = (data["fnote"] or "").strip() or None
        if data.get("confirm"):
            vals["carried"] = 0
        db.execute("""INSERT INTO responses(audit_id,round,item_id,score,ftype,comment,followup,fnote,carried,updated_by,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(audit_id,round,item_id) DO UPDATE SET score=excluded.score, ftype=excluded.ftype,
                      comment=excluded.comment, followup=excluded.followup, fnote=excluded.fnote,
                      carried=excluded.carried, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                   (audit_id, rnd, item_id, vals["score"], vals["ftype"], vals["comment"], vals["followup"],
                    vals["fnote"], vals["carried"], g.user["id"], now()))
        after = {k: vals.get(k) for k in ("score", "ftype", "comment", "followup")}
        changed = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
        if changed:
            log(audit_id, "item", rnd, item_id, repr(changed))
        db.commit()
        summ = compute(get_responses(audit_id, rnd))
        return jsonify(ok=True, item=vals, element=_el_json(summ, it["element"]), total=summ["total"])

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/item/<item_id>/photo", methods=["POST"])
    @roles(*STAFF)
    def api_photo(audit_id, rnd, item_id):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        if item_id not in ITEMS:
            return jsonify(ok=False, error="ไม่พบรายการ"), 404
        fs = request.files.get("file")
        if not fs:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 400
        try:
            path, name, ctype, size = save_upload(fs, f"audits/{audit_id}/r{rnd}/{item_id}", IMG_TYPES)
        except (ValueError, RuntimeError) as ex:
            return jsonify(ok=False, error=str(ex)), 400
        db = get_db()
        att_id = db.insert("""INSERT INTO attachments(audit_id,round,item_id,kind,path,filename,content_type,size,uploaded_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (audit_id, rnd, item_id, "finding", path, name, ctype, size, g.user["id"], now()))
        log(audit_id, "photo_add", rnd, item_id, name)
        db.commit()
        return jsonify(ok=True, id=att_id, url=url_for("file_view", att_id=att_id))

    @app.route("/api/attachment/<int:att_id>/delete", methods=["POST"])
    @login_required
    def api_attachment_delete(att_id):
        db = get_db()
        att = db.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not att:
            return jsonify(ok=False, error="ไม่พบไฟล์"), 404
        a = load_audit(att["audit_id"])
        if att["kind"] == "finding":
            if not is_staff() or not round_editable(a, att["round"]):
                return jsonify(ok=False, error="ไม่มีสิทธิ์ลบ"), 403
        else:
            if not is_lab_member(a["lab_id"]) or a["status"] != "r1_reported":
                return jsonify(ok=False, error="ไม่มีสิทธิ์ลบ"), 403
        storage.delete(att["path"])
        db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        log(a["id"], "file_delete", att["round"], att["item_id"], att["filename"])
        db.commit()
        return jsonify(ok=True)

    @app.route("/file/<int:att_id>")
    @login_required
    def file_view(att_id):
        att = get_db().execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
        if not att:
            abort(404)
        a = load_audit(att["audit_id"])
        if not is_staff() and not is_observer(a["id"]) and (
                not is_lab_member(a["lab_id"]) or (att["kind"] == "finding" and not lab_may_see_results(a))):
            abort(403)
        url = storage.signed_url(att["path"])
        if url:
            return redirect(url)
        try:
            data = storage.load(att["path"])
        except FileNotFoundError:
            abort(404)
        return Response(data, mimetype=att["content_type"] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/fill", methods=["POST"])
    @roles(*STAFF)
    def api_fill(audit_id, rnd, el):
        """ให้คะแนนเต็มกับข้อที่ยังไม่ได้ตรวจ (รอบ 1) หรือยืนยันคะแนนที่ยกมา (รอบ 2)"""
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        data = request.get_json(force=True) or {}
        section = data.get("section")
        mode = data.get("mode", "full")
        db = get_db()
        resp = get_responses(audit_id, rnd)
        follow = follow_items(audit_id) if rnd == 2 else None
        n = 0
        for node in ELEMENTS[el]["nodes"]:
            if node["kind"] != "item" or (section and node["section"] != section):
                continue
            if follow is not None and node["id"] not in follow:
                continue
            r = resp.get(node["id"])
            if mode == "confirm":
                if r and r["carried"]:
                    db.execute("UPDATE responses SET carried=0, updated_by=?, updated_at=? WHERE audit_id=? AND round=? AND item_id=?",
                               (g.user["id"], now(), audit_id, rnd, node["id"]))
                    n += 1
                continue
            if rnd == 2 or (r and r["score"] is not None):
                continue
            db.execute("""INSERT INTO responses(audit_id,round,item_id,score,carried,updated_by,updated_at) VALUES(?,?,?,?,0,?,?)
                          ON CONFLICT(audit_id,round,item_id) DO UPDATE SET score=excluded.score, carried=0,
                          updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                       (audit_id, rnd, node["id"], str(node["max"]), g.user["id"], now()))
            n += 1
        log(audit_id, "fill_" + mode, rnd, None, f"element {el} section {section} ({n} ข้อ)")
        db.commit()
        return jsonify(ok=True, changed=n)

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/done", methods=["POST"])
    @roles(*STAFF)
    def api_done(audit_id, rnd, el):
        a = load_audit(audit_id)
        if not round_editable(a, rnd):
            return jsonify(ok=False, error="รอบนี้ปิดการแก้ไขแล้ว"), 409
        data = request.get_json(force=True) or {}
        done = 1 if data.get("done") else 0
        db = get_db()
        db.execute("""INSERT INTO element_status(audit_id,round,element,done,done_by,done_at) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(audit_id,round,element) DO UPDATE SET done=excluded.done, done_by=excluded.done_by,
                      done_at=excluded.done_at""", (audit_id, rnd, el, done, g.user["id"], now()))
        log(audit_id, "element_done" if done else "element_undone", rnd, None, f"element {el}")
        db.commit()
        st = element_status(audit_id, rnd)
        return jsonify(ok=True, done=done, all_done=sum(1 for s in st.values() if s["done"]) == 7)

    @app.route("/api/audit/<int:audit_id>/round/<int:rnd>/element/<int:el>/summary", methods=["POST"])
    @roles(*STAFF)
    def api_el_summary(audit_id, rnd, el):
        data = request.get_json(force=True) or {}
        db = get_db()
        db.execute("""INSERT INTO element_status(audit_id,round,element,done,summary) VALUES(?,?,?,0,?)
                      ON CONFLICT(audit_id,round,element) DO UPDATE SET summary=excluded.summary""",
                   (audit_id, rnd, el, (data.get("summary") or "").strip() or None))
        db.commit()
        return jsonify(ok=True)

    # ---------- workflow (หัวหน้าผู้ตรวจ)
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/report", methods=["POST"])
    @roles("lead", "admin")
    def make_report(audit_id, rnd):
        from reports import build_docx
        a = load_audit(audit_id)
        if rnd == 2 and a["status"] not in ("r2", "r2_reported"):
            flash("ยังไม่ได้ตรวจรอบที่ 2", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        db = get_db()
        if request.form.get("report_date"):
            db.execute(f"UPDATE audits SET r{rnd}_report_date=? WHERE id=?", (request.form["report_date"], audit_id))
        if rnd == 1 and request.form.get("deadline"):
            db.execute("UPDATE audits SET deadline=? WHERE id=?", (request.form["deadline"], audit_id))
        db.commit()
        a = load_audit(audit_id)
        fname, data = build_docx(a, rnd)
        path = storage.save(f"reports/{audit_id}/{secrets.token_hex(4)}_{fname}", data,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        db.insert("INSERT INTO reports(audit_id,round,filename,path,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (audit_id, rnd, fname, path, g.user["id"], now()))
        if rnd == 1 and a["status"] == "r1":
            db.execute("UPDATE audits SET status='r1_reported', r1_report_at=? WHERE id=?", (now(), audit_id))
        if rnd == 2 and a["status"] == "r2":
            db.execute("UPDATE audits SET status='r2_reported', r2_report_at=? WHERE id=?", (now(), audit_id))
        log(audit_id, f"report{rnd}", rnd, None, fname)
        db.commit()
        flash(f"สร้าง Report {rnd} เรียบร้อย" + (" — ห้องปฏิบัติการเข้าดูผลและตอบ CAPA ได้แล้ว" if rnd == 1 else ""), "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=rnd))

    @app.route("/report/<int:report_id>/download")
    @login_required
    def report_download(report_id):
        r = get_db().execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not r:
            abort(404)
        a = load_audit(r["audit_id"])
        if not can_view_audit(a):
            abort(403)
        data = storage.load(r["path"])
        return send_file(io.BytesIO(data), as_attachment=True, download_name=r["filename"],
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.route("/audit/<int:audit_id>/round/<int:rnd>/report/view")
    @login_required
    def report_view(audit_id, rnd):
        """รายงานรูปแบบ PDF (A4) — พิมพ์/บันทึกเป็น PDF จากเบราว์เซอร์"""
        from reports import build_report_data
        a = load_audit(audit_id)
        if not can_view_audit(a):
            abort(403)
        issued = (rnd == 1 and a["status"] != "r1") or (rnd == 2 and a["status"] == "r2_reported")
        if not is_staff() and not issued:
            flash("รายงานฉบับนี้ยังไม่ออก", "error")
            return redirect(url_for("lab_audit", audit_id=audit_id))
        data = build_report_data(a, rnd, with_photos=True)
        return render_template("report_pdf.html", a=a, rnd=rnd, d=data, issued=issued)

    @app.route("/audit/<int:audit_id>/capa/return", methods=["POST"])
    @roles("lead", "admin")
    def capa_return(audit_id):
        a = load_audit(audit_id)
        if a["status"] != "capa_submitted":
            flash("สถานะปัจจุบันส่ง CAPA กลับไม่ได้", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        note = request.form.get("note", "").strip()
        db = get_db()
        db.execute("UPDATE audits SET status='r1_reported', capa_return_note=? WHERE id=?", (note or None, audit_id))
        log(audit_id, "capa_return", None, None, note)
        db.commit()
        flash("ส่ง CAPA กลับให้ห้องปฏิบัติการแก้ไขแล้ว", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id))

    @app.route("/audit/<int:audit_id>/start-round2", methods=["POST"])
    @roles("lead", "admin")
    def start_round2(audit_id):
        a = load_audit(audit_id)
        if a["status"] != "capa_submitted":
            flash("เริ่มรอบที่ 2 ได้หลังจากห้องปฏิบัติการส่ง CAPA แล้วเท่านั้น", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        db = get_db()
        db.execute("UPDATE audits SET status='r2', r2_date=COALESCE(?, r2_date) WHERE id=?",
                   (request.form.get("r2_date") or None, audit_id))
        ensure_round2(audit_id)
        log(audit_id, "start_r2")
        db.commit()
        flash("เริ่มการตรวจรอบที่ 2 แล้ว — ตรวจเฉพาะข้อที่เป็น NC/OBS พร้อม CAPA ของห้องปฏิบัติการ", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=2))

    @app.route("/audit/<int:audit_id>/reopen/<int:rnd>", methods=["POST"])
    @roles("lead", "admin")
    def reopen(audit_id, rnd):
        a = load_audit(audit_id)
        db = get_db()
        if rnd == 1 and a["status"] == "r1_reported":
            db.execute("UPDATE audits SET status='r1' WHERE id=?", (audit_id,))
        elif rnd == 2 and a["status"] == "r2_reported":
            db.execute("UPDATE audits SET status='r2' WHERE id=?", (audit_id,))
        else:
            flash("ไม่สามารถเปิดแก้ไขรอบนี้ได้", "error")
            return redirect(url_for("audit_view", audit_id=audit_id))
        log(audit_id, f"reopen_r{rnd}", rnd, None, request.form.get("reason"))
        db.commit()
        flash(f"เปิดให้แก้ไขผลการตรวจรอบที่ {rnd} อีกครั้ง (เมื่อแก้ไขเสร็จ ให้กดสร้าง Report ใหม่)", "ok")
        return redirect(url_for("audit_view", audit_id=audit_id, round=rnd))

    # ---------- closing meeting
    @app.route("/audit/<int:audit_id>/round/<int:rnd>/closing")
    @roles(*STAFF)
    def closing(audit_id, rnd):
        a = load_audit(audit_id)
        r1, r2 = summary_for(audit_id)
        return render_template("closing.html", a=a, rnd=rnd, r1=r1, r2=r2,
                               resp={1: get_responses(audit_id, 1), 2: get_responses(audit_id, 2)},
                               st=element_status(audit_id, rnd), asg=assignments(audit_id), team=team(audit_id),
                               capa=get_capa(audit_id), atts=get_attachments(audit_id))

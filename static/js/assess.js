/* Assessment page: tap-to-score with autosave, offline retry queue */
(function () {
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const ind = $('#saveind');
  const pending = new Map();   // item_id -> merged payload
  let flushing = false;

  function setInd(state, text) {
    ind.className = 'save-ind ' + (state || '');
    ind.textContent = text;
  }

  function queue(id, data) {
    pending.set(id, Object.assign(pending.get(id) || {}, data));
    setInd('', '● กำลังบันทึก…');
    flush();
  }

  async function flush() {
    if (flushing) return;
    flushing = true;
    try {
      while (pending.size) {
        const [id, data] = pending.entries().next().value;
        pending.delete(id);
        try {
          const j = await postJSON(CFG.base + encodeURIComponent(id), data);
          applyItem(id, j.item);
          applyTotals(j.element);
          const el = document.getElementById('i-' + id);
          el.classList.add('flash-save');
          setTimeout(() => el.classList.remove('flash-save'), 500);
        } catch (err) {
          if (/Failed to fetch|NetworkError|Load failed/i.test(err.message)) {
            pending.set(id, Object.assign(data, pending.get(id) || {}));
            setInd('err', '⚠ ออฟไลน์ – รอส่ง ' + pending.size + ' รายการ');
            flushing = false;
            setTimeout(flush, 4000);
            return;
          }
          toast(err.message, 'error');
          setInd('err', '⚠ บันทึกไม่สำเร็จ');
        }
      }
      const t = new Date();
      setInd('ok', '✓ บันทึกแล้ว ' + t.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' }));
    } finally {
      flushing = false;
    }
  }
  window.addEventListener('beforeunload', e => { if (pending.size) { e.preventDefault(); e.returnValue = ''; } });

  function stateClass(score, max) {
    if (score === '' || score == null) return 'st-none';
    if (score === 'NA') return 'st-na';
    const v = parseInt(score, 10);
    if (v >= max) return 'st-full';
    if (v === 0) return 'st-zero';
    return 'st-part';
  }

  function applyItem(id, it) {
    const el = document.getElementById('i-' + id);
    if (!el || !it) return;
    const max = +el.dataset.max;
    el.dataset.score = it.score == null ? '' : it.score;
    el.dataset.ftype = it.ftype || '';
    el.dataset.carried = (it.carried && el.dataset.follow === '1') ? 1 : 0;
    el.dataset.fu = it.followup || '';
    el.classList.remove('st-none', 'st-na', 'st-full', 'st-zero', 'st-part');
    el.classList.add(stateClass(el.dataset.score, max));
    el.classList.toggle('carried', !!it.carried);
    const cb = $('.carried-badge', el);
    if (cb && !it.carried) cb.remove();
    $$('.sbtn', el).forEach(b => b.classList.toggle('on', b.dataset.v === el.dataset.score));
    $$('.ftypes button', el).forEach(b => b.classList.toggle('on', b.dataset.t === (it.ftype || '')));
    const tg = $('.f-toggle', el);
    const tag = it.ftype ? `<span class="tag ${it.ftype}">${CFG.ftypes[it.ftype].short}</span>` : '';
    tg.innerHTML = tag + `<span class="ftl">${(it.ftype || it.comment) ? 'ข้อค้นพบ' : '+ ข้อค้นพบ / ความเห็น'}</span>`;
    $$('[data-fu] button', el).forEach(b => b.classList.toggle('on', b.dataset.f === (it.followup || '')));
  }

  function lvl(p) { return p >= 90 ? 'good' : p >= 75 ? 'ok' : p >= 50 ? 'warn' : 'bad'; }

  function applyTotals(e) {
    if (!e) return;
    $('#d-got').textContent = e.got;
    $('#d-max').textContent = e.max;
    $('#d-pct').textContent = e.pct;
    $('#d-ans').textContent = e.answered;
    $('#d-nc').textContent = e.NC;
    $('#d-obs').textContent = e.OBS;
    $('#d-sug').textContent = e.SUG;
    if (CFG.rnd === 2) {
      const f = $$('.item[data-follow="1"]'), d = f.filter(i => i.dataset.fu && i.dataset.carried !== '1').length;
      const df = $('#d-fu'); if (df) df.textContent = d;
      $('#d-bar').style.width = (f.length ? Math.round(d * 100 / f.length) : 100) + '%';
    } else {
      $('#d-bar').style.width = e.progress + '%';
    }
    const ring = $('#elring');
    ring.style.setProperty('--p', e.pct);
    ring.style.setProperty('--c', 'var(--' + lvl(e.pct) + ')');
    $('#elpct').textContent = Math.round(e.pct) + '%';
    Object.values(e.sections).forEach(s => {
      const c = document.querySelector(`[data-secscore="${s.code}"]`);
      if (c) c.innerHTML = `${s.got}/${s.max}<br><small>${s.pct}%</small>`;
    });
    const tab = document.querySelector('.el-tab.on .tp');
    if (tab && CFG.rnd === 1) tab.textContent = tab.textContent.replace(/^\d+\/\d+/, e.answered + '/' + e.count);
  }

  if (CFG.editable) {
    // score buttons
    $$('.item').forEach(item => {
      if (item.dataset.locked === '1') return;
      const id = item.dataset.id, max = +item.dataset.max;
      const finding = $('.finding', item);
      const ta = $('.comment', item);
      // photos
      const pin = $('.photo-btn input', item), thumbs = $('[data-thumbs]', item), upl = $('.upl', item);
      const bindDel = th => {
        const b = $('.tdel', th);
        b && b.addEventListener('click', async () => {
          if (!await confirmBox('ลบรูปหลักฐาน?', 'รูปนี้จะถูกลบออกจากข้อค้นพบ', 'ลบ', 'danger')) return;
          try { await postJSON(CFG.attDel + th.dataset.att + '/delete'); th.remove(); setInd('ok', '✓ ลบรูปแล้ว'); }
          catch (e) { toast(e.message, 'error'); }
        });
      };
      thumbs && $$('.thumb', thumbs).forEach(bindDel);
      pin && pin.addEventListener('change', async () => {
        const files = Array.from(pin.files || []);
        pin.value = '';
        for (let k = 0; k < files.length; k++) {
          upl.textContent = `กำลังอัปโหลด ${k + 1}/${files.length}…`;
          try {
            const fd = new FormData();
            fd.append('file', await shrinkImage(files[k]));
            const j = await postForm(CFG.photo + encodeURIComponent(id) + '/photo', fd);
            const th = document.createElement('div');
            th.className = 'thumb'; th.dataset.att = j.id;
            th.innerHTML = `<a href="${j.url}" target="_blank"><img src="${j.url}" alt="รูปหลักฐาน"></a><button type="button" class="tdel" title="ลบรูป">×</button>`;
            thumbs.appendChild(th); bindDel(th);
          } catch (e) { toast(e.message, 'error'); }
        }
        upl.textContent = files.length ? '✓ อัปโหลดแล้ว' : '';
        setTimeout(() => upl.textContent = '', 2500);
      });
      $$('.sbtn', item).forEach(b => b.addEventListener('click', () => {
        const v = b.dataset.v;
        const data = { score: v };
        // optimistic UI
        $$('.sbtn', item).forEach(x => x.classList.toggle('on', x === b));
        item.classList.remove('st-none', 'st-na', 'st-full', 'st-zero', 'st-part', 'carried');
        item.classList.add(stateClass(v, max));
        const lost = v !== 'NA' && parseInt(v, 10) < max;
        if (lost && !item.dataset.ftype) {
          data.ftype = 'NC';
          finding.style.display = '';
          $$('.ftypes button', item).forEach(x => x.classList.toggle('on', x.dataset.t === 'NC'));
          setTimeout(() => ta.focus({ preventScroll: false }), 50);
        } else if (!lost && item.dataset.ftype === 'NC' && !ta.value.trim()) {
          data.ftype = null;
          finding.style.display = 'none';
        }
        queue(id, data);
      }));
      // finding toggle
      $('.f-toggle', item).addEventListener('click', () => {
        const show = finding.style.display === 'none';
        finding.style.display = show ? '' : 'none';
        if (show) ta.focus();
      });
      $$('.ftypes button', item).forEach(b => b.addEventListener('click', () => {
        const t = b.classList.contains('on') ? null : b.dataset.t;
        $$('.ftypes button', item).forEach(x => x.classList.toggle('on', x === b && !!t));
        queue(id, { ftype: t, comment: ta.value });
      }));
      let tmr;
      ta.addEventListener('input', () => {
        clearTimeout(tmr);
        setInd('', '● กำลังพิมพ์…');
        tmr = setTimeout(() => {
          const d = { comment: ta.value };
          if (ta.value.trim() && !item.dataset.ftype) {
            const lostNow = item.dataset.score !== '' && item.dataset.score !== 'NA' && +item.dataset.score < max;
            d.ftype = lostNow ? 'NC' : 'SUG';
          }
          queue(id, d);
        }, 700);
      });
      ta.addEventListener('blur', () => { clearTimeout(tmr); });
      $$('.quick button', item).forEach(q => q.addEventListener('click', () => {
        ta.value = (ta.value.trim() ? ta.value.trim() + ' ' : '- ') + q.dataset.q;
        ta.dispatchEvent(new Event('input'));
        ta.focus();
      }));
      // follow-up (round 2)
      $$('[data-fu] button', item).forEach(b => b.addEventListener('click', () => {
        $$('[data-fu] button', item).forEach(x => x.classList.toggle('on', x === b));
        const d = { followup: b.dataset.f, confirm: true };
        if (b.dataset.f === 'resolved' && item.dataset.score !== 'NA' && (item.dataset.score === '' || +item.dataset.score < max)) {
          d.score = String(max);  // แก้ไขแล้ว -> เสนอคะแนนเต็มอัตโนมัติ (ปรับได้)
        }
        queue(id, d);
        if (d.score) {
          $$('.sbtn', item).forEach(x => x.classList.toggle('on', x.dataset.v === d.score));
          item.classList.remove('st-none', 'st-na', 'st-full', 'st-zero', 'st-part', 'carried');
          item.classList.add('st-full');
        }
      }));
      const fn = $('.fnote', item);
      if (fn) {
        let t2;
        fn.addEventListener('input', () => { clearTimeout(t2); t2 = setTimeout(() => queue(id, { fnote: fn.value }), 700); });
      }
    });

    // section tools
    $$('[data-fill-sec]').forEach(b => b.addEventListener('click', async () => {
      const ok = await confirmBox('ให้คะแนนเต็ม', `ให้คะแนนเต็มกับทุกข้อที่ <b>ยังไม่ได้ตรวจ</b> ในหัวข้อ ${b.dataset.fillSec}?<br><small>ข้อที่ให้คะแนนไว้แล้วจะไม่ถูกเปลี่ยน</small>`);
      if (!ok) return;
      try { await postJSON(CFG.fill, { section: b.dataset.fillSec, mode: 'full' }); location.reload(); }
      catch (e) { toast(e.message, 'error'); }
    }));
    $$('[data-confirm-sec]').forEach(b => b.addEventListener('click', async () => {
      try { const j = await postJSON(CFG.fill, { section: b.dataset.confirmSec, mode: 'confirm' }); toast('ยืนยัน ' + j.changed + ' ข้อ', 'ok'); setTimeout(() => location.reload(), 400); }
      catch (e) { toast(e.message, 'error'); }
    }));

    // done
    const db = $('#doneBtn');
    db && db.addEventListener('click', async () => {
      const isDone = db.dataset.done === '1';
      if (!isDone) {
        const ans = CFG.rnd === 2 ? CFG.items : +$('#d-ans').textContent;
        const carried = 0;
        const follow = $$('.item[data-follow="1"]').filter(i => !$('[data-fu] button.on', i)).length;
        const nofind = $$('.item').filter(i => i.dataset.locked !== '1' && $('.comment', i) && i.dataset.score !== '' && i.dataset.score !== 'NA' && +i.dataset.score < +i.dataset.max && !$('.comment', i).value.trim()).length;
        let warn = '';
        if (ans < CFG.items) warn += `<li>ยังไม่ได้ให้คะแนน <b>${CFG.items - ans}</b> ข้อ</li>`;
        if (carried) warn += `<li>มีคะแนนที่ยกมาจากรอบ 1 ยังไม่ยืนยัน <b>${carried}</b> ข้อ</li>`;
        if (follow) warn += `<li>ยังไม่ระบุผลติดตาม NC/OBS <b>${follow}</b> ข้อ</li>`;
        if (nofind) warn += `<li>มี <b>${nofind}</b> ข้อที่ไม่ได้คะแนนเต็มแต่ยังไม่เขียนข้อค้นพบ</li>`;
        const ok = await confirmBox('ยืนยันตรวจองค์ประกอบนี้เสร็จ', warn ? `<p>โปรดตรวจสอบ:</p><ul>${warn}</ul><p>ต้องการยืนยันต่อหรือไม่?</p>` : 'ทุกข้อได้รับการตรวจแล้ว ✓', 'ยืนยัน', 'good');
        if (!ok) return;
      }
      try {
        const j = await postJSON(CFG.done, { done: !isDone });
        db.dataset.done = j.done ? '1' : '0';
        db.className = 'btn ' + (j.done ? 'good' : 'primary');
        db.textContent = j.done ? '✓ ตรวจเสร็จแล้ว' : 'ยืนยันตรวจองค์นี้เสร็จ';
        const tab = document.querySelector('.el-tab.on');
        tab.classList.toggle('done', !!j.done);
        tab.querySelector('.n').textContent = j.done ? '✓' : tab.dataset.el;
        if (j.done) toast(j.all_done ? 'ครบทั้ง 7 องค์ประกอบแล้ว — หัวหน้าผู้ตรวจสร้าง Report ได้ทันที' : 'บันทึกสถานะตรวจเสร็จแล้ว', 'ok');
      } catch (e) { toast(e.message, 'error'); }
    });

    // element summary
    const es = $('#elsummary');
    let t3;
    es && es.addEventListener('input', () => {
      clearTimeout(t3);
      t3 = setTimeout(() => postJSON(CFG.summary, { summary: es.value }).then(() => setInd('ok', '✓ บันทึกสรุปแล้ว')).catch(e => toast(e.message, 'error')), 800);
    });
  }

  // filter & search
  let mode = CFG.rnd === 2 ? 'follow' : 'all';
  function applyFilter() {
    const q = ($('#search').value || '').trim().toLowerCase();
    $$('.item').forEach(i => {
      const s = i.dataset.score, max = +i.dataset.max;
      let ok = true;
      if (mode === 'all') ok = true;
      if (mode === 'todo') ok = s === '';
      if (mode === 'lost') ok = s !== '' && s !== 'NA' && +s < max;
      if (mode === 'find') ok = !!i.dataset.ftype || !!(($('.comment', i) || {}).value || '').trim();
      if (mode === 'follow') ok = i.dataset.follow === '1';
      if (mode === 'fu-todo') ok = i.dataset.follow === '1' && (!i.dataset.fu || i.dataset.carried === '1');
      if (q && !i.dataset.text.includes(q)) ok = false;
      i.style.display = ok ? '' : 'none';
    });
    const filtering = mode !== 'all' || q;
    $$('.section').forEach(sec => {
      const any = $$('.item', sec).some(i => i.style.display !== 'none');
      sec.style.display = (!filtering || any) ? '' : 'none';
      if (filtering && any) sec.classList.remove('collapsed');
      $$('.heading,.opt-note,.sec-tools', sec).forEach(h => h.style.display = (filtering && !h.classList.contains('sec-tools')) ? 'none' : '');
    });
  }
  $$('#filter button').forEach(b => b.addEventListener('click', () => {
    $$('#filter button').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); mode = b.dataset.f; applyFilter();
  }));
  $('#search').addEventListener('input', applyFilter);
  applyFilter();
  $('#expandAll').addEventListener('click', () => {
    const secs = $$('.section');
    const collapse = !secs.some(s => s.classList.contains('collapsed'));
    secs.forEach(s => s.classList.toggle('collapsed', collapse));
  });
  // keep the active tab visible
  const on = document.querySelector('.el-tab.on');
  on && on.scrollIntoView({ inline: 'center', block: 'nearest' });
  if (location.hash) {
    const t = document.querySelector(location.hash);
    if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('flash-save'); }
  }
})();

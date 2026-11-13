/* Shared helpers: toast, modal, fetch JSON */
(function () {
  const flashes = document.getElementById('flashes');
  // ข้อความแจ้งเตือนทั่วไปหายเอง · ข้อความผิดพลาดค้างไว้จนกว่าจะแตะปิด
  setTimeout(() => flashes && flashes.querySelectorAll('.flash:not(.error)').forEach(f => f.remove()), 6000);
  flashes && flashes.addEventListener('click', e => { const f = e.target.closest('.flash'); f && f.remove(); });

  window.toast = function (msg, cat) {
    const d = document.createElement('div');
    d.className = 'flash ' + (cat || '');
    d.textContent = msg;
    flashes.appendChild(d);
    setTimeout(() => d.remove(), 4000);
  };

  const bg = document.getElementById('modal');
  window.modal = function ({ title, html, actions }) {
    return new Promise(resolve => {
      document.getElementById('modal-title').textContent = title || '';
      document.getElementById('modal-body').innerHTML = html || '';
      const box = document.getElementById('modal-actions');
      box.innerHTML = '';
      (actions || [{ label: 'ตกลง', cls: 'primary', value: true }]).forEach(a => {
        const b = document.createElement('button');
        b.className = 'btn ' + (a.cls || '');
        b.textContent = a.label;
        b.onclick = () => { bg.classList.remove('show'); resolve(a.value); };
        box.appendChild(b);
      });
      bg.classList.add('show');
    });
  };
  bg && bg.addEventListener('click', e => { if (e.target === bg) bg.classList.remove('show'); });

  window.confirmBox = (title, html, okLabel, okCls) =>
    modal({ title, html, actions: [{ label: 'ยกเลิก', value: false }, { label: okLabel || 'ยืนยัน', cls: okCls || 'primary', value: true }] });

  const CSRF = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  window.CSRF = CSRF;
  document.querySelectorAll('form').forEach(f => {
    if ((f.method || '').toLowerCase() === 'post' && !f.querySelector('input[name=_csrf]')) {
      const i = document.createElement('input'); i.type = 'hidden'; i.name = '_csrf'; i.value = CSRF; f.appendChild(i);
    }
  });

  window.postForm = async function (url, formData) {
    const r = await fetch(url, { method: 'POST', headers: { 'X-CSRF-Token': CSRF }, body: formData, credentials: 'same-origin' });
    let j = {};
    try { j = await r.json(); } catch (e) { }
    if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  };

  /* ย่อรูปก่อนอัปโหลด (ด้านยาวสุด 1600px, JPEG) — ประหยัดเน็ตและพื้นที่ */
  window.shrinkImage = function (file, maxSide = 1600, quality = 0.82) {
    return new Promise(resolve => {
      if (!file.type.startsWith('image/') || file.type === 'image/gif') return resolve(file);
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        let { width: w, height: h } = img;
        const k = Math.min(1, maxSide / Math.max(w, h));
        const c = document.createElement('canvas');
        c.width = Math.round(w * k); c.height = Math.round(h * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(b => { URL.revokeObjectURL(url); resolve(b ? new File([b], (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' }) : file); }, 'image/jpeg', quality);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  };

  window.postJSON = async function (url, data) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify(data || {}), credentials: 'same-origin' });
    let j = {};
    try { j = await r.json(); } catch (e) { }
    if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  };

  // forms that need confirmation: <form data-confirm="...">
  document.querySelectorAll('form[data-confirm]').forEach(f => {
    f.addEventListener('submit', async e => {
      if (f.dataset.ok) return;
      e.preventDefault();
      const ok = await confirmBox(f.dataset.confirmTitle || 'ยืนยัน', f.dataset.confirm, f.dataset.okLabel, f.dataset.okCls);
      if (ok) { f.dataset.ok = 1; f.requestSubmit ? f.requestSubmit() : f.submit(); }
    });
  });
})();

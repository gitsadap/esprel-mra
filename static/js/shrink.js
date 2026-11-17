/* ย่อรูปก่อนส่งฟอร์ม: ช่องแนบไฟล์ทุกช่องที่มี name (ใบสมัคร FM-001, ฉบับลงนาม ฯลฯ)
   ด้านยาวสุด 1600px, JPEG — ไฟล์เล็กลงราว 10 เท่า และไม่ติดเพดาน 4.5 MB ของ Vercel
   (ช่องอัปโหลดรูปหลักฐาน/CAPA ที่ส่งด้วย fetch ย่อเองอยู่แล้ว) · ใส่ data-noshrink เพื่อไม่ย่อ */
(function () {
  if (!window.shrinkImage) {
    window.shrinkImage = function (file, maxSide, quality) {
      maxSide = maxSide || 1600; quality = quality || 0.82;
      return new Promise(function (resolve) {
        if (!file.type || file.type.indexOf('image/') !== 0 || file.type === 'image/gif') return resolve(file);
        var img = new Image(), url = URL.createObjectURL(file);
        img.onload = function () {
          var k = Math.min(1, maxSide / Math.max(img.width, img.height)), c = document.createElement('canvas');
          c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          c.toBlob(function (b) {
            URL.revokeObjectURL(url);
            resolve(b && b.size < file.size ? new File([b], (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' }) : file);
          }, 'image/jpeg', quality);
        };
        img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
        img.src = url;
      });
    };
  }
  var busy = 0;
  document.querySelectorAll('form input[type=file][name]:not([data-noshrink])').forEach(function (inp) {
    inp.addEventListener('change', async function () {
      var files = Array.prototype.slice.call(inp.files || []);
      if (window.DataTransfer && files.some(function (f) { return f.type && f.type.indexOf('image/') === 0; })) {
        busy++;
        try {
          var dt = new DataTransfer();
          for (var i = 0; i < files.length; i++) dt.items.add(await window.shrinkImage(files[i]));
          inp.files = dt.files;
        } catch (e) { /* ใช้ไฟล์เดิม */ }
        busy--;
      }
      if (inp.dataset.autosubmit) inp.form.submit();
    });
    inp.form.addEventListener('submit', function (e) {
      if (busy) { e.preventDefault(); e.stopImmediatePropagation(); alert('กำลังย่อรูป รอสักครู่แล้วกดอีกครั้ง'); }
    }, true);
  });
})();

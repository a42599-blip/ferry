/* 說明頁（服務條款／隱私權政策／退費政策／聯絡我們）共用：
   ① 語言跟主站同步（localStorage: fy_lang）
   ② 文字全部來自 /locales/*.json（繁中／简中／EN 三語，鐵律一之二）
   ③ 不寫死任何中文，換頁面只要換 <body data-doc="..."> */
(function () {
  'use strict';
  var KEY = 'fy_lang';
  var q = new URLSearchParams(location.search);
  var lang = q.get('lang') || localStorage.getItem(KEY) || 'zh-Hant';
  var docId = document.body.getAttribute('data-doc') || 'terms';

  function apply(L) {
    function t(k) { return (L && L[k] !== undefined) ? L[k] : ''; }
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n'));
      if (v) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      el.innerHTML = t(el.getAttribute('data-i18n-html'));
    });
    var title = t('doc_' + docId + '_title');
    if (title) document.title = title + ' · ' + t('brand');
    document.documentElement.lang = lang;
    var on = document.querySelector('#lang button[data-lang="' + lang + '"]');
    if (on) on.classList.add('on');
  }

  fetch('/locales/' + lang + '.json')
    .then(function (r) { return r.json(); })
    .then(apply)
    .catch(function () { apply({}); });

  document.querySelectorAll('#lang button').forEach(function (b) {
    b.addEventListener('click', function () {
      localStorage.setItem(KEY, b.getAttribute('data-lang'));
      location.href = location.pathname + '?lang=' + b.getAttribute('data-lang');
    });
  });
})();

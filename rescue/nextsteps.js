/* ============================================================================
 * IFF Operator Console  --  "Next Steps" add-on  (deck behaviour)
 * Build:   nextsteps-v3
 * Author:  Tomas Chadwick <tchadwick@hks.harvard.edu>
 * Injected just before </body> by console_nextsteps.py, after the dialog markup
 * and after the upstream page script. Self-contained: owns only its own dialog,
 * touches no upstream globals. The upstream file is unchanged.
 *
 * Turns the guide into a slide deck: PREV / NEXT buttons, clickable dots, and
 * Left/Right arrows while the dialog is open. Opening (button or "N") shows
 * slide 1. "Esc" closes natively.
 * ========================================================================== */
(function () {
  var btn = document.getElementById('btnSteps');
  var dlg = document.getElementById('nextModal');
  if (!btn || !dlg) return;

  var slides = Array.prototype.slice.call(dlg.querySelectorAll('.ns-slide'));
  var prevB = document.getElementById('nsPrev');
  var nextB = document.getElementById('nsNext');
  var dotsEl = document.getElementById('nsDots');
  var countEl = document.getElementById('nsCount');
  var i = 0;

  // build one dot per slide
  var dots = slides.map(function (s, k) {
    var d = document.createElement('button');
    d.type = 'button';
    d.className = 'ns-dot';
    d.setAttribute('role', 'tab');
    d.setAttribute('aria-label', 'Slide ' + (k + 1) + (s.dataset.title ? ': ' + s.dataset.title : ''));
    d.title = s.dataset.title || ('Slide ' + (k + 1));
    d.addEventListener('click', function () { go(k); });
    dotsEl.appendChild(d);
    return d;
  });

  function go(n) {
    i = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function (s, k) { s.classList.toggle('active', k === i); });
    dots.forEach(function (d, k) {
      d.classList.toggle('on', k === i);
      d.setAttribute('aria-selected', k === i ? 'true' : 'false');
    });
    if (countEl) countEl.textContent = (i + 1) + ' / ' + slides.length;
    if (prevB) prevB.disabled = i === 0;
    if (nextB) nextB.disabled = i === slides.length - 1;
    var active = slides[i];
    if (active) active.scrollTop = 0;
  }

  if (prevB) prevB.addEventListener('click', function () { go(i - 1); });
  if (nextB) nextB.addEventListener('click', function () { go(i + 1); });

  function open() {
    go(0);                                   // always start at slide 1 for the demo
    if (typeof dlg.showModal === 'function' && !dlg.open) dlg.showModal();
  }

  btn.addEventListener('click', open);

  // "N" opens the deck; arrows page through it while it's open.
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
    if (!dlg.open) {
      if ((e.key === 'n' || e.key === 'N') && !typing && !document.querySelector('dialog[open]')) open();
      return;
    }
    if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1); }
  });

  go(0);                                      // initial state
})();

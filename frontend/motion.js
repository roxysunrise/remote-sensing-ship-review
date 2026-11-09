/* ============================================================================
   MOTION — the interaction layer for the Prime-style shell.
   Loaded after app.js so it can decorate the existing views without owning
   any application state. Everything here is progressive: with scripting off
   the interface is complete, only the movement is missing.
   ========================================================================== */
(function () {
  'use strict';

  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.documentElement.classList.add('js');

  /* ------------------------------------------------------------- tab ink */
  var tabs = document.getElementById('rail');
  var ink = tabs && tabs.querySelector('.tab-ink');

  function moveInk() {
    if (!tabs || !ink) return;
    var active = tabs.querySelector('button.on, a.on');
    if (!active) { ink.style.width = '0px'; return; }
    var tr = tabs.getBoundingClientRect();
    var br = active.getBoundingClientRect();
    ink.style.width = br.width + 'px';
    ink.style.transform = 'translateX(' + (br.left - tr.left + tabs.scrollLeft) + 'px)';
    tabs.querySelectorAll('button[data-view]').forEach(function (b) {
      b.setAttribute('aria-current', b === active ? 'page' : 'false');
    });
  }

  if (tabs) {
    new MutationObserver(moveInk).observe(tabs, {
      subtree: true,
      attributes: true,
      attributeFilter: ['class']
    });
    window.addEventListener('resize', moveInk);
    tabs.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-view]');
      if (btn) setTimeout(moveInk, 0);
    });

    /* left / right arrows walk the tab row like a nav bar should */
    tabs.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var items = Array.prototype.slice.call(tabs.querySelectorAll('button[data-view], a.rail-link'));
      var i = items.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      var next = items[(i + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length];
      next.focus();
    });
  }
  setTimeout(moveInk, 80);
  window.addEventListener('load', moveInk);

  /* ------------------------------------------------------------- marquee */
  var track = document.querySelector('.st-track');
  if (track && !track.dataset.cloned) {
    track.innerHTML += track.innerHTML;   /* two identical runs -> -50% loop */
    track.dataset.cloned = '1';
  }

  /* --------------------------------------------------------------- clock */
  var clock = document.getElementById('status-clock');
  function tick() {
    if (!clock) return;
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, '0'); };
    clock.textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  tick();
  if (clock) setInterval(tick, 1000);

  /* --------------------------------------------------------- count-up */
  var running = new WeakMap();

  function countUp(el) {
    var raw = (el.textContent || '').trim();
    if (!/^\d+$/.test(raw)) return;          /* only plain integers; leave 12/12, —, 0.93 alone */
    var target = Number(raw);
    if (!isFinite(target) || target === 0) return;
    if (running.has(el)) cancelAnimationFrame(running.get(el));
    if (reduce) return;

    el.textContent = '0';
    var start = performance.now();
    var dur = 620 + Math.min(target, 500);
    function frame(now) {
      var t = Math.min(1, (now - start) / dur);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(target * eased));
      if (t < 1) running.set(el, requestAnimationFrame(frame));
      else { el.textContent = raw; running.delete(el); }
    }
    running.set(el, requestAnimationFrame(frame));
  }

  function animateNumbers(view) {
    if (!view) return;
    view.querySelectorAll('.stat-line .s b, [data-count]').forEach(countUp);
  }

  /* ------------------------------------------------------ detail swap */
  function watchDetail(id) {
    var host = document.getElementById(id);
    if (!host) return;
    new MutationObserver(function () {
      host.classList.remove('flash-in');
      void host.offsetWidth;
      host.classList.add('flash-in');
    }).observe(host, { childList: true });
  }
  watchDetail('wb-detail');
  watchDetail('dt-detail');

  /* --------------------------------------------------- detect busy / count */
  var dtStage = document.getElementById('dt-stage');
  var dtBusy = document.getElementById('dt-busy');
  if (dtStage && dtBusy) {
    var scanTimer = 0;
    var syncScan = function () {
      if (!dtBusy.classList.contains('hidden')) {
        clearTimeout(scanTimer);
        dtStage.classList.add('scanning');
      } else {
        // mock 检测可能在 100ms 内完成；让扫描线至少走完一小段，反馈才看得见
        clearTimeout(scanTimer);
        scanTimer = setTimeout(function () { dtStage.classList.remove('scanning'); }, 700);
      }
    };
    new MutationObserver(syncScan).observe(dtBusy, { attributes: true, attributeFilter: ['class'] });
    syncScan();
  }

  var dtCount = document.getElementById('dt-count');
  if (dtCount) {
    new MutationObserver(function () {
      dtCount.classList.remove('bump');
      void dtCount.offsetWidth;
      dtCount.classList.add('bump');
    }).observe(dtCount, { childList: true, characterData: true, subtree: true });
  }

  var emptyPick = document.getElementById('dt-empty-pick');
  if (emptyPick) {
    emptyPick.addEventListener('click', function () {
      var pick = document.getElementById('dt-pick');
      if (pick) pick.click();
    });
  }

  /* --------------------------------------------------------- view hook */
  function afterView() {
    var view = document.querySelector('.view.on');
    if (!view) return;
    moveInk();
    animateNumbers(view);
  }

  if (typeof window.switchView === 'function' && !window.switchView.__motion) {
    var original = window.switchView;
    var wrapped = function (name) {
      original(name);
      afterView();
    };
    wrapped.__motion = true;
    window.switchView = wrapped;
  }

  var main = document.querySelector('main');
  if (main) {
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        if (records[i].target.classList && records[i].target.classList.contains('view') && records[i].target.classList.contains('on')) {
          afterView();
          break;
        }
      }
    }).observe(main, { subtree: true, attributes: true, attributeFilter: ['class'] });
  }

  /* when the login gate hands over, restart the arrival animations once */
  var app = document.getElementById('app');
  if (app) {
    new MutationObserver(function () {
      if (app.classList.contains('hidden')) return;
      var view = document.querySelector('.view.on');
      if (!view) return;
      var parts = view.querySelectorAll(
        '.view-head .kicker, .view-head h2, .view-head .vh-side, .grid-2 > *, .grid-3 > *, .wb-side, .canvas-wrap'
      );
      parts.forEach(function (el) { el.style.animation = 'none'; });
      void view.offsetWidth;
      parts.forEach(function (el) { el.style.animation = ''; });
      afterView();
    }).observe(app, { attributes: true, attributeFilter: ['class'] });
  }
})();

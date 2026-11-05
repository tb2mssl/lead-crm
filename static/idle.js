/* Logs the person out after 15 minutes of no activity. The server enforces the same limit; this just
   sends them to the login page on time, and keeps the session alive while they are really using the page. */
(function () {
  var IDLE = 15 * 60 * 1000, PING = 60 * 1000, loginUrl = document.body.dataset.login || '/login';
  var last = Date.now(), pinged = Date.now();
  ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(function (e) {
    addEventListener(e, function () { last = Date.now(); }, { passive: true });
  });
  function out() {
    fetch('/logout', { method: 'POST', headers: { 'X-Requested-With': 'fetch' }, keepalive: true })
      .catch(function () {}).then(function () { location.href = loginUrl + '?n=idle'; });
  }
  setInterval(function () {
    var now = Date.now();
    if (now - last >= IDLE) return out();
    if (last > pinged && now - pinged >= PING) {
      pinged = now;
      fetch('/api/ping', { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { if (r.status === 401) location.href = loginUrl + '?n=idle'; }).catch(function () {});
    }
  }, 15000);
  document.addEventListener('visibilitychange', function () { // laptop woke up / tab came back
    if (!document.hidden && Date.now() - last >= IDLE) out();
  });
})();

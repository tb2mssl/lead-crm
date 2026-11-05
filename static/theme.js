/* Dark / light screen switch. Remembers the choice; first visit follows the device setting. */
(function () {
  var KEY = 'crm-theme', root = document.documentElement;
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function initial() {
    var s = saved();
    return s === 'dark' || s === 'light' ? s : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
  function paint(theme) {
    root.setAttribute('data-theme', theme);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.textContent = theme === 'dark' ? '\u2600 Light' : '\u263E Dark';
      b.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
      b.title = theme === 'dark' ? 'Switch to the light screen' : 'Switch to the dark screen';
    });
  }
  paint(initial()); // runs in <head>, before the page is drawn, so there is no flash
  document.addEventListener('DOMContentLoaded', function () {
    paint(root.getAttribute('data-theme'));
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(KEY, next); } catch (e) {}
        paint(next);
        document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
      });
    });
  });
})();

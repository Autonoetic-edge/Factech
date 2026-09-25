(function () {
  const TEXT = 'Page failed to load. Restart the gateway.';
  function check() {
    if (document.documentElement.dataset.ready === '1') return;
    const box = document.createElement('p');
    box.id = 'loadFail';
    box.setAttribute('role', 'alert');
    box.textContent = TEXT;
    box.style.cssText = 'margin:0;padding:12px 16px;background:#b3261e;color:#fff;' +
      'font:600 15px/1.4 system-ui,sans-serif;text-align:center';
    document.body.insertBefore(box, document.body.firstChild);
    for (const b of document.querySelectorAll('button')) b.disabled = true;
  }
  if (document.readyState === 'complete') check();
  else window.addEventListener('load', check, { once: true });
})();

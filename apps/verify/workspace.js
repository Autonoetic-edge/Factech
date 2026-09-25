// Minimal signed-in destination after a face check. It shows only what the server
// says about this account; nothing is passed from the face-check page.
const $ = (id) => document.getElementById(id);
const get = (path, headers = {}) => fetch(path, { headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });

function show(title, lead, action) {
  $('ws-title').innerHTML = title; // static strings from this file only
  $('ws-lead').textContent = lead;
  if (action) { $('ws-action').textContent = action[0]; $('ws-action').href = action[1]; $('ws-action').hidden = false; }
}

try {
  const me = await get('/auth/session');
  if (me.status === 401) {
    $('account-line').textContent = 'Not signed in';
    show('Your sign-in<br><em>has timed out.</em>', 'Sign in again to see your workspace.', ['Sign in', '/auth/login']);
  } else {
    const { subject_id: subject, csrf_token: csrf } = await me.json();
    const list = await get(`/v2/subjects/${subject}/templates`);
    const templates = list.status === 200 ? (await list.json()).templates : null;
    if (!Array.isArray(templates)) throw new Error('status');
    $('account-line').textContent = 'Signed in';
    if (templates.length) show('Your face check<br><em>is set up.</em>', 'You can run a face check whenever you are asked to confirm it is you.', ['Run a face check', '/']);
    else show('Set up<br><em>your face check.</em>', 'It takes about a minute, and you stay in control throughout.', ['Set up now', '/']);

    $('ws-signout').hidden = false;
    $('ws-signout').addEventListener('click', async () => {
      $('ws-signout').disabled = true;
      try {
        const response = await fetch('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, credentials: 'same-origin' });
        if (!response.ok) throw new Error('logout');
        location.assign('/');
      } catch {
        $('ws-lead').textContent = 'Sign-out did not finish. Please try again before another person uses this browser.';
        $('ws-signout').disabled = false;
      }
    });
  }
} catch {
  $('account-line').textContent = 'Couldn’t check';
  show('We couldn’t<br><em>load your account.</em>', 'Check your connection and reload this page.');
}

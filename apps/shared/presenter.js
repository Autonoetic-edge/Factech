export function presenterEnabled(search) {
  try { return new URLSearchParams(search || '').get('presenter') === '1'; } catch (e) { return false; }
}

export function signalVerdict(s) {
  if (!s) return 'unknown';
  if (s.advisory === true || s.ok === null) return 'advisory';
  return s.ok === true ? 'passed' : s.ok === false ? 'failed' : 'unknown';
}

// Fail-closed local-origin guard.
//
// The harness drives a real browser AS THE USER. To keep it from being pointed at arbitrary
// remote sites (drive-by navigation, exfiltration, the prior spec's machine-scoped threat model),
// only local origins and file:// are permitted by default. Remote targets require an explicit,
// deliberate --allow-remote. This is defense-in-depth, not a hard sandbox: a scenario file is
// agent-authored and reviewable, carrying the same trust as any Bash command Claude already runs.

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]', '0.0.0.0']);

export function guardUrl(url, allowRemote) {
  if (allowRemote) return url;
  let u;
  try { u = new URL(url); } catch { throw new Error(`invalid URL: ${url}`); }
  const local =
    u.protocol === 'file:' ||
    ((u.protocol === 'http:' || u.protocol === 'https:') && LOCAL_HOSTS.has(u.hostname));
  if (!local) {
    throw new Error(`refused non-local URL "${url}" — pass --allow-remote to override (fail-closed by design)`);
  }
  return url;
}

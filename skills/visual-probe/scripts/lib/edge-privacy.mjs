// edge-privacy.mjs — flags that keep an automation Edge instance OFF the user's account.
//
// THE TRAP: system Edge (channel:msedge) launched on Windows performs "implicit sign-in" — it signs
// the profile into whatever Microsoft/AAD account the OS user holds, and defaults browser SYNC ON.
// A *dedicated* profile does NOT save you (session.mjs already uses one): Edge signs THAT profile in
// too, silently pulling the account's history, passwords, and bookmarks into our throwaway profile —
// and re-enabling sync on every launch. This browser is for agentic dev only; it must never touch the
// user's account. Enforce that at every launch path so no launcher can forget.
//
// Two groups, split by a Chromium command-line quirk:
//
//   EDGE_NO_SYNC — DISTINCT switches, safe to append to ANY launch (incl. a Playwright
//   chromium.launch, which passes its own big --disable-features that a second occurrence would
//   OVERWRITE — so these must not be --disable-features):
//     --disable-sync                  hard-off the sync engine (the switch behind the SyncDisabled
//                                     policy) — the load-bearing fix: no account data transfers even
//                                     if a sign-in slips through.
//     --disable-background-networking cut the network path sync/telemetry/component-update ride on —
//                                     a belt-and-suspenders guarantee. Harmless for localhost dev.
//
//   EDGE_NO_IMPLICIT_SIGNIN — a --disable-features switch, so append it ONLY to the raw msedge.exe
//   spawn (session.mjs), which has no Playwright default to clobber. Stops the OS-account auto-sign-in
//   at the source (the feature behind the ImplicitSigninEnabled / BrowserSignin policies).
export const EDGE_NO_SYNC = ['--disable-sync', '--disable-background-networking'];
export const EDGE_NO_IMPLICIT_SIGNIN = '--disable-features=msImplicitSignin';

// The full set for a raw (non-Playwright) Edge spawn.
export const EDGE_PRIVACY_ARGS = [...EDGE_NO_SYNC, EDGE_NO_IMPLICIT_SIGNIN];

// Reading an invite link someone pasted (the "Were you invited?" field on
// /families/new) back into its code, so the page can open /join/<code> here.
//
// Pure and erasable TypeScript with no imports, so `node --test` loads it
// directly (__tests__/inviteLink.test.mjs).
//
// Invite codes are what api/db/families.py makes with secrets.token_urlsafe(12)
// (16 characters of A-Z a-z 0-9 _ -); the mock backend's are 12 lowercase
// letters and digits, and its demo invite is "riveras-demo". So a code is
// 8 to 64 of those URL-safe characters: anything else is not one of ours.
const CODE = /^[A-Za-z0-9_-]{8,64}$/;

/** The path an invite link opens: /join/<code>, with or without a trailing slash. */
const JOIN_PATH = /^\/join\/([^/]+)\/?$/;

/**
 * The invite code in `input`, or null when it is not an invite link.
 *
 * Accepts a full http(s) URL of any origin whose path is /join/<code> (a
 * trailing slash, a query and a hash are ignored), or a bare code. Only the
 * code is kept: the caller opens it on this site, never the pasted origin.
 * Rejects other schemes (javascript:, data:, ...), other paths, and codes
 * with characters an invite code never has.
 */
export function parseInviteCode(input: string): string | null {
  const text = typeof input === "string" ? input.trim() : "";
  if (!text) return null;
  if (CODE.test(text)) return text;
  if (!/^https?:\/\//i.test(text)) return null;

  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const m = JOIN_PATH.exec(url.pathname);
  if (!m) return null;
  let code: string;
  try {
    code = decodeURIComponent(m[1]);
  } catch {
    return null;
  }
  return CODE.test(code) ? code : null;
}

/**
 * How the stage asks for an image it will read back from a canvas.
 *
 * The API serves a family's media only to a signed-in member
 * (GET /families/{fid}/media/{id} checks the session cookie), and it lives
 * on another origin than the web app (CONTRACTS.md; :8000 beside :3000). An
 * <img crossOrigin="anonymous"> never sends the cookie, so every such fetch
 * answered 401 and the cutout fell back to its placeholder. Media URLs go
 * with credentials (the API's CORS allows them for its web origins); any
 * other image (samples under /public, data: URLs) stays anonymous.
 */

const MEDIA_PATH = /\/families\/[^/]+\/media\//;

export function corsModeFor(url: string): "use-credentials" | "anonymous" {
  return MEDIA_PATH.test(url) ? "use-credentials" : "anonymous";
}

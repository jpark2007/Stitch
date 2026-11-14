import path from "node:path";
import type { NextConfig } from "next";

// The repo root (one level above web/). It holds shared/ — the contracts,
// rig and vocabulary JSON imported as `@shared/...` (see tsconfig paths).
// Turbopack does not resolve files outside its root, so the root must be the
// repo root rather than web/. Derived from this file's location so it is
// correct in the main checkout and inside any git worktree.
const repoRoot = path.resolve(__dirname, "..");

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Keep build-output tracing in step with Turbopack's root (Next warns when
  // they differ) so server bundles can include files from shared/.
  outputFileTracingRoot: repoRoot,
  turbopack: {
    root: repoRoot,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

// Log in. A thin server shell: the screen reads ?next= in the browser, inside
// a Suspense boundary, and the prerendered HTML shows the fallback.
import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthScreen, AuthScreenFallback } from "../AuthScreen";

export const metadata: Metadata = {
  title: "Log in · Stitch",
};

export default function LoginPage() {
  return (
    <Suspense fallback={<AuthScreenFallback kind="login" />}>
      <AuthScreen kind="login" />
    </Suspense>
  );
}

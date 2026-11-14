// Make an account. A thin server shell: the screen reads ?next= in the
// browser, inside a Suspense boundary, and the prerendered HTML shows the
// fallback.
import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthScreen, AuthScreenFallback } from "../AuthScreen";

export const metadata: Metadata = {
  title: "Make your account · Stitch",
};

export default function SignupPage() {
  return (
    <Suspense fallback={<AuthScreenFallback kind="signup" />}>
      <AuthScreen kind="signup" />
    </Suspense>
  );
}

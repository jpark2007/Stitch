import type { Metadata } from "next";
import { Suspense } from "react";
import PropsLab from "./PropsLab";

export const metadata: Metadata = {
  title: "Props lab · Stitch",
};

// The lab reads ?demo= and friends on the client, so it renders inside a
// Suspense boundary (the shell prerenders; the stage is client-only anyway).
export default function PropsLabPage() {
  return (
    <Suspense fallback={<main className="fixed inset-0 bg-wood-dark" />}>
      <PropsLab />
    </Suspense>
  );
}

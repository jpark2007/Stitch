import { Suspense } from "react";
import { TreeHome, TreeHomeLoading } from "@/components/tree/TreeHome";

// Home: the family tree. Every person opens their own pop-up book.
//
// The tree is the signed-in family's, fetched in the browser; the prerendered
// HTML is its paper placeholder (the boundary's fallback), so the page never
// flashes blank while it asks the API.

export default function HomePage() {
  return (
    <Suspense fallback={<TreeHomeLoading />}>
      <TreeHome />
    </Suspense>
  );
}

"use client";

// Photo files as the book prints them: each shrunk once to a small image
// (big enough for the print, small enough to redraw fast) and handed to the
// book as blob: URLs this hook owns, revoked when the photos change and when
// the book goes. Shared by the "add" pages' books (BookDesk.tsx); /add's
// CraftingBook has its own copy, to move here.
import { useEffect, useState } from "react";

/** Longest side of a photo as printed on the page, px. */
const PHOTO_PX = 640;
const shrunk = new WeakMap<File, Promise<{ blob: Blob; aspect: number } | null>>();

/** A photo file as a small image for the page (made once per file; the blob goes when the file does). */
function shrinkPhoto(file: File): Promise<{ blob: Blob; aspect: number } | null> {
  let hit = shrunk.get(file);
  if (!hit) {
    hit = (async () => {
      try {
        const bitmap = await createImageBitmap(file);
        const s = Math.min(1, PHOTO_PX / Math.max(bitmap.width, bitmap.height));
        const w = Math.max(1, Math.round(bitmap.width * s));
        const h = Math.max(1, Math.round(bitmap.height * s));
        const c = document.createElement("canvas");
        c.width = w;
        c.height = h;
        c.getContext("2d")?.drawImage(bitmap, 0, 0, w, h);
        bitmap.close();
        const blob = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.85));
        return blob ? { blob, aspect: Math.min(1.6, Math.max(0.7, w / h)) } : null;
      } catch {
        return null;
      }
    })();
    shrunk.set(file, hit);
  }
  return hit;
}

export type PhotoPrint = { url: string; aspect: number };
const NO_PRINTS: PhotoPrint[] = [];

/**
 * The page's prints of the first `max` photos, in order (ones that cannot be
 * read are left out). The same array until the photos change.
 */
export function usePhotoPrints(files: readonly File[], max = 2): PhotoPrint[] {
  const [prints, setPrints] = useState<{ files: readonly File[]; out: PhotoPrint[] }>({ files: [], out: NO_PRINTS });
  useEffect(() => {
    let live = true;
    const urls: string[] = [];
    Promise.all(files.slice(0, max).map(shrinkPhoto)).then((all) => {
      if (!live) return;
      const out = all
        .filter((p): p is { blob: Blob; aspect: number } => p !== null)
        .map((p) => {
          const url = URL.createObjectURL(p.blob);
          urls.push(url);
          return { url, aspect: p.aspect };
        });
      setPrints({ files, out: out.length ? out : NO_PRINTS });
    });
    return () => {
      live = false;
      // Revoked a beat later: the book may still be printing the page with them.
      window.setTimeout(() => urls.forEach((u) => URL.revokeObjectURL(u)), 4000);
    };
  }, [files, max]);
  return prints.files === files ? prints.out : NO_PRINTS;
}

// Pure checks for photos picked for a memory. No runtime imports and only
// erasable TypeScript, so `node --test` can load this file directly.

/** Most photos one memory can carry. */
export const MAX_PHOTOS = 6;

/** Largest single photo, in bytes (10 MB). */
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

/** Image types the API accepts. */
export const PHOTO_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
  "image/gif",
];

// Some browsers report an empty type for HEIC photos, so fall back on the
// file extension only when no type was given. lib/api.ts applies the same
// mapping when it uploads, so a typeless pick is sent with this type.
const PHOTO_TYPES_BY_EXTENSION: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
  ".heic": "image/heic",
  ".heif": "image/heif",
  ".gif": "image/gif",
};

interface PhotoLike {
  name: string;
  size: number;
  type: string;
}

/**
 * The image type a picked file will be uploaded as: its own type (trimmed,
 * lowercased) when it has one, else the type its extension names, else null.
 */
export function photoTypeFor(file: { name: string; type: string }): string | null {
  const type = file.type.trim().toLowerCase();
  if (type) return type;
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  return (dot >= 0 && PHOTO_TYPES_BY_EXTENSION[name.slice(dot)]) || null;
}

function isPhotoType(file: PhotoLike): boolean {
  const type = photoTypeFor(file);
  return type !== null && PHOTO_TYPES.includes(type);
}

function megabytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return `${mb >= 100 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`;
}

function quoted(name: string): string {
  return `“${name || "That file"}”`;
}

/**
 * Decides which newly picked files to keep, given the photos already chosen.
 * Returns the indices of `incoming` to accept, in order, plus one friendly
 * message per file left out (wrong type, too big, empty, already added, or
 * over the MAX_PHOTOS cap).
 */
export function acceptPhotos(
  current: PhotoLike[],
  incoming: PhotoLike[],
): { accepted: number[]; errors: string[] } {
  const accepted: number[] = [];
  const errors: string[] = [];
  const seen = new Set(current.map((f) => `${f.name}\u0000${f.size}`));
  let room = Math.max(0, MAX_PHOTOS - current.length);

  incoming.forEach((file, i) => {
    const key = `${file.name}\u0000${file.size}`;
    if (seen.has(key)) {
      errors.push(`${quoted(file.name)} is already added.`);
      return;
    }
    if (!isPhotoType(file)) {
      errors.push(`${quoted(file.name)} isn't a photo we can use. Try a JPEG, PNG, WebP, HEIC or GIF.`);
      return;
    }
    if (file.size <= 0) {
      errors.push(`${quoted(file.name)} looks empty. Try picking it again.`);
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      errors.push(
        `${quoted(file.name)} is too big (${megabytes(file.size)}). Each photo can be up to ${megabytes(MAX_PHOTO_BYTES)}.`,
      );
      return;
    }
    if (room <= 0) {
      errors.push(`${quoted(file.name)} wasn't added: a memory can hold up to ${MAX_PHOTOS} photos.`);
      return;
    }
    seen.add(key);
    room -= 1;
    accepted.push(i);
  });

  return { accepted, errors };
}

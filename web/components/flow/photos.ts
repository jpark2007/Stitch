// Pure checks for photos picked for a memory. No runtime imports and only
// erasable TypeScript, so `node --test` can load this file directly.

/** Most photos one memory can carry: the default cap for a pick. */
export const MAX_PHOTOS = 6;

/** What the photos belong to, named in the over-the-cap line. */
const DEFAULT_HOLDER = "memory";

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

/** How many names the over-the-cap line spells out before "and N more". */
const OVER_CAP_NAMED = 3;

/**
 * The one line for every photo left out by the cap in a pick:
 * “e.png” and “f.png” weren't added: a memory can hold up to 6 photos.
 * At most three are named; the rest are counted ("and 2 more"). Empty for
 * no names. `limit` and `holder` default to MAX_PHOTOS and "memory"; a
 * tradition passes its own ("a tradition can hold up to 10 photos").
 */
export function overCapMessage(
  names: readonly string[],
  limit: number = MAX_PHOTOS,
  holder: string = DEFAULT_HOLDER,
): string {
  if (names.length === 0) return "";
  const shown = names.slice(0, OVER_CAP_NAMED).map(quoted);
  const rest = names.length - shown.length;
  let list: string;
  if (rest > 0) list = `${shown.join(", ")} and ${rest} more`;
  else if (shown.length === 1) list = shown[0];
  else list = `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
  const verb = names.length === 1 ? "wasn't" : "weren't";
  return `${list} ${verb} added: a ${holder} can hold up to ${limit} photos.`;
}

/**
 * Decides which newly picked files to keep, given the photos already chosen.
 * Returns the indices of `incoming` to accept, in order, plus friendly
 * messages for the files left out: one per file that is the wrong type, too
 * big, empty or already added, then one line (overCapMessage) for all the
 * files over the cap together. The cap is `limit` (default MAX_PHOTOS) and
 * the over-the-cap line names `holder` (default "memory").
 */
export function acceptPhotos(
  current: PhotoLike[],
  incoming: PhotoLike[],
  limit: number = MAX_PHOTOS,
  holder: string = DEFAULT_HOLDER,
): { accepted: number[]; errors: string[] } {
  const accepted: number[] = [];
  const errors: string[] = [];
  const seen = new Set(current.map((f) => `${f.name}\u0000${f.size}`));
  let room = Math.max(0, limit - current.length);
  const overCap: string[] = [];

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
      overCap.push(file.name);
      return;
    }
    seen.add(key);
    room -= 1;
    accepted.push(i);
  });

  if (overCap.length > 0) errors.push(overCapMessage(overCap, limit, holder));
  return { accepted, errors };
}

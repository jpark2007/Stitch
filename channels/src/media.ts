import { ProtocolError } from "./protocol.ts";

export type MediaSource = { mimeType: string; size?: number; stream(): Promise<ReadableStream<Uint8Array>> };
export type DownloadOptions = { timeoutMs?: number };

/** Consume only the SDK stream. One absolute deadline covers opening and every read. */
export async function downloadMedia(source: MediaSource, types: ReadonlySet<string>, maxBytes: number, label: string, options: DownloadOptions = {}): Promise<Buffer> {
  if (!types.has(source.mimeType)) throw new ProtocolError(415, `Unsupported ${label.toLowerCase()} type`);
  if (source.size !== undefined && (!Number.isSafeInteger(source.size) || source.size <= 0 || source.size > maxBytes)) throw new ProtocolError(413, `Invalid ${label.toLowerCase()} size`);
  const timeoutMs = options.timeoutMs ?? 15_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60_000) throw new ProtocolError(422, "Invalid download timeout");
  const expires = performance.now() + timeoutMs;
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout>;
  const timeoutError = () => new ProtocolError(504, `${label} download timed out`);
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      timedOut = true;
      reject(timeoutError());
      void reader?.cancel().catch(() => {});
    }, timeoutMs);
  });
  // SDK stream() has no abort parameter. Cancel late resolution without awaiting it.
  const opening = Promise.resolve().then(() => source.stream()).then((stream) => {
    if (timedOut || performance.now() >= expires) {
      void stream.cancel().catch(() => {});
      throw timeoutError();
    }
    return stream;
  });
  const consume = async (): Promise<Buffer> => {
    try {
      const stream = await opening;
      reader = stream.getReader();
      const chunks: Uint8Array[] = [];
      let block = Buffer.allocUnsafe(Math.min(65_536, maxBytes));
      let used = 0;
      let size = 0;
      for (;;) {
        if (performance.now() >= expires) throw timeoutError();
        const part = await reader.read();
        if (performance.now() >= expires) throw timeoutError();
        if (part.done) break;
        if (!(part.value instanceof Uint8Array)) throw new ProtocolError(502, `${label} stream failed`);
        size += part.value.byteLength;
        if (size > maxBytes) throw new ProtocolError(413, `${label} exceeds 20 MiB`);
        // Pack tiny SDK chunks into bounded blocks, avoiding millions of retained
        // objects. Copy now because producers may reuse their buffer on next read.
        for (let offset = 0; offset < part.value.byteLength;) {
          const length = Math.min(block.length - used, part.value.byteLength - offset);
          block.set(part.value.subarray(offset, offset + length), used);
          used += length;
          offset += length;
          if (used === block.length) {
            chunks.push(block);
            block = Buffer.allocUnsafe(Math.min(65_536, maxBytes));
            used = 0;
          }
        }
      }
      if (!size) throw new ProtocolError(422, `${label} is empty`);
      if (used) chunks.push(block.subarray(0, used));
      return Buffer.concat(chunks, size);
    } catch (error) {
      void reader?.cancel().catch(() => {});
      if (error instanceof ProtocolError) throw error;
      throw new ProtocolError(502, `${label} stream failed`);
    } finally {
      try { reader?.releaseLock(); } catch { /* Cancellation may still be settling. */ }
    }
  };
  // Exactly one deadline subscriber. Racing each chunk retains a reaction on
  // the still-pending deadline for every read, even after that read finishes.
  try {
    return await Promise.race([consume(), deadline]);
  } finally {
    clearTimeout(timer!);
  }
}

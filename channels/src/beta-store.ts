import { DatabaseSync } from "node:sqlite";
import { mkdirSync, lstatSync, openSync, closeSync } from "node:fs";
import { dirname, isAbsolute } from "node:path";
import { createHash } from "node:crypto";
import { ProtocolError } from "./protocol.ts";

export const digest = (text: string) => createHash("sha256").update(text).digest("hex");
export const EVENT_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
export const ROUTE_RETENTION_MS = 30 * 24 * 60 * 60 * 1000;
export const EVENT_RETRY_DELAYS_MS = [5_000, 15_000, 30_000, 60_000, 120_000, 240_000, 480_000];
export const MAX_EVENT_ATTEMPTS = EVENT_RETRY_DELAYS_MS.length + 1;
export const MAX_RETAINED_EVENTS = 50_000;
export const RESERVED_STOP_IDENTITIES = 1_000;
export const MAX_PENDING_BODY_BYTES = 16 * 1024 * 1024;

/** Single-host durable queue. Never recover uncertain external effects by retrying. */
export class BetaStore {
  private db: DatabaseSync;
  private closed = false;
  private now: () => number;
  constructor(path: string, projectId: string, now: () => number = Date.now) {
    this.now = now;
    if (!isAbsolute(path)) throw new Error("PHOTON_STATE_PATH must be absolute");
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    const directory = lstatSync(dirname(path));
    if (!directory.isDirectory() || directory.isSymbolicLink() || (directory.mode & 0o077) || directory.uid !== process.getuid?.()) throw new Error("Photon state directory must be private and owned by this user");
    try { closeSync(openSync(path, "wx", 0o600)); } catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
    const file = lstatSync(path);
    if (!file.isFile() || file.isSymbolicLink() || (file.mode & 0o077) || file.uid !== directory.uid) throw new Error("Photon state file must be private and owned by this user");
    this.db = new DatabaseSync(path);
    try {
    this.db.exec("PRAGMA busy_timeout=1000; PRAGMA locking_mode=EXCLUSIVE; BEGIN EXCLUSIVE; COMMIT; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA max_page_count=16384;");
    this.db.exec("CREATE TABLE IF NOT EXISTS config (project TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, hash TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS replies (id TEXT PRIMARY KEY, hash TEXT NOT NULL, state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS routes (id TEXT PRIMARY KEY, proof TEXT NOT NULL);");
    const existing = this.db.prepare("SELECT project FROM config").get();
    if (existing && existing.project !== projectId) throw new Error("State belongs to another Photon project");
    this.db.prepare("INSERT OR IGNORE INTO config VALUES (?)").run(projectId);
    // Additive migration preserves existing in-flight uncertainty and receipts.
    const columns = new Set(this.db.prepare("PRAGMA table_info(events)").all().map(row => row.name));
    for (const name of ["created", "updated", "attempts", "due", "part", "isStop"])
      if (!columns.has(name)) this.db.exec(`ALTER TABLE events ADD COLUMN ${name} INTEGER NOT NULL DEFAULT 0`);
    if (!this.db.prepare("PRAGMA table_info(routes)").all().some(row => row.name === "updated"))
      this.db.exec("ALTER TABLE routes ADD COLUMN updated INTEGER NOT NULL DEFAULT 0");
    this.db.prepare("UPDATE events SET created=?,updated=? WHERE created=0").run(this.now(), this.now());
    this.db.prepare("UPDATE routes SET updated=? WHERE updated=0").run(this.now());
    this.db.exec("PRAGMA secure_delete=ON; CREATE INDEX IF NOT EXISTS events_due ON events(state,due);");
    } catch (error) { this.db.close(); this.closed = true; throw error; }
    // Exclusive locking mode retains the lock for this connection's lifetime.
    // Processing rows remain processing on restart and are never selected again.
  }
  accept(id: string, body: string, isStop = false): boolean {
    this.prune();
    const hash = digest(body);
    const row = this.db.prepare("SELECT hash FROM events WHERE id=?").get(id);
    if (row) { if (row.hash !== hash) throw new ProtocolError(409, "Event identity conflict"); return false; }
    const counts = this.db.prepare("SELECT count(*) AS total, sum(CASE WHEN state IN ('queued','processing') OR (state='blocked' AND body!='') THEN 1 ELSE 0 END) AS active, coalesce(sum(length(CAST(body AS BLOB))),0) AS bytes FROM events").get()!;
    // Reserve independent headroom for signed opt-out-shaped events. This flag
    // affects queue priority only; the API still decides actual consent semantics.
    if (Number(counts.total) >= MAX_RETAINED_EVENTS - (isStop ? 0 : RESERVED_STOP_IDENTITIES)
        || Number(counts.active) >= (isStop ? 10_000 : 9_900)
        || Number(counts.bytes) + Buffer.byteLength(body) > MAX_PENDING_BODY_BYTES - (isStop ? 0 : 1024 * 1024))
      throw new ProtocolError(503, "Event storage requires operator maintenance");
    this.db.prepare("INSERT INTO events (id,hash,body,state,created,updated,isStop) VALUES (?, ?, ?, 'queued',?,?,?)").run(id, hash, body, this.now(), this.now(), isStop ? 1 : 0);
    return true;
  }
  next(): { id: string; body: string; part: number } | undefined {
    this.prune();
    return this.db.prepare("UPDATE events SET state='processing',attempts=attempts+1,updated=? WHERE id=(SELECT id FROM events WHERE state='queued' AND due<=? ORDER BY isStop DESC,rowid LIMIT 1) RETURNING id,body,part").get(this.now(), this.now()) as { id: string; body: string; part: number } | undefined;
  }
  checkpoint(id: string, part: number): void { this.db.prepare("UPDATE events SET part=?,updated=? WHERE id=? AND state='processing'").run(part, this.now(), id); }
  retry(id: string): boolean {
    const row = this.db.prepare("SELECT attempts FROM events WHERE id=? AND state='processing'").get(id);
    if (!row || Number(row.attempts) >= MAX_EVENT_ATTEMPTS) return false;
    this.db.prepare("UPDATE events SET state='queued',due=?,updated=? WHERE id=?").run(this.now() + EVENT_RETRY_DELAYS_MS[Number(row.attempts) - 1], this.now(), id);
    return true;
  }
  finish(id: string, state: "done" | "blocked"): void { this.db.prepare("UPDATE events SET state=?,body=CASE WHEN ?='done' THEN '' ELSE body END,updated=? WHERE id=?").run(state, state, this.now(), id); }
  prune(): void {
    const cutoff = this.now() - EVENT_RETENTION_MS;
    this.db.prepare("DELETE FROM events WHERE state='done' AND updated<?").run(cutoff);
    // Never erase uncertain identities/state. Expired payloads cannot auto-run.
    this.db.prepare("UPDATE events SET body='',state='blocked' WHERE state!='done' AND created<? AND body!=''").run(cutoff);
    this.db.prepare("DELETE FROM routes WHERE updated<?").run(this.now() - ROUTE_RETENTION_MS);
  }
  saveRoute(id: string, proof: unknown): void {
    const value = JSON.stringify(proof);
    const old = this.db.prepare("SELECT proof FROM routes WHERE id=?").get(id);
    if (old && old.proof !== value) throw new ProtocolError(409, "Space identity changed");
    if (!old && Number(this.db.prepare("SELECT count(*) AS n FROM routes").get()!.n) >= 1000) throw new ProtocolError(503, "Space capacity reached");
    this.db.prepare("INSERT INTO routes (id,proof,updated) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated").run(id, value, this.now());
  }
  forgetRoute(id: string): void { this.db.prepare("DELETE FROM routes WHERE id=?").run(id); }
  route(id: string): unknown { this.prune(); const row = this.db.prepare("SELECT proof FROM routes WHERE id=?").get(id); return row ? JSON.parse(String(row.proof)) : undefined; }
  replySent(id: string, hash: string): boolean {
    const row = this.db.prepare("SELECT hash,state FROM replies WHERE id=?").get(id);
    if (!row) return false;
    if (row.hash !== hash) throw new ProtocolError(409, "Reply identity conflict");
    if (row.state !== "sent") throw new ProtocolError(503, "Reply outcome uncertain; reconcile before retrying");
    return true;
  }
  reserveReply(id: string, hash: string): boolean {
    const row = this.db.prepare("SELECT hash,state FROM replies WHERE id=?").get(id);
    if (row) {
      if (row.hash !== hash) throw new ProtocolError(409, "Reply identity conflict");
      if (row.state === "sent") return false;
      throw new ProtocolError(503, "Reply outcome uncertain; reconcile before retrying");
    }
    if (Number(this.db.prepare("SELECT count(*) AS n FROM replies").get()!.n) >= 10_000) throw new ProtocolError(503, "Reply capacity reached");
    this.db.prepare("INSERT INTO replies VALUES (?,?,'sending')").run(id, hash);
    return true;
  }
  sent(id: string): void { this.db.prepare("UPDATE replies SET state='sent' WHERE id=?").run(id); }
  counts() { return this.db.prepare("SELECT state,count(*) AS count FROM events GROUP BY state").all(); }
  close() { if (!this.closed) { this.db.close(); this.closed = true; } }
}

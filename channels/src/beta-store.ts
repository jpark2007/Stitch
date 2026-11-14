import { DatabaseSync } from "node:sqlite";
import { mkdirSync, lstatSync, openSync, closeSync } from "node:fs";
import { dirname, isAbsolute } from "node:path";
import { createHash } from "node:crypto";
import { ProtocolError } from "./protocol.ts";

export const digest = (text: string) => createHash("sha256").update(text).digest("hex");

/** Single-host durable queue. Never recover uncertain external effects by retrying. */
export class BetaStore {
  private db: DatabaseSync;
  private closed = false;
  constructor(path: string, projectId: string) {
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
    } catch (error) { this.db.close(); this.closed = true; throw error; }
    // Exclusive locking mode retains the lock for this connection's lifetime.
    // Processing rows remain processing on restart and are never selected again.
  }
  accept(id: string, body: string): boolean {
    const hash = digest(body);
    const row = this.db.prepare("SELECT hash FROM events WHERE id=?").get(id);
    if (row) { if (row.hash !== hash) throw new ProtocolError(409, "Event identity conflict"); return false; }
    const count = this.db.prepare("SELECT count(*) AS n FROM events").get()!;
    if (Number(count.n) >= 10_000) throw new ProtocolError(503, "Event storage requires operator maintenance");
    this.db.prepare("INSERT INTO events VALUES (?, ?, ?, 'queued')").run(id, hash, body);
    return true;
  }
  next(): { id: string; body: string } | undefined {
    return this.db.prepare("UPDATE events SET state='processing' WHERE id=(SELECT id FROM events WHERE state='queued' ORDER BY rowid LIMIT 1) RETURNING id,body").get() as { id: string; body: string } | undefined;
  }
  finish(id: string, state: "done" | "blocked"): void { this.db.prepare("UPDATE events SET state=?,body=CASE WHEN ?='done' THEN '' ELSE body END WHERE id=?").run(state, state, id); }
  saveRoute(id: string, proof: unknown): void {
    const value = JSON.stringify(proof);
    const old = this.db.prepare("SELECT proof FROM routes WHERE id=?").get(id);
    if (old && old.proof !== value) throw new ProtocolError(409, "Space identity changed");
    if (!old && Number(this.db.prepare("SELECT count(*) AS n FROM routes").get()!.n) >= 1000) throw new ProtocolError(503, "Space capacity reached");
    this.db.prepare("INSERT OR IGNORE INTO routes VALUES (?,?)").run(id, value);
  }
  route(id: string): unknown { const row = this.db.prepare("SELECT proof FROM routes WHERE id=?").get(id); return row ? JSON.parse(String(row.proof)) : undefined; }
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

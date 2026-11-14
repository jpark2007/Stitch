// Local Channels contract. Shared application schemas remain API-owner managed.
export interface Preferences {
  timezone: string;
  cadenceDays: number;
  quietHours: { start: string; end: string };
}

export interface ChannelContact {
  personId: string;
  provider: "imessage" | "terminal";
  handle: string;
  status: "pending" | "active" | "stopped";
  timezone: string | null;
  cadenceDays: number | null;
  quietHours: { start: string; end: string } | null;
}

export interface Enrollment {
  contact: ChannelContact;
  code: string;
  expiresAt: string;
  instruction: string;
}

export function preferencesFor(contact: ChannelContact | null): Preferences {
  return {
    timezone: contact?.timezone ?? "America/New_York",
    cadenceDays: contact?.cadenceDays ?? 7,
    quietHours: contact?.quietHours ?? { start: "21:00", end: "08:00" },
  };
}

export function preferenceError(p: Preferences): string | null {
  try { new Intl.DateTimeFormat("en-US", { timeZone: p.timezone }).format(); }
  catch { return "Enter a time zone such as America/New_York."; }
  if (!p.timezone || p.timezone !== p.timezone.trim()) return "Enter a time zone such as America/New_York.";
  if (!Number.isInteger(p.cadenceDays) || p.cadenceDays < 1 || p.cadenceDays > 30) return "Choose between 1 and 30 days.";
  const time = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
  if (!time.test(p.quietHours.start) || !time.test(p.quietHours.end)) return "Choose both quiet-hour times.";
  if (p.quietHours.start === p.quietHours.end) return "Quiet hours must start and end at different times.";
  return null;
}

export function validHandle(value: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(value) || (value.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value));
}

export function parseContact(value: unknown): ChannelContact {
  if (!value || typeof value !== "object") throw new Error("Invalid contact response");
  const c = value as Record<string, unknown>;
  if (typeof c.personId !== "string" || !c.personId || typeof c.handle !== "string" || !c.handle
      || !["imessage", "terminal"].includes(String(c.provider)) || !["pending", "active", "stopped"].includes(String(c.status))
      || (c.timezone !== null && typeof c.timezone !== "string")
      || (c.cadenceDays !== null && (typeof c.cadenceDays !== "number" || !Number.isInteger(c.cadenceDays) || c.cadenceDays < 1 || c.cadenceDays > 30))) {
    throw new Error("Invalid contact response");
  }
  if (c.quietHours !== null) {
    const q = c.quietHours as Record<string, unknown> | undefined;
    const time = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
    if (!q || typeof q.start !== "string" || typeof q.end !== "string" || !time.test(q.start) || !time.test(q.end) || q.start === q.end) throw new Error("Invalid contact response");
  }
  // Copy only the public fields; never keep unexpected server metadata in UI state.
  return { personId: c.personId, provider: c.provider as ChannelContact["provider"], handle: c.handle,
    status: c.status as ChannelContact["status"], timezone: c.timezone as string | null,
    cadenceDays: c.cadenceDays as number | null, quietHours: c.quietHours as ChannelContact["quietHours"] };
}

export function statusDescription(status: ChannelContact["status"]): string {
  return {
    pending: "Waiting for the recipient to verify and opt in. No invitation has been sent.",
    active: "The recipient has verified this account and opted in. They can send STOP at any time.",
    stopped: "Messages are stopped. Only a fresh verification message from the recipient can resume consent.",
  }[status];
}

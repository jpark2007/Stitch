// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in invites.ts and members.ts.
import { test } from "node:test";
import assert from "node:assert/strict";

import { describeExpiry, inviteUrl, pickActiveInvite } from "../invites.ts";
import {
  UNKNOWN_MEMBER_NAME,
  memberRows,
  roleAfter,
  roleChangeCopy,
  roleChangeError,
} from "../members.ts";

// ---- inviteUrl ------------------------------------------------------------

test("inviteUrl: joins origin and code", () => {
  assert.equal(inviteUrl("https://stitch.family", "k3J9x"), "https://stitch.family/join/k3J9x");
});

test("inviteUrl: drops trailing slashes from the origin", () => {
  assert.equal(inviteUrl("http://localhost:3000/", "abc"), "http://localhost:3000/join/abc");
  assert.equal(inviteUrl("http://localhost:3000///", "abc"), "http://localhost:3000/join/abc");
});

test("inviteUrl: encodes the code", () => {
  assert.equal(inviteUrl("https://a.b", "a/b c?d#e"), "https://a.b/join/a%2Fb%20c%3Fd%23e");
  assert.equal(inviteUrl("https://a.b", "x_y-Z~9"), "https://a.b/join/x_y-Z~9");
});

// ---- pickActiveInvite -----------------------------------------------------

const NOW = new Date(2026, 10, 13, 12, 0, 0); // 13 Nov 2026, noon local
const inDays = (d, base = NOW) => new Date(base.getTime() + d * 86_400_000).toISOString();
const inv = (code, expiresAt, revoked = false) => ({ code, createdBy: "u1", expiresAt, revoked });

test("pickActiveInvite: undefined, empty and all-dead lists give null", () => {
  assert.equal(pickActiveInvite(undefined, NOW), null);
  assert.equal(pickActiveInvite([], NOW), null);
  assert.equal(pickActiveInvite([inv("old", inDays(-1)), inv("off", inDays(5), true)], NOW), null);
});

test("pickActiveInvite: skips revoked invites", () => {
  const live = inv("live", inDays(3));
  assert.equal(pickActiveInvite([inv("off", inDays(10), true), live], NOW), live);
});

test("pickActiveInvite: skips expired invites, including one expiring right now", () => {
  const live = inv("live", inDays(1));
  assert.equal(pickActiveInvite([inv("gone", inDays(-0.5)), inv("now", NOW.toISOString()), live], NOW), live);
});

test("pickActiveInvite: the latest expiry wins, whatever the order", () => {
  const a = inv("a", inDays(2));
  const b = inv("b", inDays(14));
  const c = inv("c", inDays(7));
  assert.equal(pickActiveInvite([a, b, c], NOW), b);
  assert.equal(pickActiveInvite([c, b, a], NOW), b);
});

test("pickActiveInvite: ignores unparseable dates", () => {
  const live = inv("live", inDays(1));
  assert.equal(pickActiveInvite([inv("bad", "not a date"), inv("empty", ""), live], NOW), live);
  assert.equal(pickActiveInvite([inv("bad", "soon")], NOW), null);
  // revoked is optional on the wire
  assert.equal(pickActiveInvite([{ code: "x", createdBy: "u", expiresAt: inDays(2) }], NOW).code, "x");
});

// ---- describeExpiry -------------------------------------------------------

const at = (y, m, d, h = 0, min = 0) => new Date(y, m, d, h, min, 0);

test("describeExpiry: later today is 'Expires today'", () => {
  assert.equal(describeExpiry(at(2026, 10, 13, 23, 59).toISOString(), NOW), "Expires today");
  assert.equal(describeExpiry(at(2026, 10, 13, 12, 1).toISOString(), NOW), "Expires today");
});

test("describeExpiry: any time on the next calendar day is 'Expires tomorrow'", () => {
  assert.equal(describeExpiry(at(2026, 10, 14, 0, 0).toISOString(), NOW), "Expires tomorrow");
  assert.equal(describeExpiry(at(2026, 10, 14, 23, 59).toISOString(), NOW), "Expires tomorrow");
  // Late at night, an expiry a couple of hours away but past midnight is tomorrow.
  const lateNight = at(2026, 10, 13, 23, 0);
  assert.equal(describeExpiry(at(2026, 10, 14, 1, 0).toISOString(), lateNight), "Expires tomorrow");
});

test("describeExpiry: two or more days counts calendar days", () => {
  assert.equal(describeExpiry(at(2026, 10, 15, 0, 1).toISOString(), NOW), "Works for 2 more days");
  assert.equal(describeExpiry(at(2026, 10, 26, 9).toISOString(), NOW), "Works for 13 more days");
  assert.equal(describeExpiry(inDays(14), NOW), "Works for 14 more days");
  // Calendar days, not 24-hour blocks: early on the 15th from late on the 13th is 2 days.
  assert.equal(describeExpiry(at(2026, 10, 15, 1).toISOString(), at(2026, 10, 13, 23)), "Works for 2 more days");
});

test("describeExpiry: counts across a month end and a daylight-saving change", () => {
  assert.equal(describeExpiry(at(2026, 11, 3, 12).toISOString(), at(2026, 10, 28, 12)), "Works for 5 more days");
  assert.equal(describeExpiry(at(2026, 2, 20, 12).toISOString(), at(2026, 2, 1, 12)), "Works for 19 more days");
  assert.equal(describeExpiry(at(2026, 9, 30, 12).toISOString(), at(2026, 9, 20, 12)), "Works for 10 more days");
});

test("describeExpiry: now or earlier is 'Expired'", () => {
  assert.equal(describeExpiry(NOW.toISOString(), NOW), "Expired");
  assert.equal(describeExpiry(at(2026, 10, 13, 11, 59).toISOString(), NOW), "Expired");
  assert.equal(describeExpiry(inDays(-30), NOW), "Expired");
});

test("describeExpiry: invalid dates give an empty string", () => {
  assert.equal(describeExpiry("", NOW), "");
  assert.equal(describeExpiry("next week", NOW), "");
  assert.equal(describeExpiry(undefined, NOW), "");
  assert.equal(describeExpiry(inDays(3), new Date(Number.NaN)), "");
});

// ---- memberRows -----------------------------------------------------------

const person = (id, userId, name, color = "#aa3322") => ({ id, userId, name, character: { color } });
const PEOPLE = [
  person("p-rosa", "u-rosa", "Rosa", "#c0392b"),
  person("p-ana", "u-ana", "Ana", "#2e86c1"),
  person("p-luis", "u-luis", "Luis", "#27ae60"),
  person("p-tom", null, "Tom"), // unclaimed
];

test("memberRows: joins each member to the person they claimed", () => {
  const rows = memberRows(
    [{ userId: "u-ana", role: "member", joinedAt: "2026-11-01T10:00:00Z" }],
    PEOPLE,
    "u-rosa",
    true,
  );
  assert.deepEqual(rows, [
    {
      userId: "u-ana",
      role: "member",
      name: "Ana",
      personId: "p-ana",
      color: "#2e86c1",
      joinedAt: "2026-11-01T10:00:00Z",
      isYou: false,
      removable: true,
      canChangeRole: true,
      roleAction: "promote",
    },
  ]);
});

test("memberRows: falls back when no person is claimed by the member", () => {
  const [row] = memberRows([{ userId: "u-ghost", role: "member" }], PEOPLE, "u-rosa", true);
  assert.equal(row.name, UNKNOWN_MEMBER_NAME);
  assert.equal(row.name, "A family member");
  assert.equal(row.personId, null);
  assert.equal(row.color, null);
  assert.equal(row.joinedAt, null);
});

test("memberRows: the sole admin is not removable, even by another admin's view", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-ana", role: "member" },
  ];
  // Viewer is an admin looking at a family whose only admin is someone else
  // (can't happen for real, but the last admin must still be protected).
  const rows = memberRows(members, PEOPLE, "u-luis", true);
  assert.equal(rows.find((r) => r.userId === "u-rosa").removable, false);
  assert.equal(rows.find((r) => r.userId === "u-ana").removable, true);
});

test("memberRows: with two admins, the other admin can be removed", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
  ];
  const rows = memberRows(members, PEOPLE, "u-rosa", true);
  assert.equal(rows.find((r) => r.userId === "u-luis").removable, true);
  assert.equal(rows.find((r) => r.userId === "u-rosa").removable, false);
});

test("memberRows: the viewer is never removable", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-ana", role: "member" },
  ];
  for (const viewer of ["u-rosa", "u-luis", "u-ana"]) {
    const rows = memberRows(members, PEOPLE, viewer, true);
    const you = rows.find((r) => r.isYou);
    assert.equal(you.userId, viewer);
    assert.equal(you.removable, false, viewer);
  }
});

test("memberRows: non-admin viewers see nothing removable", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-ana", role: "member" },
    { userId: "u-ghost", role: "member" },
  ];
  const rows = memberRows(members, PEOPLE, "u-ana", false);
  assert.equal(rows.length, 4);
  assert.ok(rows.every((r) => r.removable === false));
});

test("memberRows: sorts the viewer first, then admins, then by name", () => {
  const people = [
    ...PEOPLE,
    person("p-bea", "u-bea", "Bea"),
    person("p-zed", "u-zed", "Zed"),
    person("p-cy", "u-cy", "Cy"),
  ];
  const members = [
    { userId: "u-zed", role: "member" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-cy", role: "member" },
    { userId: "u-ana", role: "member" },
    { userId: "u-bea", role: "admin" },
    { userId: "u-rosa", role: "admin" },
  ];
  const rows = memberRows(members, people, "u-cy", true);
  assert.deepEqual(
    rows.map((r) => r.name),
    ["Cy", "Bea", "Luis", "Rosa", "Ana", "Zed"],
  );
  assert.equal(rows[0].isYou, true);
  assert.deepEqual(rows.map((r) => r.isYou), [true, false, false, false, false, false]);
});

test("memberRows: an admin viewer is first even though other admins sort above them by name", () => {
  const members = [
    { userId: "u-ana", role: "admin" },
    { userId: "u-rosa", role: "admin" },
  ];
  const rows = memberRows(members, PEOPLE, "u-rosa", true);
  assert.deepEqual(rows.map((r) => r.userId), ["u-rosa", "u-ana"]);
});

// ---- memberRows: role changes ---------------------------------------------

const byUser = (rows) => Object.fromEntries(rows.map((r) => [r.userId, r]));
const actionsOf = (rows) =>
  Object.fromEntries(rows.map((r) => [r.userId, [r.canChangeRole, r.roleAction]]));

test("memberRows: a non-admin viewer gets no role actions", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-ana", role: "member" },
    { userId: "u-ghost", role: "member" },
  ];
  const rows = memberRows(members, PEOPLE, "u-ana", false);
  for (const r of rows) {
    assert.equal(r.canChangeRole, false, r.userId);
    assert.equal(r.roleAction, null, r.userId);
  }
});

test("memberRows: an admin sees promote on members and demote on other admins", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-ana", role: "member" },
    { userId: "u-ghost", role: "member" },
  ];
  const rows = memberRows(members, PEOPLE, "u-rosa", true);
  assert.deepEqual(actionsOf(rows), {
    "u-rosa": [true, "demote"], // stepping down: Luis is still an admin
    "u-luis": [true, "demote"],
    "u-ana": [true, "promote"],
    "u-ghost": [true, "promote"],
  });
});

test("memberRows: the sole admin's own row cannot step down", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-ana", role: "member" },
  ];
  const rows = byUser(memberRows(members, PEOPLE, "u-rosa", true));
  assert.equal(rows["u-rosa"].canChangeRole, false);
  assert.equal(rows["u-rosa"].roleAction, null);
  assert.equal(rows["u-ana"].roleAction, "promote");
});

test("memberRows: with two admins, the viewer's own row can step down", () => {
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-luis", role: "admin" },
  ];
  const rows = byUser(memberRows(members, PEOPLE, "u-luis", true));
  assert.equal(rows["u-luis"].isYou, true);
  assert.equal(rows["u-luis"].canChangeRole, true);
  assert.equal(rows["u-luis"].roleAction, "demote");
  assert.equal(rows["u-luis"].removable, false, "stepping down is not removing yourself");
});

test("memberRows: the last admin is never demoted, even from another admin's view", () => {
  // Can't happen for real (the viewer would be an admin too), but the last
  // admin must still be protected.
  const members = [
    { userId: "u-rosa", role: "admin" },
    { userId: "u-ana", role: "member" },
  ];
  const rows = byUser(memberRows(members, PEOPLE, "u-luis", true));
  assert.equal(rows["u-rosa"].canChangeRole, false);
  assert.equal(rows["u-rosa"].roleAction, null);
});

test("memberRows: a viewer who is a member in the list can't promote themselves", () => {
  // The session still says admin while the family already says member (just
  // after stepping down): no control on the viewer's own row.
  const members = [
    { userId: "u-rosa", role: "member" },
    { userId: "u-luis", role: "admin" },
  ];
  const rows = byUser(memberRows(members, PEOPLE, "u-rosa", true));
  assert.equal(rows["u-rosa"].roleAction, null);
  assert.equal(rows["u-rosa"].canChangeRole, false);
});

test("memberRows: role actions keep the remove and sort rules", () => {
  const members = [
    { userId: "u-ana", role: "member" },
    { userId: "u-luis", role: "admin" },
    { userId: "u-rosa", role: "admin" },
  ];
  const rows = memberRows(members, PEOPLE, "u-rosa", true);
  assert.deepEqual(rows.map((r) => r.userId), ["u-rosa", "u-luis", "u-ana"]);
  assert.deepEqual(rows.map((r) => r.removable), [false, true, true]);
});

// ---- role change words ----------------------------------------------------

test("roleAfter: promote makes an admin, demote a member", () => {
  assert.equal(roleAfter("promote"), "admin");
  assert.equal(roleAfter("demote"), "member");
});

/** The button's accessible name: visible words with the hidden name between. */
const nameOf = (copy) => copy.labelBefore + copy.labelHidden + copy.labelAfter;
/** What the button shows. */
const shownOf = (copy) => copy.labelBefore + copy.labelAfter;

test("roleChangeCopy: names the person for screen readers, shows short labels", () => {
  const ana = { name: "Ana", personId: "p-ana", isYou: false };
  const promote = roleChangeCopy(ana, "promote");
  assert.equal(shownOf(promote), "Make admin");
  assert.equal(nameOf(promote), "Make Ana an admin");
  assert.equal(promote.question, "Make Ana an admin?");
  assert.equal(promote.done, "Ana is now an admin.");

  const demote = roleChangeCopy(ana, "demote");
  assert.equal(shownOf(demote), "Make member");
  assert.equal(nameOf(demote), "Make Ana a member");
  assert.equal(demote.done, "Ana is now a member.");

  const self = roleChangeCopy({ name: "Rosa", personId: "p-rosa", isYou: true }, "demote");
  assert.equal(shownOf(self), "Step down as admin");
  assert.equal(nameOf(self), "Step down as admin");
  assert.equal(self.confirm, "Step down");

  const ghost = roleChangeCopy({ name: UNKNOWN_MEMBER_NAME, personId: null, isYou: false }, "promote");
  assert.equal(nameOf(ghost), "Make this family member an admin");
  assert.equal(ghost.done, "The family member is now an admin.");
});

test("roleChangeError: a 409 is the last-admin rule in plain words; others keep their message", () => {
  const conflict = Object.assign(new Error("A family needs at least one admin"), { status: 409 });
  assert.match(roleChangeError(conflict, { name: "Rosa", personId: "p-rosa", isYou: true }), /only admin/);
  assert.match(roleChangeError(conflict, { name: "Luis", personId: "p-luis", isYou: false }), /^Luis is the only admin/);
  const other = Object.assign(new Error("Only a family admin can do that."), { status: 403 });
  assert.equal(roleChangeError(other, { name: "Ana", personId: "p-ana", isYou: false }), "Only a family admin can do that.");
  assert.equal(roleChangeError("boom", { name: "Ana", personId: "p-ana", isYou: false }), "Something went wrong. Please try again.");
});

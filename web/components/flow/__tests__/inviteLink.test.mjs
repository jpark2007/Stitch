// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in inviteLink.ts, which has no imports.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { parseInviteCode } from "../inviteLink.ts";

const API_CODE = "Ab3_x-9QzKp1Lm0w"; // token_urlsafe(12): 16 URL-safe characters
const MOCK_CODE = "riveras-demo";

describe("parseInviteCode: full links", () => {
  test("https link, as the settings card copies it", () => {
    assert.equal(parseInviteCode(`https://stitch.example/join/${API_CODE}`), API_CODE);
  });

  test("http link (a laptop on localhost)", () => {
    assert.equal(parseInviteCode(`http://localhost:3000/join/${MOCK_CODE}`), MOCK_CODE);
  });

  test("any origin: only the code is kept", () => {
    assert.equal(parseInviteCode(`https://someone-elses.site/join/${API_CODE}`), API_CODE);
    assert.equal(parseInviteCode(`HTTPS://STITCH.EXAMPLE:8443/join/${API_CODE}`), API_CODE);
  });

  test("trailing slash, query and hash are ignored", () => {
    for (const tail of ["/", "?ref=text", "/?ref=text", "#top", "/#top", "?a=1&b=2#x"]) {
      const link = `https://stitch.example/join/${API_CODE}${tail}`;
      assert.equal(parseInviteCode(link), API_CODE, link);
    }
  });

  test("surrounding whitespace and newlines are trimmed", () => {
    assert.equal(parseInviteCode(`  https://stitch.example/join/${API_CODE}\n`), API_CODE);
    assert.equal(parseInviteCode(`\t${MOCK_CODE}  `), MOCK_CODE);
  });

  test("a percent-encoded code is decoded", () => {
    assert.equal(parseInviteCode("https://stitch.example/join/riveras%2Ddemo"), MOCK_CODE);
  });
});

describe("parseInviteCode: bare codes", () => {
  test("API and mock codes", () => {
    assert.equal(parseInviteCode(API_CODE), API_CODE);
    assert.equal(parseInviteCode(MOCK_CODE), MOCK_CODE);
    assert.equal(parseInviteCode("k7mq2pxa9rtd"), "k7mq2pxa9rtd");
  });

  test("illegal characters or the wrong length are refused", () => {
    for (const bad of [
      "riveras demo",
      "riveras.demo",
      "riveras/demo",
      "code%20here1",
      "ab<script>cd",
      "ab'cd\"ef12",
      "short",
      "x".repeat(65),
      "not an invite link",
    ]) {
      assert.equal(parseInviteCode(bad), null, bad);
    }
  });
});

describe("parseInviteCode: refused", () => {
  test("empty and blank input", () => {
    assert.equal(parseInviteCode(""), null);
    assert.equal(parseInviteCode("   \n\t "), null);
  });

  test("not a string", () => {
    assert.equal(parseInviteCode(undefined), null);
    assert.equal(parseInviteCode(null), null);
    assert.equal(parseInviteCode(42), null);
  });

  test("a link whose path is not /join/<code>", () => {
    for (const bad of [
      `https://stitch.example/${API_CODE}`,
      `https://stitch.example/login?next=/join/${API_CODE}`,
      `https://stitch.example/families/${API_CODE}/settings`,
      `https://stitch.example/foo/join/${API_CODE}`,
      `https://stitch.example/join/${API_CODE}/extra`,
      `https://stitch.example/JOIN/${API_CODE}`,
      "https://stitch.example/join/",
      "https://stitch.example/join",
      `https://stitch.example/#/join/${API_CODE}`,
      `https://evil.example/redirect?to=/join/${API_CODE}`,
    ]) {
      assert.equal(parseInviteCode(bad), null, bad);
    }
  });

  test("a /join/ link whose code has illegal characters", () => {
    for (const bad of [
      "https://stitch.example/join/riveras%20demo",
      "https://stitch.example/join/riv.eras-demo",
      "https://stitch.example/join/%3Cscript%3E1234",
      "https://stitch.example/join/%E0%A4%A",
      "https://stitch.example/join/abc",
    ]) {
      assert.equal(parseInviteCode(bad), null, bad);
    }
  });

  test("javascript:, data: and other schemes", () => {
    for (const bad of [
      "javascript:alert(1)",
      `javascript://stitch.example/join/${API_CODE}`,
      `JavaScript:location='/join/${API_CODE}'`,
      `data:text/html,<a href="/join/${API_CODE}">`,
      `data:text/plain,${API_CODE}`,
      `ftp://stitch.example/join/${API_CODE}`,
      `file:///join/${API_CODE}`,
      `//stitch.example/join/${API_CODE}`,
      `/join/${API_CODE}`,
      `stitch.example/join/${API_CODE}`,
    ]) {
      assert.equal(parseInviteCode(bad), null, bad);
    }
  });
});

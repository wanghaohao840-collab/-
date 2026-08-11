import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const handoffPath = resolve(repositoryRoot, "docs/product-ui/penpot-handoff.md");
const handoff = readFileSync(handoffPath, "utf8");

const references = [
  {
    label: "Desktop login",
    id: "9b1e7a6b-703c-8060-8008-70761a57accd",
    file: "desktop-login.png",
    width: 1440,
    height: 1024,
  },
  {
    label: "Tablet login",
    id: "9b1e7a6b-703c-8060-8008-7076b66de7ca",
    file: "tablet-login.png",
    width: 1024,
    height: 768,
  },
  {
    label: "Mobile login",
    id: "9b1e7a6b-703c-8060-8008-707701065fab",
    file: "mobile-login.png",
    width: 390,
    height: 844,
  },
];

function pngDimensions(file) {
  const bytes = readFileSync(
    resolve(repositoryRoot, "docs/product-ui/reference/penpot", file),
  );
  assert.deepEqual(
    [...bytes.subarray(0, 8)],
    [137, 80, 78, 71, 13, 10, 26, 10],
    `${file} must be a PNG`,
  );
  assert.equal(bytes.subarray(12, 16).toString("ascii"), "IHDR");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

test("handoff records the live Penpot cleanup and canonical legacy route", () => {
  assert.match(handoff, /Validated on 2026-08-11 against Penpot 2\.17\.1/);
  assert.match(handoff, /revision `89`/);
  assert.match(
    handoff,
    /Removed empty Foundations board: `0f745b42-1a51-801c-8008-6ff39f5b8841`/,
  );
  assert.match(handoff, /Login remember rows removed from Desktop, Tablet, and Mobile/);
  assert.doesNotMatch(handoff, /remember rows read back/i);
  assert.doesNotMatch(handoff, /routes to `\/legacy`\./);
  assert.match(handoff, /routes to `\/legacy\/`\./);
});

test("handoff binds the exact responsive field internals", () => {
  const requiredFillIds = [
    "9b1e7a6b-703c-8060-8008-70743ef84e3c",
    "9b1e7a6b-703c-8060-8008-7074e250419f",
    "9b1e7a6b-703c-8060-8008-7074e286ce22",
  ];
  for (const id of requiredFillIds) {
    assert.ok(
      handoff.includes("| `" + id + "` | `fill` |"),
      `${id} must read back fill sizing`,
    );
  }
  assert.match(
    handoff,
    /`9b1e7a6b-703c-8060-8008-7074e28f276b` \| `fix` \| `44 × 44`/,
  );
});

test("desktop, tablet, and mobile Login references are real PNG exports", () => {
  for (const reference of references) {
    assert.ok(handoff.includes(`| ${reference.label} |`));
    assert.ok(handoff.includes(`\`${reference.id}\``));
    assert.ok(handoff.includes(`(${`reference/penpot/${reference.file}`})`));
    assert.deepEqual(pngDimensions(reference.file), {
      width: reference.width,
      height: reference.height,
    });
  }
});

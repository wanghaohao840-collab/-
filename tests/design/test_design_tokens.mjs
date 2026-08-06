import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  flattenTokens,
  renderCss,
  writeCss,
} from "../../scripts/design_tokens.mjs";

function token(type, value) {
  return { $type: type, $value: value };
}

function shadow(value = {}) {
  return token("shadow", {
    color: "#263B3414",
    offsetX: { value: 0, unit: "px" },
    offsetY: { value: 2, unit: "px" },
    blur: { value: 12, unit: "px" },
    spread: { value: 0, unit: "px" },
    ...value,
  });
}

test("renders DTCG paths as stable CSS variables", () => {
  const css = renderCss({
    color: { brand: { 600: token("color", "#287A60") } },
    space: { 4: token("dimension", { value: 16, unit: "px" }) },
  });

  assert.match(css, /--color-brand-600: #287A60;/);
  assert.match(css, /--space-4: 16px;/);
  assert.ok(css.indexOf("--color-brand-600") < css.indexOf("--space-4"));
  assert.equal(css, ":root {\n  --color-brand-600: #287A60;\n  --space-4: 16px;\n}\n");
});

test("renders shadows in standard CSS order", () => {
  assert.match(
    renderCss({ shadow: { surface: shadow() } }),
    /--shadow-surface: 0px 2px 12px 0px #263B3414;/,
  );
});

test("rejects duplicate flattened names with a token path", () => {
  assert.throws(
    () => flattenTokens({ color: { brand: token("color", "#fff") }, "color.brand": token("color", "#000") }),
    /Duplicate CSS token name: color\.brand/,
  );
});

test("rejects invalid dimension units and unknown types with token paths", () => {
  assert.throws(
    () => renderCss({ space: { bad: token("dimension", { value: 1, unit: "em" }) } }),
    /Unsupported or invalid token: space\.bad/,
  );
  assert.throws(
    () => renderCss({ duration: { fast: token("duration", "100ms") } }),
    /Unsupported or invalid token: duration\.fast/,
  );
});

test("rejects non-finite dimensions and shadow fields with token paths", () => {
  for (const value of [NaN, Infinity, -Infinity]) {
    assert.throws(
      () => renderCss({ space: { invalid: token("dimension", { value, unit: "px" }) } }),
      /Unsupported or invalid token: space\.invalid/,
    );
  }
  for (const field of ["offsetX", "offsetY", "blur", "spread"]) {
    assert.throws(
      () => renderCss({ shadow: { invalid: shadow({ [field]: { value: NaN, unit: "px" } }) } }),
      /Unsupported or invalid token: shadow\.invalid/,
    );
  }
});

test("rejects tokens that are missing $value with their path", () => {
  assert.throws(
    () => renderCss({ color: { missing: { $type: "color" } } }),
    /Unsupported or invalid token: color\.missing/,
  );
});

test("rejects invalid or incomplete shadows with token paths", () => {
  assert.throws(
    () => renderCss({ shadow: { missing: shadow({ color: undefined }) } }),
    /Unsupported or invalid token: shadow\.missing/,
  );
  assert.throws(
    () => renderCss({ shadow: { unit: shadow({ blur: { value: 1, unit: "em" } }) } }),
    /Unsupported or invalid token: shadow\.unit/,
  );
});

test("CLI --check accepts matching CSS and rejects stale output", () => {
  const directory = mkdtempSync(join(tmpdir(), "design-tokens-"));
  const input = join(directory, "tokens.json");
  const output = join(directory, "tokens.css");
  const source = JSON.stringify({ color: { brand: token("color", "#287A60") } });

  try {
    writeFileSync(input, source, "utf8");
    writeCss(input, output);
    const success = spawnSync(process.execPath, ["scripts/design_tokens.mjs", "--check", input, output]);
    assert.equal(success.status, 0, success.stderr.toString());
    assert.match(readFileSync(output, "utf8"), /--color-brand: #287A60;/);

    writeFileSync(output, "stale\n", "utf8");
    const stale = spawnSync(process.execPath, ["scripts/design_tokens.mjs", "--check", input, output]);
    assert.notEqual(stale.status, 0);
    assert.match(stale.stderr.toString(), /Generated CSS is stale:/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

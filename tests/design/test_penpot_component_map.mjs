import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const mappingPath = resolve(repositoryRoot, "docs/product-ui/penpot-component-map.json");
const schemaPath = resolve(
  repositoryRoot,
  "docs/product-ui/penpot-component-map.schema.json",
);

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function assertNonEmptyString(value, label) {
  assert.equal(typeof value, "string", `${label} must be a string`);
  assert.ok(value.trim(), `${label} must not be empty`);
}

test("component map schema fixes the verified Penpot bridge contract", () => {
  const schema = readJson(schemaPath);
  const component = schema.$defs.component;

  assert.deepEqual(schema.required, ["fileUrl", "components"]);
  assert.equal(schema.additionalProperties, false);
  assert.equal(component.additionalProperties, false);
  assert.deepEqual(component.required, [
    "penpotId",
    "penpotName",
    "codeFile",
    "exportName",
    "variants",
    "verified",
  ]);
  assert.equal(component.properties.verified.const, true);
  assert.equal(component.properties.variants.additionalProperties.type, "array");
  assert.equal(component.properties.variants.additionalProperties.minItems, 1);
  assert.equal(component.properties.variants.additionalProperties.uniqueItems, true);
});

test("component map is secret-free and points to unique repository files", () => {
  const raw = readFileSync(mappingPath, "utf8");
  const mapping = JSON.parse(raw);

  assertNonEmptyString(mapping.fileUrl, "fileUrl");
  assert.match(mapping.fileUrl, /^https:\/\/design\.penpot\.app\//);
  assert.doesNotMatch(raw, /userToken|figma\.com|api[_-]?key|secret/i);
  assert.ok(Array.isArray(mapping.components));
  assert.ok(mapping.components.length >= 5);

  const ids = new Set();
  for (const component of mapping.components) {
    assertNonEmptyString(component.penpotId, `${component.penpotName}.penpotId`);
    assert.ok(!ids.has(component.penpotId), `duplicate Penpot ID: ${component.penpotId}`);
    ids.add(component.penpotId);
    assertNonEmptyString(component.penpotName, "penpotName");
    assertNonEmptyString(component.codeFile, `${component.penpotName}.codeFile`);
    assert.ok(!component.codeFile.includes("\\"), "codeFile must use repository separators");
    assert.ok(!component.codeFile.startsWith("/"), "codeFile must be repository-relative");
    assert.ok(
      existsSync(resolve(repositoryRoot, component.codeFile)),
      `missing codeFile: ${component.codeFile}`,
    );
    assertNonEmptyString(component.exportName, `${component.penpotName}.exportName`);
    assert.equal(typeof component.variants, "object");
    assert.ok(component.variants !== null && !Array.isArray(component.variants));
    for (const [property, values] of Object.entries(component.variants)) {
      assertNonEmptyString(property, `${component.penpotName} variant property`);
      assert.ok(Array.isArray(values) && values.length > 0);
      assert.equal(new Set(values).size, values.length);
      values.forEach((value) => assertNonEmptyString(value, `${component.penpotName}.${property}`));
    }
  }
});

test("component map covers the required exported React components", () => {
  const mapping = readJson(mappingPath);
  const byName = new Map(
    mapping.components.map((component) => [component.penpotName, component]),
  );
  const required = {
    Button: ["web/src/components/Button/Button.tsx", "Button"],
    TextField: ["web/src/components/TextField/TextField.tsx", "TextField"],
    AppShell: ["web/src/layout/AppShell.tsx", "AppShell"],
    Sidebar: ["web/src/components/Sidebar/Sidebar.tsx", "Sidebar"],
    MobileBottomNav: [
      "web/src/components/MobileBottomNav/MobileBottomNav.tsx",
      "MobileBottomNav",
    ],
  };

  for (const [name, [codeFile, exportName]] of Object.entries(required)) {
    assert.ok(byName.has(name), `missing required mapping: ${name}`);
    assert.equal(byName.get(name).codeFile, codeFile);
    assert.equal(byName.get(name).exportName, exportName);
  }
});

test("every component ID has been freshly verified", () => {
  const mapping = readJson(mappingPath);
  for (const component of mapping.components) {
    assert.equal(component.verified, true, `${component.penpotName} requires Penpot readback`);
  }
});

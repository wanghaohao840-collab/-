import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";

const dimensionUnits = ["px", "rem", "%"];

function formatDimension(value) {
  if (
    value &&
    Number.isFinite(value.value) &&
    dimensionUnits.includes(value.unit)
  ) {
    return `${value.value}${value.unit}`;
  }
  return null;
}

function formatValue(path, token) {
  if (token.$type === "color" && typeof token.$value === "string") {
    return token.$value;
  }
  if (token.$type === "dimension") {
    const dimension = formatDimension(token.$value);
    if (dimension) return dimension;
  }
  if (token.$type === "shadow") {
    const value = token.$value;
    const dimensions = [
      formatDimension(value?.offsetX),
      formatDimension(value?.offsetY),
      formatDimension(value?.blur),
      formatDimension(value?.spread),
    ];
    if (typeof value?.color === "string" && dimensions.every(Boolean)) {
      return `${dimensions.join(" ")} ${value.color}`;
    }
  }
  throw new Error(`Unsupported or invalid token: ${path}`);
}

export function flattenTokens(root) {
  const entries = [];
  const names = new Set();

  function visit(value, segments) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`Invalid token group: ${segments.join(".") || "root"}`);
    }
    if (Object.hasOwn(value, "$value")) {
      const path = segments.join(".");
      const name = segments.join("-");
      if (names.has(name)) throw new Error(`Duplicate CSS token name: ${path}`);
      names.add(name);
      entries.push([name, formatValue(path, value)]);
      return;
    }
    if (Object.hasOwn(value, "$type")) {
      throw new Error(`Unsupported or invalid token: ${segments.join(".") || "root"}`);
    }
    for (const [key, child] of Object.entries(value)) {
      if (key.startsWith("$")) continue;
      visit(child, [...segments, ...key.split(".")]);
    }
  }

  visit(root, []);
  return entries;
}

export function renderCss(tokens) {
  const entries = flattenTokens(tokens).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const declarations = entries
    .map(([name, value]) => `  --${name}: ${value};`)
    .join("\n");
  return `:root {\n${declarations}\n}\n`;
}

export function loadTokens(inputPath) {
  return JSON.parse(readFileSync(inputPath, "utf8"));
}

export function writeCss(inputPath, outputPath) {
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, renderCss(loadTokens(inputPath)), "utf8");
}

export function checkCss(inputPath, outputPath) {
  return readFileSync(outputPath, "utf8") === renderCss(loadTokens(inputPath));
}

function main(args) {
  const check = args[0] === "--check";
  const [inputPath, outputPath] = check ? args.slice(1) : args;
  if (!inputPath || !outputPath) {
    throw new Error("Usage: node scripts/design_tokens.mjs [--check] input output");
  }
  if (check) {
    if (!checkCss(inputPath, outputPath)) {
      throw new Error(`Generated CSS is stale: ${outputPath}`);
    }
    return;
  }
  writeCss(inputPath, outputPath);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2));
}

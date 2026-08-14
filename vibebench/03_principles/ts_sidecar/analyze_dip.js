/**
 * VibeBench TS sidecar -- DIP import-graph FACTS extractor.
 *
 * Contract: stdin {"files": {...}} -> stdout {modules:[{name, n_classes,
 * n_abstract_classes, imports[]}]}. That is exactly the shape of
 * dip/graph.py::ModuleInfo, so run_dip_ts.py rebuilds those objects and reuses
 * the ALREADY-VALIDATED Python maths verbatim -- Tarjan cycle detection,
 * Martin's Instability / Abstractness / Distance, abstract-dependency ratio.
 * No metric is re-implemented in JavaScript.
 *
 * Only INTERNAL imports are kept (relative specifiers, and path-alias forms
 * like `@/x` or `~/x`), resolved to repo-relative module ids. External packages
 * are dropped: DIP here is about the repo's own dependency direction.
 *
 * "Abstract" for TypeScript = an `interface` declaration or an `abstract class`
 * (the TS analogue of Python's ABC / Protocol).
 *
 * Run:  node analyze_dip.js            (stdin -> stdout)
 *       node analyze_dip.js --self-check
 */
"use strict";

const { Project, SyntaxKind, ts } = require("ts-morph");

function pathToModule(p) {
  return p.replace(/^\//, "").replace(/\.(tsx|ts)$/i, "");
}

/** Normalise "a/b/../c" -> "a/c"; drop "./" segments. */
function normalize(parts) {
  const out = [];
  for (const seg of parts) {
    if (seg === "." || seg === "") continue;
    if (seg === "..") out.pop();
    else out.push(seg);
  }
  return out.join("/");
}

// Directories that hold the members of a monorepo workspace. A module id like
// `libs/logic-apps-shared/src/x` tells us the workspace package
// `logic-apps-shared` lives at `libs/logic-apps-shared`.
const WORKSPACE_ROOTS = new Set([
  "packages", "libs", "apps", "sdks", "extensions", "plugins", "modules",
  "services", "tools",
]);

/**
 * Map workspace package NAME -> its root directory, inferred from the tree.
 *
 * Why this exists (measured 2026-07-31): a monorepo imports its own packages by
 * NAME (`@microsoft/logic-apps-shared`, `effect/Function`), which looks exactly
 * like an npm dependency and was being dropped as external. The loss was severe
 * and wildly repo-dependent -- 0% in microsoft/vscode (all-relative imports) but
 * **8.4%** of all imports in Azure/LogicAppsUX and **61.1%** in Effect-TS/
 * effect-smol, where the package re-imports itself by name. Dropping those edges
 * silently removes real dependencies, and cycles are DIP's only trustworthy
 * signal, so the graph has to see them.
 *
 * package.json is not in our extracted tree (we pull .ts/.tsx only), so the map
 * is derived from the directory layout instead. That is weaker than reading the
 * workspace manifest, but it needs no extra fetch and covers the shape that
 * actually occurs.
 */
function workspaceMap(known) {
  const map = new Map();
  for (const mod of known) {
    const parts = mod.split("/");
    if (parts.length > 2 && WORKSPACE_ROOTS.has(parts[0])) {
      const name = parts[1];
      if (!map.has(name)) map.set(name, `${parts[0]}/${name}`);
    }
  }
  return map;
}

function tryVariants(target, known) {
  if (known.has(target)) return target;
  if (known.has(target + "/index")) return target + "/index";
  for (const pre of ["src/", "app/", "lib/"]) {
    if (known.has(pre + target)) return pre + target;
    if (known.has(pre + target + "/index")) return pre + target + "/index";
  }
  return null;
}

/** `@scope/name/sub` or `name/sub` -> [name, sub]; null for relative forms. */
function splitPackageSpec(spec) {
  const parts = spec.split("/");
  if (spec.startsWith("@")) {
    if (parts.length < 2) return null;
    return [parts[1], parts.slice(2).join("/")];
  }
  return [parts[0], parts.slice(1).join("/")];
}

/**
 * Resolve an import specifier to an internal module id, or null if external.
 * `known` is the set of module ids present in this tree, used to accept both
 * `./x` (file) and `./x/index` (directory) forms.
 */
function resolveSpecifier(spec, fromModule, known, wsMap) {
  let target = null;
  if (spec.startsWith("./") || spec.startsWith("../")) {
    const baseDir = fromModule.split("/").slice(0, -1);
    target = normalize([...baseDir, ...spec.split("/")]);
  } else if (spec.startsWith("@/") || spec.startsWith("~/")) {
    target = normalize(spec.slice(2).split("/"));      // common tsconfig alias
  } else if (spec.startsWith("src/") || spec.startsWith("app/")) {
    target = normalize(spec.split("/"));
  } else {
    // Bare specifier: an npm package OR one of this monorepo's own packages.
    if (!wsMap || !wsMap.size) return null;
    const split = splitPackageSpec(spec);
    if (!split) return null;
    const [name, sub] = split;
    const root = wsMap.get(name);
    if (!root) return null;                             // genuinely external
    const base = sub ? `${root}/${sub}` : root;
    return (tryVariants(base, known)
            || tryVariants(`${root}/src${sub ? "/" + sub : ""}`, known));
  }
  return tryVariants(target, known);
}

function analyzeFiles(filesMap) {
  const project = new Project({
    useInMemoryFileSystem: true,
    skipLoadingLibFiles: true,
    compilerOptions: { allowJs: false, jsx: ts.JsxEmit.Preserve },
  });

  const sources = [];
  for (const [path, source] of Object.entries(filesMap)) {
    try {
      sources.push(project.createSourceFile(path, source, { overwrite: true }));
    } catch (e) { /* skip unparseable */ }
  }
  const known = new Set(sources.map((sf) => pathToModule(sf.getFilePath())));
  const wsMap = workspaceMap(known);

  const modules = [];
  for (const sf of sources) {
    const mod = pathToModule(sf.getFilePath());
    const classes = sf.getClasses();
    const nAbstractClasses = classes.filter(
      (c) => typeof c.isAbstract === "function" && c.isAbstract()).length;
    const nInterfaces = sf.getInterfaces().length;

    const imports = new Set();
    for (const d of sf.getImportDeclarations()) {
      const spec = d.getModuleSpecifierValue();
      const t = resolveSpecifier(spec, mod, known, wsMap);
      if (t && t !== mod) imports.add(t);
    }
    for (const d of sf.getExportDeclarations()) {          // `export ... from './x'`
      const spec = d.getModuleSpecifierValue && d.getModuleSpecifierValue();
      if (!spec) continue;
      const t = resolveSpecifier(spec, mod, known, wsMap);
      if (t && t !== mod) imports.add(t);
    }

    modules.push({
      name: mod,
      // Interfaces count as declared types for the abstractness ratio, in the
      // same spirit as Python counting ABC/Protocol classes.
      n_classes: classes.length + nInterfaces,
      n_abstract_classes: nAbstractClasses + nInterfaces,
      imports: [...imports].sort(),
    });
  }
  return { ok: true, ts_version: require("typescript").version, modules };
}

// ---------------------------------------------------------------------------
function selfCheck() {
  const files = {
    // policy -> concrete detail (a DIP smell), plus a 2-module cycle
    "src/core/service.ts": `
import { Helper } from './helper';
import { Port } from './port';
import express from 'express';
export class Service { run(h: Helper, p: Port) { return 1; } }`,
    "src/core/helper.ts": `
import { Service } from './service';
export class Helper { go(s: Service) { return 2; } }`,
    "src/core/port.ts": `
export interface Port { send(x: string): void; }
export abstract class BasePort implements Port { abstract send(x: string): void; }`,
    "src/app/main.ts": `
import { Service } from '@/core/service';
import { util } from '../shared/util';
export class Main { s = new Service(); }`,
    "src/shared/util.ts": `export const util = 1;`,
    // monorepo: a package imported by NAME, scoped and unscoped, plus a
    // subpath -- the shape that was silently dropped as "external" until
    // 2026-07-31 (61% of all imports in Effect-TS/effect-smol).
    "packages/shared-kit/src/index.ts": `export const kit = 1;`,
    "packages/shared-kit/src/deep.ts": `export const deep = 2;`,
    "libs/consumer/src/use.ts": `
import { kit } from '@acme/shared-kit';
import { deep } from 'shared-kit/deep';
import lodash from 'lodash';
export class Use { k = kit; d = deep; }`,
  };

  const out = analyzeFiles(files);
  const assert = (c, m) => { if (!c) { console.error("SELF-CHECK FAIL:", m); process.exit(1); } };
  const M = Object.fromEntries(out.modules.map((m) => [m.name, m]));

  assert(out.modules.length === 8, `8 modules, got ${out.modules.length}`);

  const svc = M["src/core/service"];
  assert(svc.imports.includes("src/core/helper"), "relative import resolved");
  assert(svc.imports.includes("src/core/port"), "relative import resolved (2)");
  assert(!svc.imports.some((i) => i.includes("express")), "external package dropped");

  assert(M["src/core/helper"].imports.includes("src/core/service"),
         "cycle edge present (helper -> service)");

  const main = M["src/app/main"];
  assert(main.imports.includes("src/core/service"), "@/ alias resolved");
  assert(main.imports.includes("src/shared/util"), "../ traversal resolved");

  const port = M["src/core/port"];
  assert(port.n_abstract_classes === 2,
         `interface + abstract class both abstract, got ${port.n_abstract_classes}`);
  assert(M["src/shared/util"].n_abstract_classes === 0, "plain module has no abstractions");

  // Workspace-package resolution (the 2026-07-31 fix).
  const use = M["libs/consumer/src/use"];
  assert(use.imports.includes("packages/shared-kit/src/index"),
         `scoped workspace import resolved to its index, got ${use.imports}`);
  assert(use.imports.includes("packages/shared-kit/src/deep"),
         `unscoped workspace subpath resolved, got ${use.imports}`);
  assert(!use.imports.some((i) => i.includes("lodash")),
         "a real npm package is still dropped");

  console.log(
    `analyze_dip.js self-check OK: ts ${out.ts_version}, ${out.modules.length} modules; ` +
      `relative/alias/parent/workspace-package imports resolved, externals dropped, ` +
      `cycle edge kept`
  );
}

function main() {
  if (process.argv.includes("--self-check")) { selfCheck(); return; }
  let input = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (c) => (input += c));
  process.stdin.on("end", () => {
    try {
      const req = JSON.parse(input);
      process.stdout.write(JSON.stringify(analyzeFiles(req.files || {})));
    } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) }));
      process.exitCode = 1;
    }
  });
}

main();

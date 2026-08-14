/**
 * VibeBench TS sidecar -- LSP override-compatibility FACTS extractor.
 *
 * Contract: stdin {"files": {...}} -> stdout {"pairs":[...]}: one entry per
 * internal subclass -> nearest-ancestor method override, with the signals that
 * decide substitutability. NO thresholds here; run_lsp_ts.py decides.
 *
 * Liskov: a subtype must be usable wherever its supertype is. An override
 * breaks that when it
 *   - demands MORE  (a new required parameter, or drops one callers pass)   [precondition]
 *   - accepts LESS  (parameter type narrowed -- contravariance violation)
 *   - promises MORE (return type widened -- covariance violation)
 *   - refuses the job (body only throws over a working base)                [behavioural]
 *
 * This mirrors the validated Python rule (overrides.py) so the two languages
 * are comparable, INCLUDING its deterministic false-positive suppressor:
 *   1. base is a `...args: any[]` placeholder (plugin pattern)
 *   2. variance against `any` / `unknown` / an unbound type parameter
 *   3. an optional parameter dropped while a rest/`...args` absorbs it
 *   4. constructor-like static factories (create / from* / of / build)
 *
 * TypeScript advantage over the Python port: `implements` and `extends` are
 * explicit and types are written down, so no import-environment guessing is
 * needed. Constructors are excluded (not substitutable call sites).
 *
 * Run:  node analyze_lsp.js            (stdin -> stdout)
 *       node analyze_lsp.js --self-check
 */
"use strict";

const { Project, SyntaxKind, ts } = require("ts-morph");

const ANY_TYPES = new Set(["any", "unknown", "", "object", "Object"]);
const FACTORY_RE = /^(create|from|of|build|make|new|parse|with)/i;

function pathToModule(p) {
  return p.replace(/\.(tsx|ts)$/i, "");
}

function typeText(node) {
  try {
    const t = node.getTypeNode && node.getTypeNode();
    return t ? t.getText() : "";
  } catch (e) {
    return "";
  }
}

/** Structural summary of one method signature. */
function sigOf(m) {
  const params = (m.getParameters ? m.getParameters() : []).map((p) => ({
    name: p.getName(),
    type: typeText(p),
    optional: p.hasQuestionToken() || p.hasInitializer(),
    rest: p.isRestParameter(),
  }));
  let body = "";
  try {
    const b = m.getBody && m.getBody();
    body = b ? b.getText() : "";
  } catch (e) { /* ignore */ }

  // "Refused bequest": the body's only real action is to throw.
  const stripped = body.replace(/^\{|\}$/g, "").trim();
  const statements = stripped.split(/;|\n/).map((s) => s.trim()).filter(Boolean)
    .filter((s) => !s.startsWith("//") && !s.startsWith("/*") && !s.startsWith("*"));
  const throwsOnly = statements.length > 0 &&
    statements.every((s) => s.startsWith("throw") || s.startsWith("console.") ||
                            s.startsWith("delete ") || s.startsWith("this.log"));
  const hasThrow = statements.some((s) => s.startsWith("throw"));

  return {
    params,
    ret: typeText(m),
    required: params.filter((p) => !p.optional && !p.rest).length,
    hasRest: params.some((p) => p.rest),
    isAbstract: typeof m.isAbstract === "function" ? m.isAbstract() : false,
    isStatic: typeof m.isStatic === "function" ? m.isStatic() : false,
    bodyEmpty: statements.length === 0,
    refuses: throwsOnly && hasThrow,
  };
}

/** True when the BASE is a bare `...args: any[]` plugin placeholder. */
function isPlaceholderBase(base) {
  if (base.isAbstract || base.bodyEmpty) return true;
  if (base.refuses && base.params.every((p) => p.rest || ANY_TYPES.has(p.type))) return true;
  return base.params.length > 0 && base.params.every((p) => p.rest);
}

function compare(subName, sub, base, suppress) {
  const signals = [];

  // --- STATIC: preconditions (mypy's [override] rule) ---
  const newRequired = sub.required - base.required;
  if (newRequired > 0) {
    // Suppressed when the base is a placeholder that defines no real contract.
    if (!(suppress && isPlaceholderBase(base))) signals.push("param_required_added");
  }
  if (sub.params.length < base.params.length) {
    const droppedNames = base.params.slice(sub.params.length);
    const allOptional = droppedNames.every((p) => p.optional || p.rest);
    // Suppressed when the dropped params were optional AND a rest absorbs them.
    const absorbed = suppress && sub.hasRest && allOptional;
    if (!absorbed && !(suppress && isPlaceholderBase(base))) {
      signals.push("param_removed");
    }
  }

  // --- SEMANTIC: variance ---
  const n = Math.min(sub.params.length, base.params.length);
  for (let i = 0; i < n; i++) {
    const st = (sub.params[i].type || "").trim();
    const bt = (base.params[i].type || "").trim();
    if (!st || !bt || st === bt) continue;
    if (suppress && (ANY_TYPES.has(st) || ANY_TYPES.has(bt))) continue;
    // Narrowing: the base accepts a union the subclass no longer accepts.
    if (bt.includes("|") && !st.includes("|") && bt.includes(st)) {
      signals.push("param_type_narrowed");
      break;
    }
  }
  const sr = (sub.ret || "").trim();
  const br = (base.ret || "").trim();
  if (sr && br && sr !== br && !(suppress && (ANY_TYPES.has(sr) || ANY_TYPES.has(br)))) {
    // Widening: the subclass may now return things the base promised it wouldn't.
    if (sr.includes("|") && !br.includes("|") && sr.includes(br)) {
      signals.push("return_type_widened");
    }
  }

  // --- BEHAVIOURAL: refused bequest ---
  if (sub.refuses && !base.refuses && !base.bodyEmpty && !base.isAbstract) {
    signals.push("refused_bequest");
  }

  // Factory statics are constructor-like: not substitutable call sites.
  if (suppress && sub.isStatic && FACTORY_RE.test(subName)) return [];
  return [...new Set(signals)];
}

function analyzeFiles(filesMap, suppress) {
  const project = new Project({
    useInMemoryFileSystem: true,
    skipLoadingLibFiles: true,
    compilerOptions: { allowJs: false, jsx: ts.JsxEmit.Preserve },
  });

  const sources = [];
  for (const [path, source] of Object.entries(filesMap)) {
    try {
      sources.push(project.createSourceFile(path, source, { overwrite: true }));
    } catch (e) { /* unparseable file: skip */ }
  }

  // Index every class in the tree by name (in-repo hierarchy only, exactly the
  // scope the validated Python LSP uses).
  const byName = new Map();
  for (const sf of sources) {
    for (const cls of sf.getClasses()) {
      const n = cls.getName();
      if (n && !byName.has(n)) {
        byName.set(n, { cls, module: pathToModule(sf.getFilePath().replace(/^\//, "")) });
      }
    }
  }

  const pairs = [];
  for (const sf of sources) {
    const mod = pathToModule(sf.getFilePath().replace(/^\//, ""));
    for (const cls of sf.getClasses()) {
      const subName = cls.getName();
      if (!subName) continue;
      // Walk up `extends` links that resolve INSIDE this tree.
      let baseName = null;
      try {
        const ex = cls.getExtends();
        baseName = ex ? ex.getExpression().getText() : null;
      } catch (e) { baseName = null; }
      if (!baseName || !byName.has(baseName)) continue;
      const baseEntry = byName.get(baseName);

      const baseMethods = new Map();
      for (const bm of baseEntry.cls.getMethods()) baseMethods.set(bm.getName(), bm);

      for (const sm of cls.getMethods()) {
        const name = sm.getName();
        if (name === "constructor") continue;
        const bm = baseMethods.get(name);
        if (!bm) continue;
        const subSig = sigOf(sm);
        const baseSig = sigOf(bm);
        const signals = compare(name, subSig, baseSig, suppress);
        pairs.push({
          module: mod,
          qualname: subName,
          base_id: `${baseEntry.module}:${baseName}`,
          method: name,
          lineno: sm.getStartLineNumber(),
          file_path: sf.getFilePath().replace(/^\//, ""),
          signals,
          violation: signals.length > 0,
        });
      }
    }
  }
  return { ok: true, ts_version: require("typescript").version, pairs };
}

// ---------------------------------------------------------------------------
function selfCheck() {
  const files = {
    "shapes.ts": `
export class Base {
  /** does real work */
  save(id: string, opts?: object): string { return id; }
  render(x: string | number): string { return String(x); }
  load(id: string): string { return id; }
  ping(): string { return "ok"; }
}
export class BadChild extends Base {
  // VIOLATION: demands a new required argument (strengthened precondition)
  save(id: string, opts: object, extra: string): string { return id; }
  // VIOLATION: narrows the accepted type (contravariance)
  render(x: string): string { return x; }
  // VIOLATION: refuses a working inherited method
  load(id: string): string { throw new Error("not supported"); }
  // OK: identical signature
  ping(): string { return "pong"; }
}`,
    "plugin.ts": `
export class PluginBase {
  // placeholder: defines no real contract
  run(...args: any[]): any { throw new Error("implement me"); }
  handle(a?: string, b?: string): void {}
}
export class RealPlugin extends PluginBase {
  // NOT a violation: the base is a *args placeholder (suppressor #1)
  run(a: string, b: number): any { return a + b; }
  // NOT a violation: optional params dropped but a rest absorbs them (#3)
  handle(...rest: string[]): void {}
}`,
    "factory.ts": `
export class Repo {
  static create(a: string): Repo { return new Repo(); }
  fetch(x: any): any { return x; }
}
export class SubRepo extends Repo {
  // NOT a violation: factory static (suppressor #4)
  static create(a: string, b: string): SubRepo { return new SubRepo(); }
  // NOT a violation: variance against 'any' (suppressor #2)
  fetch(x: string): string { return x; }
}`,
  };

  const on = analyzeFiles(files, true);
  const off = analyzeFiles(files, false);
  const assert = (c, m) => { if (!c) { console.error("SELF-CHECK FAIL:", m); process.exit(1); } };

  const get = (r, cls, meth) =>
    r.pairs.find((p) => p.qualname === cls && p.method === meth);

  // BadChild 4 (save/render/load/ping) + RealPlugin 2 + SubRepo 2 = 8
  assert(on.pairs.length === 8, `8 override pairs, got ${on.pairs.length}`);

  // --- genuine violations must fire in BOTH modes ---
  assert(get(on, "BadChild", "save").signals.includes("param_required_added"),
         "new required param detected");
  assert(get(on, "BadChild", "render").signals.includes("param_type_narrowed"),
         "narrowed param detected");
  assert(get(on, "BadChild", "load").signals.includes("refused_bequest"),
         "refused bequest detected");
  assert(get(on, "BadChild", "ping").violation === false, "identical signature is clean");

  // --- suppressor must silence the four known false-positive idioms ---
  assert(get(off, "RealPlugin", "run").violation === true, "placeholder fires when suppressor OFF");
  assert(get(on, "RealPlugin", "run").violation === false, "#1 placeholder base suppressed");
  assert(get(on, "RealPlugin", "handle").violation === false, "#3 rest-absorbed drop suppressed");
  assert(get(on, "SubRepo", "create").violation === false, "#4 factory static suppressed");
  assert(get(on, "SubRepo", "fetch").violation === false, "#2 variance vs any suppressed");

  const vOn = on.pairs.filter((p) => p.violation).length;
  const vOff = off.pairs.filter((p) => p.violation).length;
  assert(vOn < vOff, "suppressor reduces violations");

  // --- the language fact behind config.LSP_TS_STRONG_SIGNALS -------------
  // Asserted here so it is re-verified on every run rather than remembered:
  // TypeScript methods are BIVARIANT (strictFunctionTypes exempts method
  // declarations), so tsc accepts all three static "incompatibilities" that
  // mypy rejects in Python. `param_removed` is additionally safe at runtime --
  // JavaScript ignores extra arguments -- which is why it is not a signal.
  const probe = new Project({
    useInMemoryFileSystem: true,
    compilerOptions: { strict: true, noEmit: true },
  });
  probe.createSourceFile("variance_probe.ts", `
abstract class VBase {
  drop(a: string, b: number): boolean { return false; }
  narrow(name: string | undefined): void {}
  require(action: string, ctx?: unknown): void {}
}
class VDrop extends VBase { override drop(a: string): boolean { return true; } }
class VNarrow extends VBase { override narrow(name: string): void {} }
class VRequire extends VBase { override require(action: string, ctx: unknown): void {} }
const vb: VBase = new VDrop();
vb.drop("x", 1);
`);
  const diags = probe.getPreEmitDiagnostics();
  assert(diags.length === 0,
         `tsc must accept bivariant overrides; got ${diags.length} diagnostics`);

  console.log(
    `analyze_lsp.js self-check OK: ts ${on.ts_version}, ${on.pairs.length} override pairs, ` +
      `${vOff} violations (raw) -> ${vOn} (suppressed); all 4 FP idioms silenced; ` +
      `tsc confirms method bivariance (0 diagnostics on the variance probe)`
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
      const suppress = req.suppress_fps !== false;
      process.stdout.write(JSON.stringify(analyzeFiles(req.files || {}, suppress)));
    } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) }));
      process.exitCode = 1;
    }
  });
}

main();

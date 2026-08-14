/**
 * VibeBench TS sidecar -- OCP dispatch-chain FACTS extractor.
 *
 * Contract: stdin {"files": {"rel/path.ts": "source"}} -> stdout JSON with every
 * type/tag dispatch chain found. NO thresholds and NO smell decision here --
 * run_ocp_ts.py applies config.OCP_TS_THRESHOLDS and the closed-set suppressor,
 * mirroring the Python split (dispatch.py emits chains, run_ocp.py judges them).
 *
 * Martin's canonical OCP violation is the "switch statement": a ladder that
 * discriminates on one subject's type or tag and must be EDITED whenever a new
 * variant appears. TypeScript expresses it four ways, all detected here:
 *
 *   kind = "switch_tag"  switch (shape.kind) { case 'circle': ... }   <- discriminated union
 *   kind = "value_switch" if (s.kind === 'a') ... else if (s.kind === 'b')
 *   kind = "instanceof"  if (x instanceof A) ... else if (x instanceof B)
 *   kind = "typeof"      if (typeof x === 'string') ... / switch (typeof x)
 *
 * `typeof` chains are reported with a flag because JavaScript's typeof results
 * are a CLOSED, language-fixed set ("string" | "number" | "boolean" | "object" |
 * "undefined" | "function" | "symbol" | "bigint"): dispatching over them is
 * exhaustive by construction, not the open-ended extension OCP guards against.
 * This is the exact TypeScript analogue of the Python all-builtins closed-set
 * rule that lifted OCP-Python holdout precision 0.33 -> 0.44.
 *
 * Run:  node analyze_ocp.js            (stdin -> stdout)
 *       node analyze_ocp.js --self-check
 */
"use strict";

const { Project, SyntaxKind, ts } = require("ts-morph");

// typeof results: fixed by the language spec => a closed set.
const TYPEOF_RESULTS = new Set([
  "string", "number", "boolean", "object", "undefined",
  "function", "symbol", "bigint",
]);

// Built-in constructors: dispatching over only these is also a closed set.
const BUILTIN_CTORS = new Set([
  "Array", "Date", "Map", "Set", "WeakMap", "WeakSet", "RegExp", "Error",
  "Promise", "String", "Number", "Boolean", "Object", "Function", "Symbol",
  "ArrayBuffer", "Uint8Array", "Int8Array", "Float32Array", "Float64Array",
  "TypeError", "RangeError", "SyntaxError", "EvalError", "ReferenceError",
]);

const EQ_OPS = new Set(["===", "==", "!==", "!="]);

function text(node) {
  try {
    return node.getText();
  } catch (e) {
    return "";
  }
}

/** Dotted path of enclosing classes/functions, e.g. "Foo.bar" ("" at top level). */
function qualnameOf(node) {
  const parts = [];
  let cur = node.getParent();
  while (cur) {
    const k = cur.getKind();
    if (
      k === SyntaxKind.ClassDeclaration ||
      k === SyntaxKind.FunctionDeclaration ||
      k === SyntaxKind.MethodDeclaration ||
      k === SyntaxKind.GetAccessor ||
      k === SyntaxKind.SetAccessor
    ) {
      const n = typeof cur.getName === "function" ? cur.getName() : null;
      if (n) parts.unshift(n);
    } else if (k === SyntaxKind.Constructor) {
      parts.unshift("constructor");
    } else if (
      k === SyntaxKind.VariableDeclaration &&
      typeof cur.getName === "function"
    ) {
      // `const handler = (x) => {...}` -- the arrow's readable name
      const init = cur.getInitializer && cur.getInitializer();
      if (init && (init.getKind() === SyntaxKind.ArrowFunction ||
                   init.getKind() === SyntaxKind.FunctionExpression)) {
        parts.unshift(cur.getName());
      }
    }
    cur = cur.getParent();
  }
  return parts.join(".");
}

/**
 * Classify one if-test. Returns {kind, subject, typeName} or null when the test
 * does not discriminate on a single subject's type/tag.
 */
function classifyTest(node) {
  // `a instanceof B`
  if (node.getKind() === SyntaxKind.BinaryExpression) {
    const op = node.getOperatorToken().getText();
    const left = node.getLeft();
    const right = node.getRight();

    if (op === "instanceof") {
      return { kind: "instanceof", subject: text(left), typeName: text(right) };
    }

    if (EQ_OPS.has(op)) {
      // typeof x === 'string'   (either operand order)
      for (const [a, b] of [[left, right], [right, left]]) {
        if (a.getKind() === SyntaxKind.TypeOfExpression) {
          const lit =
            b.getKind() === SyntaxKind.StringLiteral ? b.getLiteralText() : null;
          return {
            kind: "typeof",
            subject: text(a.getExpression()),
            typeName: lit,
          };
        }
      }
      // x.kind === 'circle' / x.kind === Kind.Circle  (discriminated union)
      for (const [a, b] of [[left, right], [right, left]]) {
        const ak = a.getKind();
        const bk = b.getKind();
        const subjectish =
          ak === SyntaxKind.PropertyAccessExpression ||
          ak === SyntaxKind.Identifier ||
          ak === SyntaxKind.ElementAccessExpression;
        const literalish =
          bk === SyntaxKind.StringLiteral ||
          bk === SyntaxKind.NumericLiteral ||
          bk === SyntaxKind.NoSubstitutionTemplateLiteral ||
          bk === SyntaxKind.PropertyAccessExpression; // enum member
        // Guard: `a.x === b.y` (two subjects) is a comparison, not a dispatch.
        if (subjectish && literalish && !(ak === SyntaxKind.PropertyAccessExpression &&
                                          bk === SyntaxKind.PropertyAccessExpression &&
                                          !/^[A-Z]/.test(text(b).split(".")[0]))) {
          return { kind: "value", subject: text(a), typeName: null };
        }
      }
    }
    // `a === 'x' || a === 'y'` -- one branch testing several tags of one subject
    if (op === "||") {
      const l = classifyTest(left);
      const r = classifyTest(right);
      if (l && r && l.subject === r.subject) return l;
      return null;
    }
  }
  return null;
}

/** Flatten an if / else-if ladder into its tests (else branch is not a test). */
function unrollIf(node) {
  const tests = [];
  let cur = node;
  while (cur && cur.getKind() === SyntaxKind.IfStatement) {
    tests.push(cur.getExpression());
    const els = cur.getElseStatement();
    cur = els && els.getKind() === SyntaxKind.IfStatement ? els : null;
  }
  return tests;
}

function chainFromIf(node, modulePath) {
  const tests = unrollIf(node).map(classifyTest);
  const counts = new Map();
  for (const c of tests) {
    if (c) counts.set(c.subject, (counts.get(c.subject) || 0) + 1);
  }
  if (!counts.size) return null;
  let subject = null;
  let n = 0;
  for (const [s, c] of counts) if (c > n) { subject = s; n = c; }
  if (n < 2) return null; // a single test is a guard, not a dispatch ladder

  const mine = tests.filter((c) => c && c.subject === subject);
  const kinds = new Set(mine.map((c) => c.kind));
  const kind = kinds.has("instanceof")
    ? "instanceof"
    : kinds.has("typeof")
    ? "typeof"
    : "value_switch";
  const typeNames = mine.map((c) => c.typeName).filter(Boolean);
  return {
    module: modulePath,
    qualname: qualnameOf(node),
    subject,
    kind,
    n_branches: n,
    lineno: node.getStartLineNumber(),
    type_names: typeNames,
  };
}

function chainFromSwitch(node, modulePath) {
  const disc = node.getExpression();
  const isTypeof = disc.getKind() === SyntaxKind.TypeOfExpression;
  const subject = isTypeof ? text(disc.getExpression()) : text(disc);

  let n = 0;
  const typeNames = [];
  for (const clause of node.getClauses()) {
    if (clause.getKind() !== SyntaxKind.CaseClause) continue; // skip `default:`
    n += 1;
    const e = clause.getExpression();
    if (e && (e.getKind() === SyntaxKind.StringLiteral ||
              e.getKind() === SyntaxKind.NoSubstitutionTemplateLiteral)) {
      typeNames.push(e.getLiteralText());
    } else if (e) {
      typeNames.push(text(e));
    }
  }
  if (n < 2) return null;
  return {
    module: modulePath,
    qualname: qualnameOf(node),
    subject,
    kind: isTypeof ? "typeof" : "switch_tag",
    n_branches: n,
    lineno: node.getStartLineNumber(),
    type_names: typeNames,
  };
}

/** True if every tested name is a typeof result or a built-in constructor. */
function isClosedTypeSet(kind, typeNames) {
  const names = (typeNames || []).filter(Boolean);
  if (names.length < 2) return false;
  if (kind === "typeof") {
    return names.every((n) => TYPEOF_RESULTS.has(n));
  }
  if (kind === "instanceof") {
    return names.every((n) => BUILTIN_CTORS.has(n.split(".").pop()));
  }
  return false; // tag/value sets are open by nature (new variants are cheap)
}

function pathToModule(p) {
  return p.replace(/\.(tsx|ts)$/i, "");
}

function analyzeFiles(filesMap) {
  const project = new Project({
    useInMemoryFileSystem: true,
    skipLoadingLibFiles: true,
    compilerOptions: { allowJs: false, jsx: ts.JsxEmit.Preserve },
  });

  const chains = [];
  const errors = [];
  for (const [path, source] of Object.entries(filesMap)) {
    try {
      const sf = project.createSourceFile(path, source, { overwrite: true });
      const mod = pathToModule(path);
      sf.forEachDescendant((node) => {
        const k = node.getKind();
        let ch = null;
        if (k === SyntaxKind.IfStatement) {
          // Only the LADDER HEAD: an if that is itself an `else if` was already
          // consumed by its parent's unroll.
          const parent = node.getParent();
          const isElseIf =
            parent &&
            parent.getKind() === SyntaxKind.IfStatement &&
            parent.getElseStatement() === node;
          if (!isElseIf) ch = chainFromIf(node, mod);
        } else if (k === SyntaxKind.SwitchStatement) {
          ch = chainFromSwitch(node, mod);
        }
        if (ch) {
          ch.closed_set = isClosedTypeSet(ch.kind, ch.type_names);
          ch.file_path = path;
          chains.push(ch);
        }
      });
      project.removeSourceFile(sf);
    } catch (e) {
      errors.push({ path, error: String(e && e.message ? e.message : e) });
    }
  }
  return {
    ok: true,
    ts_version: require("typescript").version,
    chains,
    errors,
  };
}

// ---------------------------------------------------------------------------
function selfCheck() {
  const files = {
    // 1. discriminated-union switch: the canonical TS OCP smell (OPEN set)
    "shapes.ts": `
export function area(shape: Shape): number {
  switch (shape.kind) {
    case 'circle': return 1;
    case 'square': return 2;
    case 'triangle': return 3;
    default: return 0;
  }
}`,
    // 2. instanceof ladder over user types (OPEN set)
    "render.ts": `
export class Renderer {
  render(s: unknown): number {
    if (s instanceof Circle) { return 1; }
    else if (s instanceof Square) { return 2; }
    else if (s instanceof Triangle) { return 3; }
    return 0;
  }
}`,
    // 3. typeof ladder: CLOSED set (language-fixed) -> must be flagged closed
    "coerce.ts": `
export function coerce(v: unknown): string {
  if (typeof v === 'string') { return v; }
  else if (typeof v === 'number') { return String(v); }
  else if (typeof v === 'boolean') { return v ? '1' : '0'; }
  return '';
}`,
    // 4. tag if-ladder (discriminated union in if form, OPEN set)
    "route.ts": `
export const route = (a: Action) => {
  if (a.type === 'add') { return 1; }
  else if (a.type === 'del') { return 2; }
  else if (a.type === 'upd') { return 3; }
  return 0;
};`,
    // 5. NEGATIVES: single guard, two different subjects, builtin instanceof
    "neg.ts": `
export function f(a: any, b: any) {
  if (a.kind === 'x') { return 1; }          // single test: not a ladder
  if (a.x === 1) { return 2; } else if (b.y === 2) { return 3; }  // 2 subjects
  if (a instanceof Date) { return 4; } else if (a instanceof Map) { return 5; }
  return 0;
}`,
  };

  const out = analyzeFiles(files);
  const assert = (c, m) => {
    if (!c) { console.error("SELF-CHECK FAIL:", m); process.exit(1); }
  };
  assert(out.errors.length === 0, "no parse errors: " + JSON.stringify(out.errors));

  const by = (mod) => out.chains.filter((c) => c.module === mod);

  const shapes = by("shapes")[0];
  assert(shapes && shapes.kind === "switch_tag", "discriminated-union switch detected");
  assert(shapes.n_branches === 3, `3 cases (default excluded), got ${shapes && shapes.n_branches}`);
  assert(shapes.subject === "shape.kind", `subject, got ${shapes.subject}`);
  assert(shapes.qualname === "area", `qualname, got ${shapes.qualname}`);
  assert(shapes.closed_set === false, "tag set is OPEN");

  const render = by("render")[0];
  assert(render && render.kind === "instanceof", "instanceof ladder detected");
  assert(render.n_branches === 3, `3 branches, got ${render && render.n_branches}`);
  assert(render.qualname === "Renderer.render", `qualname, got ${render.qualname}`);
  assert(render.closed_set === false, "user types are an OPEN set");

  const coerce = by("coerce")[0];
  assert(coerce && coerce.kind === "typeof", "typeof ladder detected");
  assert(coerce.n_branches === 3, `3 branches, got ${coerce && coerce.n_branches}`);
  assert(coerce.closed_set === true, "typeof results are a CLOSED set");

  const route = by("route")[0];
  assert(route && route.kind === "value_switch", "tag if-ladder detected");
  assert(route.n_branches === 3, `3 branches, got ${route && route.n_branches}`);
  assert(route.qualname === "route", `arrow-const qualname, got ${route.qualname}`);

  const neg = by("neg");
  assert(!neg.some((c) => c.n_branches < 2), "no 1-branch 'chains'");
  assert(!neg.some((c) => c.subject === "a.x"), "two-subject ladder rejected");
  const builtin = neg.find((c) => c.kind === "instanceof");
  assert(builtin && builtin.closed_set === true, "builtin-ctor instanceof is CLOSED");

  console.log(
    `analyze_ocp.js self-check OK: ts ${out.ts_version}, ${out.chains.length} chains ` +
      `(switch_tag/instanceof/typeof/value_switch all detected; closed-set rule works)`
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
      process.stdout.write(
        JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) })
      );
      process.exitCode = 1;
    }
  });
}

main();

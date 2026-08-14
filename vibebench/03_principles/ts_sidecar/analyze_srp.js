/**
 * VibeBench TS sidecar -- SRP structural FACTS extractor.
 *
 * Contract: reads {"files": {"rel/path.ts": "source", ...}} as JSON on stdin,
 * writes one JSON document on stdout with per-file, per-class, per-method
 * structural facts. NO metric math here -- LCOM/C3/rubric/thresholds all live
 * in Python (03_principles/srp_ts/run_srp_ts.py), reusing the validated logic.
 *
 * Facts per method: name, lineno, loc, ccn, params, kind flags, `this.X`
 * property accesses (props), `this.m()` calls (calls), and an identifier+JSDoc
 * token bag (tokens) for conceptual-cohesion (C3) computed Python-side.
 *
 * Design notes (mirroring the validated Python evaluator):
 *  - CCN = 1 + branch points (if/for/while/do/case/catch/ternary/&&/||/??),
 *    counting ONLY tokens belonging to the function itself -- nested
 *    function-likes are separate entries (lizard convention).
 *  - Class-field arrow functions (`handleClick = () => {...}`) count as
 *    METHODS -- the dominant method idiom in React/TS classes.
 *  - `constructor` is marked so Python can exclude it from cohesion (same
 *    rationale as Python's __init__ exclusion).
 *  - Parse errors are per-file facts, never crashes.
 *
 * Run:  node analyze_srp.js            (stdin -> stdout JSON)
 *       node analyze_srp.js --self-check
 */
"use strict";

const { Project, SyntaxKind, ts } = require("ts-morph");

// ---------------------------------------------------------------------------
// Tokenisation for C3: split identifiers on camelCase / snake_case, lowercase.
// ---------------------------------------------------------------------------
function splitIdentifier(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+| /)
    .map((t) => t.toLowerCase())
    .filter((t) => t.length >= 2 && !/^\d+$/.test(t));
}

const FUNCTION_KINDS = new Set([
  SyntaxKind.FunctionDeclaration,
  SyntaxKind.FunctionExpression,
  SyntaxKind.ArrowFunction,
  SyntaxKind.MethodDeclaration,
  SyntaxKind.Constructor,
  SyntaxKind.GetAccessor,
  SyntaxKind.SetAccessor,
]);

function isFunctionLike(node) {
  return FUNCTION_KINDS.has(node.getKind());
}

// CCN branch tokens. Logical && || ?? each add a path (lizard convention).
const BRANCH_KINDS = new Set([
  SyntaxKind.IfStatement,
  SyntaxKind.ForStatement,
  SyntaxKind.ForInStatement,
  SyntaxKind.ForOfStatement,
  SyntaxKind.WhileStatement,
  SyntaxKind.DoStatement,
  SyntaxKind.CaseClause,
  SyntaxKind.CatchClause,
  SyntaxKind.ConditionalExpression,
]);
const LOGICAL_OPS = new Set(["&&", "||", "??"]);

/**
 * Facts for one function-like node, counting only its OWN body (descendants
 * inside nested function-likes are skipped -- they are separate functions).
 */
function functionFacts(fn) {
  let ccn = 1;
  const props = new Set();
  const calls = new Set();
  const tokens = [];

  fn.forEachDescendant((node, traversal) => {
    if (isFunctionLike(node)) {
      traversal.skip(); // nested function: its own entry, not ours
      return;
    }
    const kind = node.getKind();
    if (BRANCH_KINDS.has(kind)) {
      ccn += 1;
    } else if (kind === SyntaxKind.BinaryExpression) {
      if (LOGICAL_OPS.has(node.getOperatorToken().getText())) ccn += 1;
    } else if (kind === SyntaxKind.PropertyAccessExpression) {
      // this.X  -> prop access, or this.m(...) -> method call
      if (node.getExpression().getKind() === SyntaxKind.ThisKeyword) {
        const name = node.getName();
        const parent = node.getParent();
        const isCall =
          parent &&
          parent.getKind() === SyntaxKind.CallExpression &&
          parent.getExpression() === node;
        (isCall ? calls : props).add(name);
      }
      tokens.push(...splitIdentifier(node.getName()));
    } else if (kind === SyntaxKind.Identifier) {
      tokens.push(...splitIdentifier(node.getText()));
    } else if (
      kind === SyntaxKind.StringLiteral ||
      kind === SyntaxKind.NoSubstitutionTemplateLiteral
    ) {
      // strings often carry domain vocabulary (same spirit as Python C3)
      tokens.push(...splitIdentifier(node.getLiteralText()));
    }
  });

  // JSDoc text belongs to the method's vocabulary (Python: docstrings).
  if (typeof fn.getJsDocs === "function") {
    for (const doc of fn.getJsDocs()) {
      tokens.push(...splitIdentifier(doc.getInnerText()));
    }
  }

  const params =
    typeof fn.getParameters === "function"
      ? fn.getParameters().filter((p) => p.getName() !== "this").length
      : 0;

  return {
    ccn,
    params,
    loc: fn.getEndLineNumber() - fn.getStartLineNumber() + 1,
    props: [...props].sort(),
    calls: [...calls].sort(),
    tokens,
  };
}

/** Methods of a class: real declarations + accessors + arrow-function fields. */
function classMethods(cls) {
  const out = [];
  for (const m of cls.getMembers()) {
    const kind = m.getKind();
    if (
      kind === SyntaxKind.MethodDeclaration ||
      kind === SyntaxKind.Constructor ||
      kind === SyntaxKind.GetAccessor ||
      kind === SyntaxKind.SetAccessor
    ) {
      if (typeof m.isAbstract === "function" && m.isAbstract()) continue;
      const facts = functionFacts(m);
      out.push({
        name: kind === SyntaxKind.Constructor ? "constructor" : m.getName(),
        lineno: m.getStartLineNumber(),
        is_constructor: kind === SyntaxKind.Constructor,
        is_static: typeof m.isStatic === "function" ? m.isStatic() : false,
        ...facts,
      });
    } else if (kind === SyntaxKind.PropertyDeclaration) {
      const init = m.getInitializer();
      if (
        init &&
        (init.getKind() === SyntaxKind.ArrowFunction ||
          init.getKind() === SyntaxKind.FunctionExpression)
      ) {
        const facts = functionFacts(init);
        out.push({
          name: m.getName(),
          lineno: m.getStartLineNumber(),
          is_constructor: false,
          is_static: typeof m.isStatic === "function" ? m.isStatic() : false,
          ...facts,
        });
      }
    }
  }
  return out;
}

/** Non-blank, non-pure-comment line count (file_loc analogue of radon SLOC). */
function sourceLoc(text) {
  let n = 0;
  let inBlock = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (inBlock) {
      if (line.includes("*/")) inBlock = false;
      continue;
    }
    if (line === "" || line.startsWith("//")) continue;
    if (line.startsWith("/*")) {
      if (!line.includes("*/")) inBlock = true;
      continue;
    }
    n += 1;
  }
  return n;
}

function analyzeFiles(filesMap) {
  const project = new Project({
    useInMemoryFileSystem: true,
    skipLoadingLibFiles: true,
    compilerOptions: { allowJs: false, jsx: ts.JsxEmit.Preserve },
  });

  const results = [];
  for (const [path, source] of Object.entries(filesMap)) {
    let fileFacts = {
      path,
      parse_ok: true,
      error: null,
      file_loc: 0,
      max_func_ccn: 0,
      max_func_params: 0,
      classes: [],
    };
    try {
      const sf = project.createSourceFile(path, source, { overwrite: true });
      fileFacts.file_loc = sourceLoc(source);

      // File-wide function inventory (for max ccn / params), incl. methods.
      let maxCcn = 0;
      let maxParams = 0;
      sf.forEachDescendant((node) => {
        if (!isFunctionLike(node)) return;
        const f = functionFacts(node);
        if (f.ccn > maxCcn) maxCcn = f.ccn;
        if (f.params > maxParams) maxParams = f.params;
      });
      fileFacts.max_func_ccn = maxCcn;
      fileFacts.max_func_params = maxParams;

      for (const cls of sf.getClasses()) {
        fileFacts.classes.push({
          name: cls.getName() || "<anonymous>",
          lineno: cls.getStartLineNumber(),
          loc: cls.getEndLineNumber() - cls.getStartLineNumber() + 1,
          is_abstract: cls.isAbstract(),
          methods: classMethods(cls),
        });
      }
      project.removeSourceFile(sf);
    } catch (e) {
      fileFacts.parse_ok = false;
      fileFacts.error = String(e && e.message ? e.message : e);
    }
    results.push(fileFacts);
  }
  return { ok: true, ts_version: require("typescript").version, files: results };
}

// ---------------------------------------------------------------------------
// Self-check: a 3-responsibility god class vs a cohesive class (TS twin of the
// Python metrics.py fixture). Run: node analyze_srp.js --self-check
// ---------------------------------------------------------------------------
function selfCheck() {
  const god = `
/** Handles users, reports and emails all at once. */
export class GodService {
  private userDb: string[] = [];
  private reportRows: number[] = [];
  private smtpHost = "localhost";

  constructor(private cfg: object) {}

  addUser(name: string): void { if (name) { this.userDb.push(name); } }
  removeUser(name: string): boolean {
    for (const u of this.userDb) { if (u === name) { return true; } }
    return false;
  }
  buildReport(): number { return this.reportRows.length > 0 ? 1 : 0; }
  sumReport(): number {
    let s = 0;
    for (const r of this.reportRows) { s += r; }
    return s;
  }
  sendEmail(to: string, body: string): void {
    const target = to && this.smtpHost ? to : "nobody";
    console.log(target, body);
  }
}`;
  const cohesive = `
export class PageFetcher {
  private cache = new Map<string, string>();
  fetchPage(url: string): string {
    const cached = this.cache.get(url);
    return cached ?? this.download(url);
  }
  download(url: string): string {
    this.cache.set(url, url.toUpperCase());
    return this.cache.get(url) || "";
  }
  handleClick = (url: string): string => this.fetchPage(url);
}`;
  const tsx = `
export class Widget {
  render(): unknown { return <div a={1} b={2}>hello</div>; }
}`;

  const out = analyzeFiles({
    "god.ts": god,
    "cohesive.ts": cohesive,
    "widget.tsx": tsx,
  });

  const assert = (cond, msg) => {
    if (!cond) {
      console.error("SELF-CHECK FAIL:", msg);
      process.exit(1);
    }
  };

  const godCls = out.files[0].classes[0];
  assert(out.files.every((f) => f.parse_ok), "all files parse (incl. .tsx)");
  assert(godCls.methods.length === 6, `god has 6 methods, got ${godCls.methods.length}`);
  const byName = Object.fromEntries(godCls.methods.map((m) => [m.name, m]));
  assert(byName["constructor"].is_constructor, "constructor flagged");
  // Disjoint responsibility clusters visible in props:
  assert(byName["addUser"].props.includes("userDb"), "addUser -> userDb");
  assert(byName["sumReport"].props.includes("reportRows"), "sumReport -> reportRows");
  assert(byName["sendEmail"].props.includes("smtpHost"), "sendEmail -> smtpHost");
  assert(!byName["addUser"].props.includes("reportRows"), "clusters disjoint");
  // CCN: removeUser = 1 + for + if = 3; addUser = 1 + if = 2; sendEmail = 1 + ternary + && = 3
  assert(byName["removeUser"].ccn === 3, `removeUser ccn=3, got ${byName["removeUser"].ccn}`);
  assert(byName["addUser"].ccn === 2, `addUser ccn=2, got ${byName["addUser"].ccn}`);
  assert(byName["sendEmail"].ccn === 3, `sendEmail ccn=3, got ${byName["sendEmail"].ccn}`);
  assert(byName["sendEmail"].params === 2, "sendEmail 2 params");
  // Tokens carry domain vocabulary for C3:
  assert(byName["addUser"].tokens.includes("user"), "identifier splitting works");

  const coh = out.files[1].classes[0];
  assert(coh.methods.length === 3, `cohesive has 3 methods (incl. arrow field), got ${coh.methods.length}`);
  const cohByName = Object.fromEntries(coh.methods.map((m) => [m.name, m]));
  assert(cohByName["handleClick"], "arrow-function class field counted as method");
  assert(cohByName["handleClick"].calls.includes("fetchPage"), "arrow field this-call captured");
  assert(cohByName["fetchPage"].calls.includes("download"), "method call edge captured");
  assert(cohByName["fetchPage"].props.includes("cache"), "shared prop captured");
  assert(cohByName["fetchPage"].ccn === 2, `?? counts a branch, got ${cohByName["fetchPage"].ccn}`);

  const widget = out.files[2].classes[0];
  assert(widget.methods.length === 1, "tsx class parsed");

  console.log(
    `analyze_srp.js self-check OK: ts ${out.ts_version}, ` +
      `${out.files.length} files, god=${godCls.methods.length}m ` +
      `cohesive=${coh.methods.length}m (arrow field counted), tsx parses`
  );
}

// ---------------------------------------------------------------------------
function main() {
  if (process.argv.includes("--self-check")) {
    selfCheck();
    return;
  }
  let input = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => (input += chunk));
  process.stdin.on("end", () => {
    try {
      const req = JSON.parse(input);
      const out = analyzeFiles(req.files || {});
      process.stdout.write(JSON.stringify(out));
    } catch (e) {
      process.stdout.write(
        JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) })
      );
      process.exitCode = 1;
    }
  });
}

main();

/**
 * VibeBench TS sidecar -- ISP interface/implementer FACTS extractor.
 *
 * Contract: stdin {"files": {...}} -> stdout {interfaces:[...], implementers:[...]}.
 * NO thresholds here; run_isp_ts.py applies config.ISP_TS_THRESHOLDS.
 *
 * Interface Segregation: "no client should be forced to depend on methods it
 * does not use". The measurable shape of that is a WIDE interface whose
 * implementers are full of stub methods they never really implement.
 *
 * Two interface tiers (the TS analogue of Python's abc / Protocol / implicit):
 *   kind = "interface"       a real `interface X { ... }` declaration
 *   kind = "abstract_class"  an abstract class; its abstract members are the contract
 *
 * Per interface : width = number of contract members.
 * Per implementer: how many of those members it implements, and how many of
 * those implementations are STUBS (empty body, `return null/undefined/[]/{}`,
 * or a bare `throw`). A wide interface whose implementers use all of it is NOT
 * a violation -- the flag needs width PLUS forced stubs.
 *
 * Run:  node analyze_isp.js            (stdin -> stdout)
 *       node analyze_isp.js --self-check
 */
"use strict";

const { Project, SyntaxKind, ts } = require("ts-morph");

function pathToModule(p) {
  return p.replace(/^\//, "").replace(/\.(tsx|ts)$/i, "");
}

/** Is this method body a stub (no real behaviour)? */
function isStub(m) {
  let body = "";
  try {
    const b = m.getBody && m.getBody();
    if (!b) return true;                       // declaration only
    body = b.getText();
  } catch (e) {
    return false;
  }
  const inner = body.replace(/^\{|\}$/g, "").trim();
  if (!inner) return true;                     // {}
  const stmts = inner.split(/;|\n/).map((s) => s.trim()).filter(Boolean)
    .filter((s) => !s.startsWith("//") && !s.startsWith("/*") && !s.startsWith("*"));
  if (stmts.length === 0) return true;
  if (stmts.length === 1) {
    const s = stmts[0];
    if (/^throw\b/.test(s)) return true;
    if (/^return\s*(null|undefined|\{\}|\[\]|""|''|0|false|void 0)?\s*$/.test(s)) return true;
    if (/^return\s+Promise\.resolve\(\s*\)\s*$/.test(s)) return true;
  }
  return false;
}

function memberNames(node, onlyAbstract) {
  const names = [];
  // interface members
  if (typeof node.getMethods === "function") {
    for (const m of node.getMethods()) {
      if (onlyAbstract && typeof m.isAbstract === "function" && !m.isAbstract()) continue;
      const n = m.getName ? m.getName() : null;
      if (n && n !== "constructor") names.push(n);
    }
  }
  if (typeof node.getProperties === "function") {
    for (const p of node.getProperties()) {
      // A function-typed property is part of the callable contract.
      let t = "";
      try { const tn = p.getTypeNode && p.getTypeNode(); t = tn ? tn.getText() : ""; } catch (e) {}
      if (t.includes("=>")) {
        if (onlyAbstract && typeof p.isAbstract === "function" && !p.isAbstract()) continue;
        const n = p.getName ? p.getName() : null;
        if (n) names.push(n);
      }
    }
  }
  return [...new Set(names)];
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

  // --- pass 1: index the contracts -------------------------------------
  const ifaces = new Map();   // name -> {id, kind, module, members[]}
  for (const sf of sources) {
    const mod = pathToModule(sf.getFilePath());
    for (const it of sf.getInterfaces()) {
      const n = it.getName();
      if (!n || ifaces.has(n)) continue;
      ifaces.set(n, { id: `${mod}:${n}`, name: n, kind: "interface",
                      module: mod, lineno: it.getStartLineNumber(),
                      members: memberNames(it, false) });
    }
    for (const cls of sf.getClasses()) {
      if (!cls.isAbstract || !cls.isAbstract()) continue;
      const n = cls.getName();
      if (!n || ifaces.has(n)) continue;
      ifaces.set(n, { id: `${mod}:${n}`, name: n, kind: "abstract_class",
                      module: mod, lineno: cls.getStartLineNumber(),
                      members: memberNames(cls, true) });
    }
  }

  // --- pass 2: implementers -------------------------------------------
  const implementers = [];
  for (const sf of sources) {
    const mod = pathToModule(sf.getFilePath());
    for (const cls of sf.getClasses()) {
      const clsName = cls.getName();
      if (!clsName) continue;

      // ISP is about a CLIENT being forced to supply members it does not use.
      // An abstract partial implementation is not a client: it is deferring
      // the obligation to its subclasses, and neutral defaults (`return false`,
      // `return null as any`) are the Template Method pattern, not refusal.
      // Without this, every `abstract class BaseXService implements IXService`
      // in the corpus read as a forced-stub implementer.
      const clsIsAbstract = typeof cls.isAbstract === "function" && cls.isAbstract();

      const contracts = [];
      try {
        for (const impl of cls.getImplements()) {
          const nm = impl.getExpression().getText().split("<")[0];
          if (ifaces.has(nm)) contracts.push(ifaces.get(nm));
        }
      } catch (e) { /* ignore */ }
      try {
        const ex = cls.getExtends();
        if (ex) {
          const nm = ex.getExpression().getText().split("<")[0];
          if (ifaces.has(nm) && ifaces.get(nm).kind === "abstract_class") {
            contracts.push(ifaces.get(nm));
          }
        }
      } catch (e) { /* ignore */ }
      if (!contracts.length) continue;

      // A class whose members are installed at RUNTIME (the `dynamicProto`
      // idiom used across microsoft/ApplicationInsights-JS: bodies are assigned
      // inside the constructor closure) declares every member with an empty
      // body. Read statically that looks like a class stubbing 100% of a wide
      // contract -- the strongest possible ISP signal -- when it is in fact a
      // full implementation. Stub counting is therefore skipped for them.
      let dynamicBodies = false;
      try {
        dynamicBodies = /\bdynamicProto\s*\(/.test(cls.getText());
      } catch (e) { /* ignore */ }

      const own = new Map();
      for (const m of cls.getMethods()) {
        // An `abstract` re-declaration is not an implementation -- it passes
        // the obligation down. Counting it as a stub made every abstract base
        // that restates its interface look like a forced-stub implementer.
        if (typeof m.isAbstract === "function" && m.isAbstract()) continue;
        own.set(m.getName(), m);
      }
      for (const p of cls.getProperties()) {
        const init = p.getInitializer && p.getInitializer();
        if (init && (init.getKind() === SyntaxKind.ArrowFunction ||
                     init.getKind() === SyntaxKind.FunctionExpression)) {
          own.set(p.getName(), init);
        }
      }

      for (const iface of contracts) {
        const width = iface.members.length;
        if (!width) continue;
        let implemented = 0, stubs = 0;
        for (const name of iface.members) {
          const m = own.get(name);
          if (!m) continue;                    // inherited or not overridden here
          implemented += 1;
          if (!dynamicBodies && !clsIsAbstract && isStub(m)) stubs += 1;
        }
        implementers.push({
          iface_id: iface.id,
          iface_kind: iface.kind,
          iface_width: width,
          class_id: `${mod}:${clsName}`,
          module: mod,
          qualname: clsName,
          lineno: cls.getStartLineNumber(),
          file_path: sf.getFilePath().replace(/^\//, ""),
          n_implemented: implemented,
          n_stubs: stubs,
          coverage: width ? implemented / width : 0,
          stub_ratio: implemented ? stubs / implemented : 0,
        });
      }
    }
  }

  return {
    ok: true,
    ts_version: require("typescript").version,
    interfaces: [...ifaces.values()].map((i) => ({
      iface_id: i.id, name: i.name, kind: i.kind, module: i.module,
      lineno: i.lineno, n_members: i.members.length,
    })),
    implementers,
  };
}

// ---------------------------------------------------------------------------
function selfCheck() {
  const files = {
    "fat.ts": `
export interface FatRepo {
  create(x: string): void;
  read(id: string): string;
  update(id: string, x: string): void;
  remove(id: string): void;
  archive(id: string): void;
  restore(id: string): void;
}
// forced to stub almost everything -> the ISP smell
export class ReadOnlyRepo implements FatRepo {
  create(x: string): void { throw new Error("unsupported"); }
  read(id: string): string { return "real value " + id; }
  update(id: string, x: string): void { throw new Error("unsupported"); }
  remove(id: string): void { throw new Error("unsupported"); }
  archive(id: string): void {}
  restore(id: string): void {}
}`,
    "thin.ts": `
export interface Reader { read(id: string): string; }
// implements everything for real -> NOT a violation
export class GoodReader implements Reader {
  read(id: string): string { const v = id.trim(); return v.toUpperCase(); }
}`,
    "abs.ts": `
export abstract class BaseStore {
  abstract get(k: string): string;
  abstract put(k: string, v: string): void;
  helper(): string { return "shared"; }
}
export class MemStore extends BaseStore {
  private m = new Map<string,string>();
  get(k: string): string { return this.m.get(k) || ""; }
  put(k: string, v: string): void { this.m.set(k, v); }
}
// an abstract MIDDLE class restates the contract -- those are not stubs
export abstract class PartialStore extends BaseStore {
  abstract get(k: string): string;
  put(k: string, v: string): void { this.cache[k] = v; }
  private cache: Record<string,string> = {};
}`,
    "dyn.ts": `
export interface Tracker {
  trackEvent(name: string): void;
  trackPage(name: string): void;
}
// bodies installed at runtime by dynamicProto -- statically they look empty
export class DynTracker implements Tracker {
  constructor() {
    dynamicProto(DynTracker, this, (_self: any) => {
      _self.trackEvent = (name: string) => { console.log(name); };
      _self.trackPage = (name: string) => { console.log(name); };
    });
  }
  trackEvent(name: string): void {}
  trackPage(name: string): void {}
}`,
  };

  const out = analyzeFiles(files);
  const assert = (c, m) => { if (!c) { console.error("SELF-CHECK FAIL:", m); process.exit(1); } };
  const iface = (n) => out.interfaces.find((i) => i.name === n);
  const impl = (c) => out.implementers.find((i) => i.qualname === c);

  assert(iface("FatRepo") && iface("FatRepo").n_members === 6,
         `FatRepo width 6, got ${iface("FatRepo") && iface("FatRepo").n_members}`);
  assert(iface("FatRepo").kind === "interface", "interface kind");
  assert(iface("Reader").n_members === 1, "thin interface width 1");
  assert(iface("BaseStore") && iface("BaseStore").kind === "abstract_class",
         "abstract class indexed as a contract");
  assert(iface("BaseStore").n_members === 2,
         `only ABSTRACT members count (2), got ${iface("BaseStore").n_members}`);

  const ro = impl("ReadOnlyRepo");
  assert(ro && ro.n_implemented === 6, "implements all 6");
  assert(ro.n_stubs === 5, `5 stubs (3 throw + 2 empty), got ${ro && ro.n_stubs}`);
  assert(ro.stub_ratio > 0.8, `high stub ratio, got ${ro.stub_ratio}`);

  const gr = impl("GoodReader");
  assert(gr && gr.n_stubs === 0, "real implementation is not a stub");
  assert(gr.coverage === 1, "full coverage");

  const ms = impl("MemStore");
  assert(ms && ms.n_stubs === 0 && ms.coverage === 1,
         "abstract-class implementer measured");

  // FP fix 1: an abstract re-declaration defers, it does not stub -- and an
  // abstract class is not a forced client in the first place.
  const ps = out.implementers.find((i) => i.qualname === "PartialStore");
  assert(ps && ps.n_stubs === 0,
         `abstract re-declaration is not a stub, got ${ps && ps.n_stubs}`);
  assert(ps.n_implemented === 1,
         `only the real body counts as implemented, got ${ps && ps.n_implemented}`);
  const rd = impl("ReadOnlyRepo");
  assert(rd && rd.n_stubs === 5,
         "a CONCRETE class still gets its stubs counted");

  // FP fix 2: dynamicProto installs bodies at runtime -- not a 100% stubber.
  const dt = impl("DynTracker");
  assert(dt && dt.n_implemented === 2 && dt.n_stubs === 0,
         `dynamicProto class must not read as all-stubs, got ${dt && dt.n_stubs}`);

  console.log(
    `analyze_isp.js self-check OK: ts ${out.ts_version}, ` +
      `${out.interfaces.length} contracts, ${out.implementers.length} implementer pairs; ` +
      `fat/thin + stub detection work`
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

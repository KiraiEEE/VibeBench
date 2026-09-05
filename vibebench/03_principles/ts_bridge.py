"""
Bridge to the Node ts-morph sidecar (03_principles/ts_sidecar).

One function: analyze_tree(files) -> facts dict, by piping {"files": {...}} as
JSON into `node analyze_srp.js` and parsing the JSON reply. One sidecar process
per PR TREE (all files batched in a single call -- process spawn is the
overhead, so we pay it once per PR, not once per file).

Deterministic: the sidecar's deps are pinned (ts-morph 28.0.0 / typescript
5.9.3, package-lock.json committed); same input -> same output.

Failure model: never raises on sidecar problems -- returns {"ok": False,
"error": ...} so callers can record a status row and continue (the "one bad
item must not abort the batch" rule).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

SIDECAR = C.TS_SIDECAR_DIR / "analyze_srp.js"        # back-compat default
SCRIPTS = {
    "srp": C.TS_SIDECAR_DIR / "analyze_srp.js",
    "ocp": C.TS_SIDECAR_DIR / "analyze_ocp.js",
    "lsp": C.TS_SIDECAR_DIR / "analyze_lsp.js",
    "isp": C.TS_SIDECAR_DIR / "analyze_isp.js",
    "dip": C.TS_SIDECAR_DIR / "analyze_dip.js",
}
_EMPTY = {
    "srp": {"ok": True, "files": []},
    "ocp": {"ok": True, "chains": [], "errors": []},
    "lsp": {"ok": True, "pairs": []},
    "isp": {"ok": True, "interfaces": [], "implementers": []},
    "dip": {"ok": True, "modules": []},
}


def analyze_tree(files: dict[str, str],
                 timeout_s: int | None = None,
                 script: str = "srp",
                 **opts) -> dict:
    """Run a facts extractor over {relative_path: source}.

    script: "srp" (per-class metrics), "ocp" (dispatch chains), or
    "lsp" (override pairs; pass suppress_fps=False to see the raw rule).
    Empty-result shape matches the script so callers need no special-casing.
    """
    if not files:
        return _EMPTY[script]
    payload = json.dumps({"files": files, **opts})
    try:
        r = subprocess.run(
            ["node", str(SCRIPTS[script])],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s or C.TS_SIDECAR_TIMEOUT_S,
            cwd=str(C.TS_SIDECAR_DIR),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"sidecar timeout after {C.TS_SIDECAR_TIMEOUT_S}s"}
    except FileNotFoundError:
        return {"ok": False, "error": "node not found on PATH"}
    if r.returncode != 0 and not r.stdout:
        return {"ok": False, "error": f"sidecar exit {r.returncode}: {r.stderr[:500]}"}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bad sidecar JSON: {e}; stderr: {r.stderr[:300]}"}


if __name__ == "__main__":
    out = analyze_tree({
        "a.ts": "export class A { x = 1; get(): number { return this.x; } "
                "set(v: number): void { this.x = v; } }",
    })
    assert out.get("ok"), out
    cls = out["files"][0]["classes"][0]
    assert cls["name"] == "A" and len(cls["methods"]) == 2, cls
    assert cls["methods"][0]["props"] == ["x"], cls["methods"][0]

    ocp = analyze_tree({
        "b.ts": "export function f(s: any) { if (s instanceof A) return 1; "
                "else if (s instanceof B) return 2; else if (s instanceof C) return 3; }",
    }, script="ocp")
    assert ocp.get("ok"), ocp
    ch = ocp["chains"][0]
    assert ch["kind"] == "instanceof" and ch["n_branches"] == 3, ch
    assert ch["closed_set"] is False, ch

    print("ts_bridge.py self-check OK:", f"ts {out.get('ts_version')}, "
          f"srp: 1 class/{len(cls['methods'])} methods; "
          f"ocp: {ch['kind']} chain n={ch['n_branches']}")

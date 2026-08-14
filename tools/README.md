# `tools/` — third-party analysers used ONLY as an independent cross-check

Nothing here is a pipeline dependency. Our own detectors stay primary because the
three languages must remain numerically commensurable (`metrics.py::
lcom_from_access_sets` = LCOM4 and `c3_from_token_bags` are shared verbatim across
Python / TypeScript / Java). These tools exist to answer one question: *does an
established, published Java analyser order and flag the same classes we do?*

Driver: `vibebench/05_crosscheck/run_crosscheck_java.py`.
Write-up: `VibeBench_Java_Guide.html` §6 "Convergent validity", `JAVA_STATUS.md` §9.

## CK 0.7.0 (Aniche) — per-class metrics

```
mvn -f pom.xml dependency:build-classpath -Dmdep.outputFile=cp_ck.txt
java -cp "$(cat cp_ck.txt)" com.github.mauricioaniche.ck.Runner <srcdir> false 0 false <prefix>
```

`useJars=false` is what makes CK usable on a bare `git archive` tree: it falls
back to partial resolution instead of demanding a classpath.

**CK's `lcom` is LCOM1 and its `lcom*` is Henderson-Sellers. LCOM4 — ours — is not
implemented.** CK is therefore a RANK CORRELATE, never an agreement check, and the
write-up says so.

**Known crash.** CK embeds the Eclipse JDT compiler, which on an unbuildable tree
can die outright — `OpenHFT/Chronicle-Core` produces
`NullPointerException: Cannot read field "declaringClass" because "mb" is null` in
`ReferenceExpression.generateCode` (a `MethodHandle.invokeExact` method reference
whose type JDT cannot resolve). The crash kills the run and leaves a 0-byte csv.
The driver retries with CK's own `maxFilesPerPartition=100`, which confines each
JDT session to a slice and succeeds. Partitioning degrades cross-file metrics
(`cbo`, `fanin`, `fanout`, `rfc`) but not the within-class ones we compare on
(`wmc`, `loc`, `totalMethodsQty`, `lcom`, `lcom*`, `tcc`). Affected repos are
marked `ck_partitioned` in `java_crosscheck_status.csv`.

## PMD 7.7.0 — design rules

```
mvn -f pom_pmd.xml dependency:build-classpath -Dmdep.outputFile=cp_pmd.txt
java -cp "$(cat cp_pmd.txt)" net.sourceforge.pmd.cli.PmdCli check \
     -d <srcdir> -R pmd_srp_ruleset.xml -f xml -r <out.xml> --no-cache --no-progress
```

`pmd_srp_ruleset.xml` selects the design rules that map onto our SRP rubric, **at
their default thresholds**. Defaults are deliberate: re-tuning PMD until it agreed
with us would destroy the only thing it is being asked for.

`ExcessiveClassLength` was **removed in PMD 7**; `NcssCount` is its documented
successor and is used in its place.

## DesigniteJava 1.1.2 (Community) — design smells for SRP, LSP and DIP

Not on Maven Central and the binary download is form-gated, so it is built from
source (`github.com/tushartushar/DesigniteJava`) and the jar is committed here.

```
git clone --depth 1 https://github.com/tushartushar/DesigniteJava.git
# apply the dependency exclusion below, then:
mvn -DskipTests package        # -> target/DesigniteJava.jar
mkdir -p <workdir>/output && (cd <workdir> && java -jar DesigniteJava-1.1.2.jar -i <srcdir> -o <workdir>)
```

**Two build/run gotchas, both fixed in `pom_designite_patched.xml` and in the driver:**

1. `org.eclipse.text:3.5.101` drags in the 2008-era
   `org.eclipse.equinox:org.eclipse.equinox.common:3.6.0` under a *different*
   groupId, so Maven cannot dedupe it against the
   `org.eclipse.platform:...:3.20.300` that `jdt.core:3.31` needs. Whichever lands
   first on the classpath wins, and 3.6.0 has no `SubMonitor.split` →
   `NoSuchMethodError` in `ASTParser.createAST`. The patched pom excludes the
   stale one.
2. It **ignores `-o`** and writes to `<cwd>/output/`, which must already exist.
   The driver runs it with `cwd` set per repo.

Confirms JAVA_STATUS §0c: it is genuinely source-only — the old TOOLS.md
"needs a build" rejection was wrong.

## Deliberately NOT used

| Tool | Reason |
|---|---|
| **JDepend** | Reads `.class` files only. Our inputs are bare `git archive` trees with no build. It also computes *exactly* the Martin A/I/D + package-cycle metrics `dip/graph.py` already implements and that are shared across all three languages — so it would cost a build for numbers we already have. |
| **ArchUnit** | Also bytecode-only, and it is a *conformance DSL*: you hand-write the rules. It checks a project against an architecture you declare, which is not what a cross-corpus measurement needs. |
| **SonarQube / Qodana** | Server or Docker plus per-project configuration — irreproducible as a thesis artefact. |
| **DV8** | Commercial. |

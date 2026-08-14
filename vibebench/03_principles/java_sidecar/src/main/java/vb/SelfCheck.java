package vb;

import java.nio.file.*;
import java.util.*;

/**
 * Fixture assertions for the Java fact extractor -- the "unit test", following
 * the project convention that every pure evaluator self-checks when run.
 *
 * Each assertion below pins one DEFECT that made the first Java run void
 * (JAVA_STATUS.md section 2). They exist so those defects cannot silently return.
 *
 * Run: java -cp sidecar.jar vb.Facts --self-check
 */
public class SelfCheck {

    static int failures = 0;

    static void check(boolean cond, String what) {
        System.out.printf("  [%s] %s%n", cond ? "ok  " : "FAIL", what);
        if (!cond) failures++;
    }

    static final String GOD = """
            package app;
            /** Does persistence, formatting and networking all at once. */
            public class GodService {
                private String dbHost; private int dbPort; private String cache;
                private String template; private String locale;
                private String endpoint; private int timeout;
                public void connectDb() { if (dbHost != null) { cache = dbHost + dbPort; } }
                public void closeDb() { cache = null; }
                public String queryDb(String sql) { return cache + sql + dbPort; }
                public String renderHtml(String body) { return template + body + locale; }
                public String renderPdf(String body) { return template + body; }
                public String formatDate(String d) { return locale + d; }
                public void sendRequest(String p) { if (endpoint != null && timeout > 0) { } }
                public void retryRequest(String p) { for (int i = 0; i < timeout; i++) { sendRequest(p); } }
                public String describe(Object o) {
                    if (o instanceof String) { return "str"; }
                    else if (o instanceof Integer) { return "int"; }
                    else if (o instanceof Double) { return "dbl"; }
                    else { return "other"; }
                }
                public static class Inner { public void hello() { } }
            }
            """;

    static final String COHESIVE = """
            package app;
            public class Money {
                private long cents; private String currency;
                public long getCents() { return cents; }
                public String getCurrency() { return currency; }
                public Money plus(Money o) { return make(cents + o.cents); }
                private Money make(long c) { Money m = new Money(); m.cents = c; m.currency = currency; return m; }
            }
            """;

    static final String ENUMS = """
            package app;
            public enum Kind { A, B, C }
            """;

    static final String SWITCHES = """
            package app;
            public class Switches {
                static final int MAX_VALUE = 7;
                public String onEnum(Kind k) {
                    switch (k) { case A: return "a"; case B: return "b"; case C: return "c"; }
                    return "?";
                }
                public String onInt(int v) {
                    switch (v) { case 1: return "x"; case MAX_VALUE: return "y"; default: return "z"; }
                }
            }
            """;

    static final String HIER = """
            package app;
            public interface Repo {
                void save(String s); void load(String s); void delete(String s);
                void flush(); void close(); void reindex(); void vacuum();
            }
            """;

    static final String IMPL = """
            package app;
            public class ReadOnlyRepo extends Base implements Repo {
                public void save(String s) { throw new UnsupportedOperationException("read only"); }
                public void load(String s) { }
                public void delete(String s) { throw new UnsupportedOperationException(); }
                public void flush() { } public void close() { }
                public void reindex() { } public void vacuum() { }
                @Override public String render(String in) {
                    if (in == null) throw new IllegalArgumentException("null");
                    return in.trim();
                }
            }
            """;

    static final String BASE = """
            package app;
            public class Base { public String render(String in) { return String.valueOf(in); } }
            """;

    public static void run() throws Exception {
        Path root = Files.createTempDirectory("vbjava");
        Path src = root.resolve("src/main/java/app");
        Files.createDirectories(src);
        Files.writeString(src.resolve("GodService.java"), GOD);
        Files.writeString(src.resolve("Money.java"), COHESIVE);
        Files.writeString(src.resolve("Kind.java"), ENUMS);
        Files.writeString(src.resolve("Switches.java"), SWITCHES);
        Files.writeString(src.resolve("Repo.java"), HIER);
        Files.writeString(src.resolve("Base.java"), BASE);
        Files.writeString(src.resolve("ReadOnlyRepo.java"), IMPL);

        Facts.Out out = new Facts.Out();
        Facts.run(root, out);

        System.out.println("Java fact extractor self-check");
        check(out.ok, "run completed");
        check(out.files.stream().allMatch(f -> f.parseOk), "all 7 fixtures parse");
        check(out.sourceRoots.contains("src\\main\\java") || out.sourceRoots.contains("src/main/java"),
                "source root inferred from package decl (not repo root)");

        Map<String, Facts.TypeF> byId = new HashMap<>();
        for (Facts.TypeF t : out.types) byId.put(t.id, t);

        // DEFECT 7: nested/inner classes were folded into the outer type
        check(byId.containsKey("app.GodService.Inner"), "nested class gets its OWN row (defect #7)");
        check(byId.get("app.GodService").methods.stream().noneMatch(m -> m.name.equals("hello")),
                "nested class methods are NOT counted in the outer type");

        // DEFECT 6: simple-name keys collided; ids must be fully qualified
        check(byId.keySet().stream().allMatch(k -> k.startsWith("app.")), "type ids are fully qualified (defect #6)");

        // Resolution: the capability tree-sitter cannot provide
        Facts.TypeF ro = byId.get("app.ReadOnlyRepo");
        check(ro.supertypes.contains("app.Base") && ro.supertypes.contains("app.Repo"),
                "supertypes resolved to FQNs on a bare tree");
        check(ro.allAncestors.contains("app.Base"), "transitive ancestors resolved");

        // DEFECT 5: lineno was re-found by regex and broke on overloads
        check(byId.get("app.Money").methods.stream().allMatch(m -> m.lineno > 0),
                "every method carries a real AST lineno (defect #5)");

        // DEFECT 3: every chain had the literal subject "instanceof"; 1-branch "chains"
        Facts.MethodF desc = byId.get("app.GodService").methods.stream()
                .filter(m -> m.name.equals("describe")).findFirst().orElseThrow();
        check(desc.chains.size() == 1, "one instanceof ladder -> exactly one chain");
        check(desc.chains.get(0).subject.equals("o"), "chain subject is the REAL subject 'o' (defect #3)");
        check(desc.chains.get(0).nBranches == 3, "ladder counted as 3 branches, not 1");

        // DEFECT 4: enum-ness was guessed from label CASING, so `case MAX_VALUE:` counted as enum
        Facts.TypeF sw = byId.get("app.Switches");
        Facts.MethodF onEnum = sw.methods.stream().filter(m -> m.name.equals("onEnum")).findFirst().orElseThrow();
        Facts.MethodF onInt = sw.methods.stream().filter(m -> m.name.equals("onInt")).findFirst().orElseThrow();
        check(onEnum.chains.get(0).closedSet, "enum switch IS closed-set (subject type resolved)");
        check(!onInt.chains.get(0).closedSet,
                "int switch with an UPPERCASE constant label is NOT closed-set (defect #4)");
        check(onEnum.chains.get(0).nBranches == 3 && onInt.chains.get(0).nBranches == 2,
                "default: is excluded from the branch count");

        // ISP / LSP body kinds
        long stubs = ro.methods.stream().filter(m -> "throw".equals(m.bodyKind)).count();
        check(stubs == 2, "throw-only overrides detected as stubs (ISP forced stub / LSP refused bequest)");
        check(ro.methods.stream().filter(m -> m.name.equals("render")).findFirst().orElseThrow().guardThrows == 1,
                "precondition-strengthening guard detected (LSP)");
        check(byId.get("app.Repo").kind.equals("interface") && byId.get("app.Repo").methods.size() == 7,
                "interface width measured");

        // SRP inputs: LCOM/C3 need non-empty access sets and token bags
        Facts.TypeF god = byId.get("app.GodService");
        check(god.methods.stream().anyMatch(m -> !m.props.isEmpty()), "field-access sets populated (LCOM4 input)");
        check(god.methods.stream().anyMatch(m -> !m.calls.isEmpty()), "sibling-call sets populated (LCOM4 input)");
        check(god.methods.stream().allMatch(m -> !m.tokens.isEmpty()), "token bags populated (C3 input)");
        check(god.methods.stream().mapToInt(m -> m.ccn).max().orElse(0) >= 4, "CCN counts decision points");

        // DIP shape
        Facts.ModuleF impl = out.modules.stream().filter(m -> m.file.endsWith("ReadOnlyRepo.java"))
                .findFirst().orElseThrow();
        check(impl.imports.contains("app.Base") || impl.imports.contains("app.Repo"),
                "internal dependency edges emitted for DIP");

        System.out.printf("%n%s  (%d failures)%n", failures == 0 ? "ALL PASS" : "FAILURES PRESENT", failures);
        if (failures > 0) System.exit(1);
    }
}

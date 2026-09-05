package vb;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.*;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;

/**
 * VibeBench Java fact extractor -- the single source of structural facts for
 * ALL FIVE principles, replacing the tree-sitter extractor.
 *
 * WHY THIS EXISTS (see JAVA_STATUS.md section 6): tree-sitter parses Java but
 * cannot RESOLVE symbols, so two classes both named `Node` collapse into one
 * and every hierarchy silently corrupts. LSP/ISP/DIP are relations between
 * types; they need resolution. The Phase 0b pilot measured 93-100% supertype
 * resolution on bare `git archive` trees with no build, no classpath.
 *
 * ARCHITECTURE, mirroring the validated TypeScript sidecar: this program emits
 * FACTS as JSON and makes NO judgements. Every threshold, rubric, suppressor
 * and metric formula stays in Python, where the already-validated
 * `metrics.py::c3_from_token_bags` and `lcom_from_access_sets` are reused
 * verbatim so the three languages stay commensurable.
 *
 * Usage:
 *   java -cp sidecar.jar vb.Facts <source-dir>      # facts JSON on stdout
 *   java -cp sidecar.jar vb.Facts --self-check      # fixture assertions
 */
public class Facts {

    // ---------- emitted shapes (fields are the JSON schema) ----------

    static class MethodF {
        String name, signature, returnType;
        List<String> paramTypes = new ArrayList<>();
        int ccn, loc, lineno, nParams;
        boolean isAbstract, isStatic, isCtor, hasOverrideAnno, isPublic;
        /** "empty" | "throw" | "normal" -- drives ISP stubs and LSP refused bequest. */
        String bodyKind;
        /** thrown type simple name when bodyKind=="throw" (e.g. UnsupportedOperationException) */
        String throwType;
        /** fields of the owning type this method touches -> LCOM4 */
        List<String> props = new ArrayList<>();
        /** sibling methods this method calls -> LCOM4 */
        List<String> calls = new ArrayList<>();
        /** identifier + javadoc token bag -> C3 cohesion */
        List<String> tokens = new ArrayList<>();
        /** LSP: parameter guards that throw (precondition strengthening) */
        int guardThrows;
        /** LSP: does it return a bare null anywhere (postcondition weakening) */
        boolean returnsNull;
        List<DispatchF> chains = new ArrayList<>();
    }

    static class DispatchF {
        String kind;          // switch_tag | value_switch | instanceof | type_eq
        String subject;       // the expression being switched on -- REAL, not a literal
        String subjectType;   // resolved FQN of the subject, or null
        int nBranches, lineno;
        List<String> typeNames = new ArrayList<>();
        boolean closedSet;    // enum / sealed subject -> exhaustive by construction
        String enclosing;     // owning method qualname
    }

    static class TypeF {
        String id;            // FULLY QUALIFIED -- fixes the simple-name collision defect
        String name, file, pkg, kind;   // class|interface|enum|record|annotation
        int lineno, loc, nFields;
        boolean isAbstract, isNested;
        List<String> supertypes = new ArrayList<>();          // resolved FQNs, direct
        List<String> allAncestors = new ArrayList<>();        // resolved FQNs, transitive
        List<String> unresolvedSupertypes = new ArrayList<>();
        List<String> fields = new ArrayList<>();
        List<MethodF> methods = new ArrayList<>();
    }

    /** Shaped to match dip/graph.py::ModuleInfo so the validated Tarjan/Martin maths is reused. */
    static class ModuleF {
        String name;          // package-qualified compilation-unit name
        String file;
        int nClasses, nAbstractClasses;
        boolean parseOk;
        List<String> imports = new ArrayList<>();   // internal targets only
        List<String> abstractImports = new ArrayList<>();
    }

    static class FileF {
        String path;
        boolean parseOk;
        String parseError;
        int loc, maxFuncCcn, maxFuncParams;
    }

    static class Out {
        boolean ok = true;
        String error;
        List<String> sourceRoots = new ArrayList<>();
        List<FileF> files = new ArrayList<>();
        List<TypeF> types = new ArrayList<>();
        List<ModuleF> modules = new ArrayList<>();
        int resolvedSupertypes, unresolvedSupertypes;
    }

    // ---------- source-root inference (the Phase 0b finding) ----------

    /**
     * A JavaParserTypeSolver maps package a.b.C to <root>/a/b/C.java, so it must
     * be rooted at the SOURCE root. Rooting at the repo root fails on every
     * Maven/Gradle layout -- measured: 17% vs 93% supertype resolution on
     * Chronicle-Wire. Infer each root from the files' own package declarations;
     * needs no build file and handles Maven, Gradle, multi-module and flat
     * layouts identically.
     */
    static Set<Path> sourceRoots(List<Path> files, Path root) {
        Set<Path> roots = new LinkedHashSet<>();
        for (Path f : files) {
            String pkg = readPackage(f);
            Path dir = f.getParent();
            if (pkg != null && !pkg.isEmpty()) {
                for (int i = pkg.split("\\.").length; i > 0 && dir != null; i--) dir = dir.getParent();
            }
            if (dir != null && dir.startsWith(root)) roots.add(dir);
        }
        if (roots.isEmpty()) roots.add(root);
        return roots;
    }

    static String readPackage(Path f) {
        try {
            for (String line : Files.readAllLines(f)) {
                String t = line.strip();
                if (t.startsWith("package ") && t.endsWith(";")) return t.substring(8, t.length() - 1).strip();
                if (t.startsWith("import ") || t.startsWith("public ") || t.startsWith("class ")) break;
            }
        } catch (Throwable ignored) { }
        return null;
    }

    // ---------- metric primitives ----------

    /** Cyclomatic complexity: 1 + decision points. Matches lizard's rule set, which
     *  the earlier tree-sitter extractor was validated against at 94% exact. */
    static int ccn(Node body) {
        int n = 1;
        n += body.findAll(IfStmt.class).size();
        n += body.findAll(ForStmt.class).size();
        n += body.findAll(ForEachStmt.class).size();
        n += body.findAll(WhileStmt.class).size();
        n += body.findAll(DoStmt.class).size();
        n += body.findAll(CatchClause.class).size();
        n += body.findAll(ConditionalExpr.class).size();
        for (SwitchEntry e : body.findAll(SwitchEntry.class)) n += Math.max(e.getLabels().size(), 0);
        for (BinaryExpr b : body.findAll(BinaryExpr.class)) {
            if (b.getOperator() == BinaryExpr.Operator.AND || b.getOperator() == BinaryExpr.Operator.OR) n++;
        }
        return n;
    }

    static int loc(Node n) {
        return n.getRange().map(r -> r.end.line - r.begin.line + 1).orElse(0);
    }

    static int line(Node n) {
        return n.getRange().map(r -> r.begin.line).orElse(0);
    }

    /** Split camelCase / snake_case identifiers into a lowercase word bag for C3. */
    static void addTokens(List<String> bag, String ident) {
        for (String part : ident.split("_")) {
            for (String w : part.split("(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")) {
                if (w.length() > 1) bag.add(w.toLowerCase(Locale.ROOT));
            }
        }
    }

    // ---------- dispatch chains (OCP) ----------

    /**
     * Emit dispatch chains with a REAL subject. The tree-sitter version hardcoded
     * the literal string "instanceof" as the subject for every chain, so all
     * chains in a method collapsed to one delta key and 5,130 "chains" had a
     * single branch. A chain here is one subject tested across an if/else-if
     * ladder, or one switch selector.
     */
    static void collectChains(Node body, MethodF mf, String qual) {
        // (1) switch statements and switch expressions
        for (SwitchStmt sw : body.findAll(SwitchStmt.class)) addSwitch(sw.getSelector(), sw.getEntries(), sw, mf, qual);
        for (SwitchExpr sw : body.findAll(SwitchExpr.class)) addSwitch(sw.getSelector(), sw.getEntries(), sw, mf, qual);

        // (2) if/else-if ladders, grouped by SUBJECT
        for (IfStmt ifs : body.findAll(IfStmt.class)) {
            // only start from the head of a ladder
            if (ifs.getParentNode().isPresent() && ifs.getParentNode().get() instanceof IfStmt p
                    && p.getElseStmt().isPresent() && p.getElseStmt().get() == ifs) continue;
            Map<String, List<String>> bySubjectTypes = new LinkedHashMap<>();
            Map<String, String> bySubjectKind = new LinkedHashMap<>();
            IfStmt cur = ifs;
            while (cur != null) {
                probeCondition(cur.getCondition(), bySubjectTypes, bySubjectKind);
                cur = (cur.getElseStmt().isPresent() && cur.getElseStmt().get() instanceof IfStmt nxt) ? nxt : null;
            }
            for (Map.Entry<String, List<String>> e : bySubjectTypes.entrySet()) {
                if (e.getValue().size() < 2) continue;   // not a ladder
                DispatchF d = new DispatchF();
                d.kind = bySubjectKind.get(e.getKey());
                d.subject = e.getKey();
                d.nBranches = e.getValue().size();
                d.typeNames = new ArrayList<>(new LinkedHashSet<>(e.getValue()));
                d.lineno = line(ifs);
                d.enclosing = qual;
                d.subjectType = null;
                d.closedSet = false;
                mf.chains.add(d);
            }
        }
    }

    static void probeCondition(Expression cond, Map<String, List<String>> types, Map<String, String> kinds) {
        for (Expression part : splitOr(cond)) {
            if (part instanceof InstanceOfExpr io) {
                String subj = io.getExpression().toString();
                types.computeIfAbsent(subj, k -> new ArrayList<>()).add(io.getType().asString());
                kinds.putIfAbsent(subj, "instanceof");
            } else if (part instanceof BinaryExpr be
                    && (be.getOperator() == BinaryExpr.Operator.EQUALS
                     || be.getOperator() == BinaryExpr.Operator.NOT_EQUALS)) {
                String l = be.getLeft().toString(), r = be.getRight().toString();
                // subject is the non-constant side
                boolean rConst = isConstantish(be.getRight());
                if (rConst) {
                    types.computeIfAbsent(l, k -> new ArrayList<>()).add(r);
                    kinds.putIfAbsent(l, kindFor(be.getRight()));
                } else if (isConstantish(be.getLeft())) {
                    types.computeIfAbsent(r, k -> new ArrayList<>()).add(l);
                    kinds.putIfAbsent(r, kindFor(be.getLeft()));
                }
            } else if (part instanceof MethodCallExpr mc && mc.getNameAsString().equals("equals")
                    && mc.getArguments().size() == 1 && mc.getScope().isPresent()) {
                Expression scope = mc.getScope().get(), arg = mc.getArgument(0);
                // both "x.equals(CONST)" and "CONST.equals(x)"
                if (isConstantish(arg)) {
                    types.computeIfAbsent(scope.toString(), k -> new ArrayList<>()).add(arg.toString());
                    kinds.putIfAbsent(scope.toString(), "value_switch");
                } else if (isConstantish(scope)) {
                    types.computeIfAbsent(arg.toString(), k -> new ArrayList<>()).add(scope.toString());
                    kinds.putIfAbsent(arg.toString(), "value_switch");
                }
            }
        }
    }

    static List<Expression> splitOr(Expression e) {
        if (e instanceof BinaryExpr b && b.getOperator() == BinaryExpr.Operator.OR) {
            List<Expression> out = new ArrayList<>(splitOr(b.getLeft()));
            out.addAll(splitOr(b.getRight()));
            return out;
        }
        if (e instanceof EnclosedExpr en) return splitOr(en.getInner());
        return List.of(e);
    }

    static boolean isConstantish(Expression e) {
        return e instanceof StringLiteralExpr || e instanceof IntegerLiteralExpr
                || e instanceof CharLiteralExpr || e instanceof NameExpr
                || (e instanceof FieldAccessExpr) || (e instanceof ClassExpr);
    }

    static String kindFor(Expression constant) {
        return (constant instanceof ClassExpr) ? "type_eq" : "value_switch";
    }

    static void addSwitch(Expression selector, List<SwitchEntry> entries, Node node, MethodF mf, String qual) {
        DispatchF d = new DispatchF();
        d.subject = selector.toString();
        d.enclosing = qual;
        d.lineno = line(node);
        int n = 0;
        for (SwitchEntry e : entries) {
            n += e.getLabels().size();          // `default` has zero labels -> not counted
            for (Expression lab : e.getLabels()) d.typeNames.add(lab.toString());
        }
        d.nBranches = n;
        // Resolve the SELECTOR's type instead of guessing from label casing.
        // The tree-sitter version called a switch "enum" when labels were
        // uppercase, which also matches `case MAX_VALUE:` (a static final int)
        // -- it suppressed 2,705 of 24,836 chains on that guess.
        String st = null;
        boolean isEnum = false;
        try {
            var rt = selector.calculateResolvedType();
            if (rt.isReferenceType()) {
                st = rt.asReferenceType().getQualifiedName();
                var decl = rt.asReferenceType().getTypeDeclaration();
                if (decl.isPresent()) isEnum = decl.get().isEnum();
            } else {
                st = rt.describe();
            }
        } catch (Throwable ignored) { }
        d.subjectType = st;
        d.closedSet = isEnum;                    // enum switch: exhaustive by construction
        d.kind = isEnum ? "switch_tag" : "value_switch";
        mf.chains.add(d);
    }

    // ---------- method facts ----------

    static MethodF methodFacts(CallableDeclaration<?> cd, Set<String> fieldNames,
                               Set<String> siblingMethods, String ownerQual) {
        MethodF m = new MethodF();
        m.name = cd.getNameAsString();
        m.lineno = line(cd);
        m.loc = loc(cd);
        m.isCtor = cd instanceof ConstructorDeclaration;
        m.isStatic = cd.getModifiers().stream().anyMatch(x -> x.getKeyword().asString().equals("static"));
        m.isPublic = cd.getModifiers().stream().anyMatch(x -> x.getKeyword().asString().equals("public"));
        m.hasOverrideAnno = cd.getAnnotationByName("Override").isPresent();
        m.nParams = cd.getParameters().size();
        for (Parameter p : cd.getParameters()) m.paramTypes.add(p.getType().asString());
        m.signature = m.name + "(" + String.join(",", m.paramTypes) + ")";

        Optional<BlockStmt> body;
        if (cd instanceof MethodDeclaration md) {
            m.returnType = md.getType().asString();
            m.isAbstract = md.getBody().isEmpty();
            body = md.getBody();
        } else {
            m.returnType = "void";
            body = Optional.of(((ConstructorDeclaration) cd).getBody());
        }

        addTokens(m.tokens, m.name);
        for (Parameter p : cd.getParameters()) addTokens(m.tokens, p.getNameAsString());
        cd.getJavadocComment().ifPresent(j -> {
            for (String w : j.getContent().split("[^A-Za-z0-9_]+")) if (w.length() > 2) addTokens(m.tokens, w);
        });

        if (body.isEmpty()) { m.bodyKind = "empty"; m.ccn = 1; return m; }
        BlockStmt b = body.get();
        m.ccn = ccn(b);

        // body kind -- drives ISP forced-stub and LSP refused-bequest
        List<Statement> stmts = b.getStatements();
        if (stmts.isEmpty()) {
            m.bodyKind = "empty";
        } else if (stmts.get(stmts.size() - 1) instanceof ThrowStmt th
                && stmts.stream().noneMatch(s -> s instanceof IfStmt || s instanceof ForStmt
                        || s instanceof WhileStmt || s instanceof TryStmt)) {
            // LIBERAL refusal check, same as the Python detector: a leading log or
            // `del` before an unconditional throw still counts as refusal.
            m.bodyKind = "throw";
            m.throwType = th.getExpression() instanceof ObjectCreationExpr oc
                    ? oc.getType().getNameAsString() : th.getExpression().toString();
        } else {
            m.bodyKind = "normal";
        }

        // LSP precondition strengthening: `if (<param ...>) throw ...` at any depth
        Set<String> paramNames = cd.getParameters().stream()
                .map(Parameter::getNameAsString).collect(Collectors.toSet());
        for (IfStmt ifs : b.findAll(IfStmt.class)) {
            boolean throws_ = ifs.getThenStmt().findAll(ThrowStmt.class).size() > 0;
            if (!throws_) continue;
            boolean onParam = ifs.getCondition().findAll(NameExpr.class).stream()
                    .anyMatch(ne -> paramNames.contains(ne.getNameAsString()));
            if (onParam) m.guardThrows++;
        }
        for (ReturnStmt rs : b.findAll(ReturnStmt.class)) {
            if (rs.getExpression().isPresent() && rs.getExpression().get() instanceof NullLiteralExpr) {
                m.returnsNull = true;
            }
        }

        // LCOM4 access sets
        Set<String> props = new LinkedHashSet<>(), calls = new LinkedHashSet<>();
        for (NameExpr ne : b.findAll(NameExpr.class)) {
            if (fieldNames.contains(ne.getNameAsString())) props.add(ne.getNameAsString());
            addTokens(m.tokens, ne.getNameAsString());
        }
        for (FieldAccessExpr fa : b.findAll(FieldAccessExpr.class)) {
            if (fa.getScope() instanceof ThisExpr && fieldNames.contains(fa.getNameAsString())) {
                props.add(fa.getNameAsString());
            }
        }
        for (MethodCallExpr mc : b.findAll(MethodCallExpr.class)) {
            boolean own = mc.getScope().isEmpty() || mc.getScope().get() instanceof ThisExpr;
            if (own && siblingMethods.contains(mc.getNameAsString())) calls.add(mc.getNameAsString());
            addTokens(m.tokens, mc.getNameAsString());
        }
        m.props = new ArrayList<>(props);
        m.calls = new ArrayList<>(calls);

        collectChains(b, m, ownerQual + "." + m.name);
        return m;
    }

    // ---------- main ----------

    public static void main(String[] args) throws Exception {
        Gson gson = new GsonBuilder().serializeNulls().create();
        if (args.length > 0 && args[0].equals("--self-check")) { SelfCheck.run(); return; }
        Out out = new Out();
        try {
            run(Paths.get(args[0]), out);
        } catch (Throwable e) {
            out.ok = false;
            out.error = e.getClass().getSimpleName() + ": " + e.getMessage();
        }
        System.out.println(gson.toJson(out));
    }

    static void run(Path root, Out out) throws Exception {
        List<Path> files;
        try (var s = Files.walk(root)) {
            files = s.filter(p -> p.toString().endsWith(".java")).sorted().toList();
        }
        Set<Path> roots = sourceRoots(files, root);
        for (Path r : roots) out.sourceRoots.add(root.relativize(r).toString());

        CombinedTypeSolver solver = new CombinedTypeSolver();
        solver.add(new ReflectionTypeSolver(false));
        for (Path r : roots) solver.add(new JavaParserTypeSolver(r));
        ParserConfiguration cfg = new ParserConfiguration()
                .setSymbolResolver(new JavaSymbolSolver(solver))
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17);
        JavaParser parser = new JavaParser(cfg);

        // internal package prefixes -- used to keep DIP edges internal-only
        Set<String> internalPkgs = new HashSet<>();
        for (Path f : files) { String p = readPackage(f); if (p != null) internalPkgs.add(p); }

        for (Path f : files) {
            String rel = root.relativize(f).toString().replace('\\', '/');
            FileF ff = new FileF();
            ff.path = rel;
            CompilationUnit cu = null;
            try {
                var res = parser.parse(f);
                if (res.getResult().isPresent() && res.isSuccessful()) {
                    cu = res.getResult().get();
                    ff.parseOk = true;
                } else {
                    ff.parseOk = false;
                    ff.parseError = res.getProblems().isEmpty() ? "unknown"
                            : res.getProblems().get(0).getMessage();
                }
            } catch (Throwable e) {
                ff.parseOk = false;
                ff.parseError = e.getClass().getSimpleName() + ": " + e.getMessage();
            }
            if (cu == null) { out.files.add(ff); continue; }
            ff.loc = loc(cu);

            String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
            ModuleF mod = new ModuleF();
            mod.name = pkg.isEmpty() ? rel.replace(".java", "").replace('/', '.')
                                     : pkg + "." + f.getFileName().toString().replace(".java", "");
            mod.file = rel;
            mod.parseOk = true;
            for (ImportDeclaration imp : cu.getImports()) {
                String nm = imp.getNameAsString();
                String targetPkg = imp.isAsterisk() ? nm : (nm.contains(".") ? nm.substring(0, nm.lastIndexOf('.')) : nm);
                if (internalPkgs.contains(targetPkg)) mod.imports.add(nm);
            }

            // EVERY type declaration, including nested and inner -- the old
            // extractor folded nested classes into the outer type and dropped
            // annotation types entirely.
            for (TypeDeclaration<?> td : cu.findAll(TypeDeclaration.class)) {
                TypeF t = typeFacts(td, rel, pkg, out);
                if (t == null) continue;
                out.types.add(t);
                mod.nClasses++;
                if (t.isAbstract || t.kind.equals("interface")) {
                    mod.nAbstractClasses++;
                    for (String s : t.supertypes) if (internalOf(s, internalPkgs)) mod.abstractImports.add(s);
                }
                for (String s : t.supertypes) if (internalOf(s, internalPkgs) && !mod.imports.contains(s)) mod.imports.add(s);
                ff.maxFuncCcn = Math.max(ff.maxFuncCcn, t.methods.stream().mapToInt(m -> m.ccn).max().orElse(0));
                ff.maxFuncParams = Math.max(ff.maxFuncParams, t.methods.stream().mapToInt(m -> m.nParams).max().orElse(0));
            }
            out.modules.add(mod);
            out.files.add(ff);
        }
    }

    static boolean internalOf(String fqn, Set<String> internalPkgs) {
        int i = fqn.lastIndexOf('.');
        return i > 0 && internalPkgs.contains(fqn.substring(0, i));
    }

    static TypeF typeFacts(TypeDeclaration<?> td, String rel, String pkg, Out out) {
        TypeF t = new TypeF();
        t.name = td.getNameAsString();
        t.id = td.getFullyQualifiedName().orElse(pkg.isEmpty() ? t.name : pkg + "." + t.name);
        t.file = rel;
        t.pkg = pkg;
        t.lineno = line(td);
        t.loc = loc(td);
        t.isNested = td.isNestedType();
        t.isAbstract = td.getModifiers().stream().anyMatch(m -> m.getKeyword().asString().equals("abstract"));

        List<ClassOrInterfaceType> sup = new ArrayList<>();
        if (td instanceof ClassOrInterfaceDeclaration cid) {
            t.kind = cid.isInterface() ? "interface" : "class";
            sup.addAll(cid.getExtendedTypes());
            sup.addAll(cid.getImplementedTypes());
            if (cid.isInterface()) t.isAbstract = true;
        } else if (td instanceof EnumDeclaration ed) {
            t.kind = "enum";
            sup.addAll(ed.getImplementedTypes());
        } else if (td instanceof RecordDeclaration rd) {
            t.kind = "record";
            sup.addAll(rd.getImplementedTypes());
        } else if (td instanceof AnnotationDeclaration) {
            t.kind = "annotation";
            t.isAbstract = true;
        } else {
            t.kind = "class";
        }

        for (ClassOrInterfaceType st : sup) {
            try {
                t.supertypes.add(st.resolve().asReferenceType().getQualifiedName());
                out.resolvedSupertypes++;
            } catch (Throwable e) {
                t.unresolvedSupertypes.add(st.getNameAsString());
                out.unresolvedSupertypes++;
            }
        }
        try {
            if (td instanceof ClassOrInterfaceDeclaration cid) {
                for (var anc : cid.resolve().getAllAncestors()) {
                    try { t.allAncestors.add(anc.getQualifiedName()); } catch (Throwable ignored) { }
                }
            }
        } catch (Throwable ignored) { }

        Set<String> fieldNames = new LinkedHashSet<>();
        for (FieldDeclaration fd : td.getFields()) {
            for (VariableDeclarator v : fd.getVariables()) fieldNames.add(v.getNameAsString());
        }
        t.fields = new ArrayList<>(fieldNames);
        t.nFields = fieldNames.size();

        Set<String> siblings = td.getMethods().stream()
                .map(MethodDeclaration::getNameAsString).collect(Collectors.toCollection(LinkedHashSet::new));
        for (MethodDeclaration md : td.getMethods()) {
            // skip methods belonging to a NESTED type -- that type gets its own row
            if (md.findAncestor(TypeDeclaration.class).map(a -> a != td).orElse(false)) continue;
            t.methods.add(methodFacts(md, fieldNames, siblings, t.id));
        }
        for (ConstructorDeclaration cd : td.getConstructors()) {
            if (cd.findAncestor(TypeDeclaration.class).map(a -> a != td).orElse(false)) continue;
            t.methods.add(methodFacts(cd, fieldNames, siblings, t.id));
        }
        return t;
    }
}

package vb;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.*;

import java.nio.file.*;
import java.util.*;

/**
 * PHASE 0 PILOT: can JavaParser + SymbolSolver resolve types on a BARE source
 * tree -- no compilation, no classpath, no dependencies downloaded?
 *
 * This is the capability tree-sitter cannot provide and that LSP/ISP/DIP need:
 * without it, two classes both named `Node` in different packages collapse into
 * one, silently corrupting every hierarchy.
 *
 * Usage: java -jar pilot.jar <source-root>
 */
public class Pilot {

    /**
     * Infer source roots WITHOUT any build file: for each .java file, read its
     * `package a.b.c;` declaration and strip `a/b/c` off the end of its
     * directory. What remains is a source root. Files with no package
     * declaration sit directly in a root. This handles Maven, Gradle, multi-
     * module monorepos and flat layouts identically, which is what we need on
     * bare `git archive` trees.
     */
    static Set<Path> sourceRoots(Path root) throws Exception {
        Set<Path> roots = new LinkedHashSet<>();
        try (var s = Files.walk(root)) {
            for (Path f : s.filter(p -> p.toString().endsWith(".java")).toList()) {
                String pkg = null;
                try {
                    for (String line : Files.readAllLines(f)) {
                        String t = line.strip();
                        if (t.startsWith("package ") && t.endsWith(";")) {
                            pkg = t.substring(8, t.length() - 1).strip();
                            break;
                        }
                        if (t.startsWith("import ") || t.startsWith("class ")) break;
                    }
                } catch (Throwable ignored) { }
                Path dir = f.getParent();
                if (pkg != null && !pkg.isEmpty()) {
                    for (int i = pkg.split("\\.").length; i > 0 && dir != null; i--) {
                        dir = dir.getParent();
                    }
                }
                if (dir != null && dir.startsWith(root)) roots.add(dir);
            }
        }
        if (roots.isEmpty()) roots.add(root);
        return roots;
    }

    public static void main(String[] args) throws Exception {
        Path root = Paths.get(args[0]);

        CombinedTypeSolver solver = new CombinedTypeSolver();
        solver.add(new ReflectionTypeSolver(false));   // JDK types
        // A JavaParserTypeSolver maps package a.b.C -> <root>/a/b/C.java, so it
        // must be rooted at the SOURCE root, not the repo root. Maven/Gradle
        // repos bury that under src/main/java, and multi-module repos have many.
        // Infer every root from the files' own package declarations.
        Set<Path> roots = sourceRoots(root);
        for (Path r : roots) solver.add(new JavaParserTypeSolver(r));
        System.out.printf("  source roots        : %d  %s%n", roots.size(),
                roots.size() <= 6 ? roots.stream().map(p -> root.relativize(p).toString())
                        .map(s -> s.isEmpty() ? "." : s).toList() : "(many)");

        ParserConfiguration cfg = new ParserConfiguration()
                .setSymbolResolver(new JavaSymbolSolver(solver))
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17);
        JavaParser parser = new JavaParser(cfg);

        List<Path> files;
        try (var s = Files.walk(root)) {
            files = s.filter(p -> p.toString().endsWith(".java"))
                     .filter(p -> !p.toString().contains("target"))
                     .limit(400).toList();
        }

        int parsed = 0, failed = 0, types = 0;
        int extResolved = 0, extUnresolved = 0, ovResolved = 0, ovUnresolved = 0;
        for (Path f : files) {
            try {
                var res = parser.parse(f);
                if (res.getResult().isEmpty()) { failed++; continue; }
                CompilationUnit cu = res.getResult().get();
                parsed++;
                for (ClassOrInterfaceDeclaration cls
                        : cu.findAll(ClassOrInterfaceDeclaration.class)) {
                    types++;
                    // (a) can we resolve what this class EXTENDS/IMPLEMENTS?
                    List<ClassOrInterfaceType> sup = new ArrayList<>();
                    sup.addAll(cls.getExtendedTypes());
                    sup.addAll(cls.getImplementedTypes());
                    for (ClassOrInterfaceType t : sup) {
                        try { t.resolve().asReferenceType().getQualifiedName(); extResolved++; }
                        catch (Throwable e) { extUnresolved++; }
                    }
                    // (b) can we resolve the ANCESTOR chain (what LSP needs)?
                    for (MethodDeclaration m : cls.getMethods()) {
                        if (!m.getAnnotationByName("Override").isPresent()) continue;
                        try {
                            var anc = cls.resolve().getAllAncestors();
                            if (anc.isEmpty()) ovUnresolved++; else ovResolved++;
                        } catch (Throwable e) { ovUnresolved++; }
                    }
                }
            } catch (Throwable e) { failed++; }
        }
        System.out.printf("  files parsed        : %d  (failed %d)%n", parsed, failed);
        System.out.printf("  types found         : %d%n", types);
        int et = extResolved + extUnresolved;
        System.out.printf("  extends/implements  : %d resolved, %d unresolved  (%.1f%% resolved)%n",
                extResolved, extUnresolved, et == 0 ? 0.0 : 100.0 * extResolved / et);
        int ot = ovResolved + ovUnresolved;
        System.out.printf("  @Override ancestors : %d resolved, %d unresolved  (%.1f%% resolved)%n",
                ovResolved, ovUnresolved, ot == 0 ? 0.0 : 100.0 * ovResolved / ot);
    }
}

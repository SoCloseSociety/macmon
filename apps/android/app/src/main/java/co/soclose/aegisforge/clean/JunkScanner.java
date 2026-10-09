package co.soclose.aegisforge.clean;

import java.io.File;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Walks shared storage and classifies junk with {@link JunkRules}. Bounded
 * (depth + a visit cap) and loop-safe (skips symlinked dirs by canonical path).
 * Never descends into Android/ (other apps' sandboxes). IO is isolated here so
 * the rules stay unit-testable.
 */
public final class JunkScanner {
    private JunkScanner() {}

    private static final int MAX_DEPTH = 14;
    private static final int MAX_VISITS = 200_000;

    public interface Progress { void onDir(String name); boolean cancelled(); }

    public static ScanResult scan(File root, Progress progress) {
        ScanResult result = new ScanResult();
        if (root == null || !root.isDirectory()) return result;

        String rootPath;
        try { rootPath = root.getCanonicalPath(); }
        catch (IOException e) { rootPath = root.getAbsolutePath(); }

        Deque<File> stack = new ArrayDeque<>();
        Deque<Integer> depths = new ArrayDeque<>();
        stack.push(root); depths.push(0);
        int visits = 0;

        while (!stack.isEmpty()) {
            if (progress != null && progress.cancelled()) break;
            File dir = stack.pop();
            int depth = depths.pop();
            if (depth > MAX_DEPTH || ++visits > MAX_VISITS) continue;

            File[] kids = dir.listFiles();
            if (kids == null) continue;
            if (progress != null) progress.onDir(dir.getName());

            for (File f : kids) {
                String rel = relativize(rootPath, f);
                if (rel == null) continue;
                boolean isDir = f.isDirectory();

                // never descend into Android/ (other apps' data, OS-restricted)
                if (isDir && "android".equals(f.getName().toLowerCase(java.util.Locale.US))
                        && depth == 0) {
                    continue;
                }
                if (isDir && isSymlink(f)) continue;  // loop / escape guard

                boolean empty = isDir && isEmpty(f);
                JunkCategory cat = JunkRules.classify(rel, isDir, empty);
                if (cat != null) {
                    result.add(cat, f.getAbsolutePath(), isDir ? 0L : f.length());
                }
                if (isDir && !empty) { stack.push(f); depths.push(depth + 1); }
            }
        }
        return result;
    }

    private static boolean isEmpty(File dir) {
        String[] names = dir.list();
        return names == null || names.length == 0;
    }

    private static boolean isSymlink(File f) {
        try { return !f.getCanonicalPath().equals(f.getAbsolutePath()); }
        catch (IOException e) { return true; }  // if we cannot tell, do not follow
    }

    private static String relativize(String rootPath, File f) {
        String abs = f.getAbsolutePath();
        if (abs.startsWith(rootPath)) {
            String rel = abs.substring(rootPath.length());
            while (rel.startsWith("/")) rel = rel.substring(1);
            return rel;
        }
        return f.getName();
    }
}

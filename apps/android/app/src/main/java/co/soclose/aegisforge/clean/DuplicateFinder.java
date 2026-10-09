package co.soclose.aegisforge.clean;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Finds duplicate files in shared storage: group by exact size first (cheap),
 * then hash only the size-colliding ones (SHA-256) to confirm identical content.
 * java.io only, so the core is unit-tested off-device. Skips Android/ and tiny
 * files. Never follows symlinks.
 */
public final class DuplicateFinder {
    private DuplicateFinder() {}

    private static final int MAX_DEPTH = 14;
    private static final int MAX_VISITS = 200_000;
    private static final long MIN_SIZE = 16 * 1024;   // ignore tiny files (noise)

    public static final class Group {
        public final long size;
        public final List<String> paths = new ArrayList<>();
        Group(long size) { this.size = size; }
        /** Bytes reclaimable if all but one copy are removed. */
        public long reclaimable() { return (long) (paths.size() - 1) * size; }
    }

    public interface Progress { void onFile(String name); boolean cancelled(); }

    public static List<Group> find(File root, Progress progress) {
        List<Group> groups = new ArrayList<>();
        if (root == null || !root.isDirectory()) return groups;

        // 1. bucket candidate files by size
        Map<Long, List<File>> bySize = new HashMap<>();
        Deque<File> stack = new ArrayDeque<>();
        Deque<Integer> depths = new ArrayDeque<>();
        stack.push(root); depths.push(0);
        int visits = 0;
        while (!stack.isEmpty()) {
            if (progress != null && progress.cancelled()) return groups;
            File dir = stack.pop();
            int depth = depths.pop();
            if (depth > MAX_DEPTH || ++visits > MAX_VISITS) continue;
            File[] kids = dir.listFiles();
            if (kids == null) continue;
            for (File f : kids) {
                if (f.isDirectory()) {
                    if (depth == 0 && "android".equals(f.getName().toLowerCase(Locale.US))) continue;
                    if (isSymlink(f)) continue;
                    stack.push(f); depths.push(depth + 1);
                } else if (f.length() >= MIN_SIZE) {
                    bySize.computeIfAbsent(f.length(), k -> new ArrayList<>()).add(f);
                }
            }
        }

        // 2. within each size bucket of >1, confirm by content hash
        for (Map.Entry<Long, List<File>> e : bySize.entrySet()) {
            List<File> candidates = e.getValue();
            if (candidates.size() < 2) continue;
            Map<String, Group> byHash = new HashMap<>();
            for (File f : candidates) {
                if (progress != null) {
                    if (progress.cancelled()) return groups;
                    progress.onFile(f.getName());
                }
                String h = sha256(f);
                if (h == null) continue;
                Group g = byHash.get(h);
                if (g == null) { g = new Group(e.getKey()); byHash.put(h, g); }
                g.paths.add(f.getAbsolutePath());
            }
            for (Group g : byHash.values()) if (g.paths.size() > 1) groups.add(g);
        }
        // biggest reclaimable first
        Collections.sort(groups, (a, b) -> Long.compare(b.reclaimable(), a.reclaimable()));
        return groups;
    }

    public static String sha256(File f) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] buf = new byte[1 << 16];
            try (InputStream in = new FileInputStream(f)) {
                int n;
                while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            }
            byte[] d = md.digest();
            StringBuilder sb = new StringBuilder(d.length * 2);
            for (byte b : d) sb.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
            return sb.toString();
        } catch (NoSuchAlgorithmException | IOException e) {
            return null;
        }
    }

    private static boolean isSymlink(File f) {
        try { return !f.getCanonicalPath().equals(f.getAbsolutePath()); }
        catch (IOException e) { return true; }
    }
}

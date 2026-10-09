package co.soclose.aegisforge.clean;

import java.io.File;
import java.io.IOException;
import java.util.List;
import java.util.Locale;

/**
 * Deletes the junk a scan proposed, re-validating every path first (defence in
 * depth): it must sit under the scanned root, must not be in another app's
 * sandbox, and a directory is removed only if still empty. Returns the bytes
 * actually freed, so the UI never reports a clean that did not happen.
 */
public final class Cleaner {
    private Cleaner() {}

    public static final class Outcome {
        public long freedBytes;
        public int deleted;
        public int skipped;
    }

    public static Outcome deleteAll(File root, List<String> paths) {
        Outcome out = new Outcome();
        String rootPath;
        try { rootPath = root.getCanonicalPath(); }
        catch (IOException e) { rootPath = root.getAbsolutePath(); }

        for (String path : paths) {
            File f = new File(path);
            if (!underRoot(rootPath, f) || inOtherAppSandbox(f) || !f.exists()) { out.skipped++; continue; }
            if (f.isDirectory()) {
                String[] kids = f.list();
                if (kids != null && kids.length > 0) { out.skipped++; continue; }  // no longer empty
                if (f.delete()) out.deleted++; else out.skipped++;
            } else {
                long len = f.length();
                if (f.delete()) { out.deleted++; out.freedBytes += len; } else out.skipped++;
            }
        }
        return out;
    }

    private static boolean underRoot(String rootPath, File f) {
        try { return f.getCanonicalPath().startsWith(rootPath); }
        catch (IOException e) { return false; }
    }

    private static boolean inOtherAppSandbox(File f) {
        String p = f.getAbsolutePath().toLowerCase(Locale.US).replace('\\', '/');
        return p.contains("/android/data/") || p.contains("/android/obb/");
    }
}

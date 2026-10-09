package co.soclose.aegisforge.clean;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Pure classification: given a path relative to the shared-storage root, decide
 * which junk category it is, or null to keep it. No Android, no IO -- unit-tested
 * off-device. Conservative by design: anything not clearly disposable is kept,
 * and whole families of paths are refused outright.
 */
public final class JunkRules {
    private JunkRules() {}

    // Other apps' sandboxes (OS-restricted anyway) and the app's own tree: never.
    private static final String[] REFUSED_PREFIXES = {"android/"};

    // Standard media/user folders: never proposed as an "empty folder" to delete.
    private static final Set<String> PROTECTED_TOP = new HashSet<>(Arrays.asList(
            "dcim", "pictures", "camera", "download", "downloads", "documents",
            "movies", "music", "podcasts", "ringtones", "alarms", "notifications",
            "audiobooks", "recordings", "screenshots", "android"));

    private static final Set<String> TEMP_EXT = new HashSet<>(Arrays.asList(
            "tmp", "temp", "log", "old", "bak", "part", "crdownload"));

    /**
     * @param relPath   path relative to shared-storage root, '/'-separated
     * @param isDir     true if it is a directory
     * @param isEmpty   true if it is an empty directory (meaningful only for dirs)
     * @return the junk category, or null to keep the entry
     */
    public static JunkCategory classify(String relPath, boolean isDir, boolean isEmpty) {
        if (relPath == null) return null;
        String p = relPath.replace('\\', '/');
        while (p.startsWith("/")) p = p.substring(1);
        if (p.isEmpty()) return null;
        String lower = p.toLowerCase(Locale.US);

        for (String pre : REFUSED_PREFIXES) {
            if (lower.equals(pre.substring(0, pre.length() - 1)) || lower.startsWith(pre)) return null;
        }

        String base = p.substring(p.lastIndexOf('/') + 1);
        String baseLower = base.toLowerCase(Locale.US);

        // thumbnail caches (a .thumbnails dir, or any file inside one): regenerable
        if (segmentIs(lower, ".thumbnails")) return JunkCategory.THUMBNAILS;

        if (!isDir) {
            if (baseLower.startsWith("tombstone") || baseLower.endsWith(".crash") || baseLower.endsWith(".dmp")) {
                return JunkCategory.CRASH_LOG;
            }
            String ext = ext(baseLower);
            if ("apk".equals(ext)) return JunkCategory.OLD_APK;
            if (TEMP_EXT.contains(ext) || baseLower.endsWith("~")) return JunkCategory.TEMP_LOG;
            return null;
        }

        // directories: only an empty one that is not a standard top-level folder
        if (isDir && isEmpty) {
            int slash = p.indexOf('/');
            String top = (slash < 0 ? p : p.substring(0, slash)).toLowerCase(Locale.US);
            if (PROTECTED_TOP.contains(top) && slash < 0) return null;  // the top folder itself
            return JunkCategory.EMPTY_DIR;
        }
        return null;
    }

    private static boolean segmentIs(String lowerPath, String seg) {
        return lowerPath.equals(seg) || lowerPath.startsWith(seg + "/")
                || lowerPath.contains("/" + seg + "/") || lowerPath.endsWith("/" + seg);
    }

    private static String ext(String name) {
        int dot = name.lastIndexOf('.');
        return dot < 0 || dot == name.length() - 1 ? "" : name.substring(dot + 1);
    }
}

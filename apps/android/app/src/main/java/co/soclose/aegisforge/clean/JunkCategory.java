package co.soclose.aegisforge.clean;

/**
 * The kinds of cleanable junk AegisForge recognises in shared storage. Each is
 * regenerable or disposable by nature; nothing here touches another app's data
 * (Android/data and Android/obb are OS-restricted and skipped outright).
 */
public enum JunkCategory {
    TEMP_LOG("Temp & logs"),
    THUMBNAILS("Thumbnail caches"),
    OLD_APK("Leftover APKs"),
    EMPTY_DIR("Empty folders"),
    CRASH_LOG("Crash & tombstone logs");

    public final String label;

    JunkCategory(String label) {
        this.label = label;
    }
}

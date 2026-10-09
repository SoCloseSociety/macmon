package co.soclose.aegisforge;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * The scoring, the verdict and the human strings. Pure Java (no Android), the
 * mobile analog of macmon's {@code health} command: each dimension gets a
 * level, the levels fold into a 0..100 score and an overall verdict, and the
 * worst dimension names itself. An unknown dimension is skipped, never scored
 * as a failure.
 */
public final class Health {
    private Health() {}

    public enum Level { OK, WARN, BAD }
    public enum Verdict { OK, WATCH, RISK }

    // Thresholds. Battery leans on charging state; the rest on free ratios.
    static final int BATT_WARN = 30, BATT_BAD = 15;
    static final double STORE_WARN = 0.15, STORE_BAD = 0.05;
    static final double MEM_WARN = 0.15;
    static final int PENALTY_WARN = 12, PENALTY_BAD = 30;

    /** A single dimension of the readout: a label, its value string, its level. */
    public static final class Dim {
        public final String label, value;
        public final Level level;
        Dim(String label, String value, Level level) { this.label = label; this.value = value; this.level = level; }
    }

    public static Level battery(Snapshot s) {
        if (s.batteryPct < 0) return Level.OK;            // unknown -> do not penalise
        if (s.charging) return s.batteryPct < BATT_BAD ? Level.WARN : Level.OK;
        if (s.batteryPct < BATT_BAD) return Level.BAD;
        if (s.batteryPct < BATT_WARN) return Level.WARN;
        return Level.OK;
    }

    public static Level storage(Snapshot s) {
        double r = s.storageFreeRatio();
        if (r < 0) return Level.OK;
        if (r < STORE_BAD) return Level.BAD;
        if (r < STORE_WARN) return Level.WARN;
        return Level.OK;
    }

    public static Level memory(Snapshot s) {
        if (s.lowMemory) return Level.BAD;
        double r = s.memAvailRatio();
        if (r < 0) return Level.OK;
        return r < MEM_WARN ? Level.WARN : Level.OK;
    }

    public static Level network(Snapshot s) {
        return "Offline".equals(s.netType) ? Level.WARN : Level.OK;
    }

    public static Level lock(Snapshot s) {
        if (s.deviceSecure == null) return Level.OK;      // unknown -> skip
        return s.deviceSecure ? Level.OK : Level.WARN;
    }

    /** The dimensions in display order (battery, storage, memory, network). */
    public static List<Dim> dimensions(Snapshot s) {
        List<Dim> out = new ArrayList<>();
        out.add(new Dim("BATTERY", s.batteryPct < 0 ? "n/a"
                : (s.batteryPct + "%" + (s.charging ? " +" : "")), battery(s)));
        out.add(new Dim("STORAGE", s.storageFreeRatio() < 0 ? "n/a"
                : humanBytes(s.storageFreeBytes) + " free", storage(s)));
        out.add(new Dim("MEMORY", s.memAvailRatio() < 0 ? "n/a"
                : pct(s.memAvailRatio()) + " free", memory(s)));
        out.add(new Dim("NETWORK", s.netType, network(s)));
        return out;
    }

    /** All the dimensions that carry a level, including the screen-lock posture. */
    static List<Level> allLevels(Snapshot s) {
        List<Level> ls = new ArrayList<>();
        ls.add(battery(s)); ls.add(storage(s)); ls.add(memory(s));
        ls.add(network(s)); ls.add(lock(s));
        return ls;
    }

    public static int score(Snapshot s) {
        int score = 100;
        for (Level l : allLevels(s)) {
            if (l == Level.WARN) score -= PENALTY_WARN;
            else if (l == Level.BAD) score -= PENALTY_BAD;
        }
        return Math.max(0, Math.min(100, score));
    }

    public static Verdict verdict(Snapshot s) {
        boolean anyBad = false;
        for (Level l : allLevels(s)) if (l == Level.BAD) { anyBad = true; break; }
        int sc = score(s);
        if (anyBad || sc < 50) return sc < 50 ? Verdict.RISK : Verdict.WATCH;
        return sc < 80 ? Verdict.WATCH : Verdict.OK;
    }

    /** A short sentence for the sub-line: the worst dimension, or "all clear". */
    public static String subLabel(Snapshot s) {
        // battery
        if (battery(s) == Level.BAD) return "battery critical -- " + s.batteryPct + "%";
        if (storage(s) == Level.BAD) return "storage almost full";
        if (memory(s) == Level.BAD) return "memory under pressure";
        if (battery(s) == Level.WARN) return "battery low -- " + s.batteryPct + "%";
        if (storage(s) == Level.WARN) return "storage running low";
        if (lock(s) == Level.WARN) return "no screen lock set";
        if (memory(s) == Level.WARN) return "memory tight";
        if (network(s) == Level.WARN) return "offline";
        return "all clear";
    }

    public static String verdictText(Verdict v) {
        switch (v) {
            case OK: return "OK";
            case WATCH: return "WATCH";
            default: return "RISK";
        }
    }

    // ---- formatting ----

    public static String humanBytes(long bytes) {
        if (bytes < 0) return "n/a";
        double gb = bytes / (1024.0 * 1024.0 * 1024.0);
        if (gb >= 10) return String.format(Locale.US, "%.0f GB", gb);
        if (gb >= 1) return String.format(Locale.US, "%.1f GB", gb);
        double mb = bytes / (1024.0 * 1024.0);
        if (mb >= 1) return String.format(Locale.US, "%.0f MB", mb);
        double kb = bytes / 1024.0;
        if (kb >= 1) return String.format(Locale.US, "%.0f KB", kb);
        return bytes + " B";
    }

    public static String pct(double ratio) {
        if (ratio < 0) return "n/a";
        return Math.round(ratio * 100.0) + "%";
    }
}

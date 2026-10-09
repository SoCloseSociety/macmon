package co.soclose.aegisforge;

import java.util.ArrayList;
import java.util.List;

/**
 * The unified Health Check: one scan folded into CCleaner-style categories
 * (Space / Speed / Security), each with a level and a one-line verdict. Pure
 * logic (reuses Health + Snapshot), so it is unit-tested off-device. Honest:
 * only categories Android actually lets an app assess, never a fake "privacy
 * scan" it cannot perform.
 */
public final class HealthCheck {
    private HealthCheck() {}

    public enum Cat { SPACE, SPEED, SECURITY }

    public static final class Result {
        public final Cat cat;
        public final String title, detail;
        public final Health.Level level;
        Result(Cat cat, String title, String detail, Health.Level level) {
            this.cat = cat; this.title = title; this.detail = detail; this.level = level;
        }
    }

    static final long JUNK_WARN = 20L * 1024 * 1024;
    static final long JUNK_BAD = 200L * 1024 * 1024;

    public static Health.Level spaceLevel(long junkBytes) {
        if (junkBytes >= JUNK_BAD) return Health.Level.BAD;
        if (junkBytes >= JUNK_WARN) return Health.Level.WARN;
        return Health.Level.OK;
    }

    /** Speed = the worst of the device-pressure dimensions. */
    public static Health.Level speedLevel(Snapshot s) {
        return worst(Health.memory(s), worst(Health.storage(s), Health.battery(s)));
    }

    public static List<Result> evaluate(Snapshot s, long junkBytes, int junkCount) {
        List<Result> out = new ArrayList<>();

        Health.Level space = spaceLevel(junkBytes);
        out.add(new Result(Cat.SPACE, "SPACE",
                junkCount == 0 ? "No junk found" : Health.humanBytes(junkBytes) + " of junk (" + junkCount + " items)",
                space));

        Health.Level speed = speedLevel(s);
        out.add(new Result(Cat.SPEED, "SPEED", speedDetail(s, speed), speed));

        Health.Level sec = Health.lock(s);
        out.add(new Result(Cat.SECURITY, "SECURITY",
                s.deviceSecure == null ? "Screen lock: unknown"
                        : (s.deviceSecure ? "Screen lock is set" : "No screen lock set"),
                sec));
        return out;
    }

    private static String speedDetail(Snapshot s, Health.Level level) {
        if (level == Health.Level.OK) return "Memory and storage are healthy";
        if (Health.storage(s) != Health.Level.OK) return "Storage is running low";
        if (Health.memory(s) != Health.Level.OK) return "Memory is under pressure";
        if (Health.battery(s) != Health.Level.OK) return "Battery is low";
        return "Needs attention";
    }

    private static Health.Level worst(Health.Level a, Health.Level b) {
        if (a == Health.Level.BAD || b == Health.Level.BAD) return Health.Level.BAD;
        if (a == Health.Level.WARN || b == Health.Level.WARN) return Health.Level.WARN;
        return Health.Level.OK;
    }
}

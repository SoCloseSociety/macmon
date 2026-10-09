package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

import java.util.List;

/** Off-device tests for the unified Health Check aggregation (pure). */
public class HealthCheckTest {

    private static Snapshot healthy() {
        Snapshot s = new Snapshot();
        s.batteryPct = 90;
        s.storageTotalBytes = 128L << 30; s.storageFreeBytes = 64L << 30;
        s.memTotalBytes = 8L << 30; s.memAvailBytes = 4L << 30;
        s.netType = "Wi-Fi";
        s.deviceSecure = Boolean.TRUE;
        return s;
    }

    @Test public void space_level_tracks_junk_size() {
        assertEquals(Health.Level.OK, HealthCheck.spaceLevel(0));
        assertEquals(Health.Level.OK, HealthCheck.spaceLevel(5L << 20));
        assertEquals(Health.Level.WARN, HealthCheck.spaceLevel(30L << 20));
        assertEquals(Health.Level.BAD, HealthCheck.spaceLevel(300L << 20));
    }

    @Test public void healthy_device_with_no_junk_is_all_ok() {
        List<HealthCheck.Result> r = HealthCheck.evaluate(healthy(), 0, 0);
        assertEquals(3, r.size());
        for (HealthCheck.Result x : r) assertEquals(x.title, Health.Level.OK, x.level);
    }

    @Test public void no_screen_lock_flags_security() {
        Snapshot s = healthy();
        s.deviceSecure = Boolean.FALSE;
        for (HealthCheck.Result x : HealthCheck.evaluate(s, 0, 0)) {
            if (x.cat == HealthCheck.Cat.SECURITY) assertEquals(Health.Level.WARN, x.level);
        }
    }

    @Test public void junk_drives_the_space_card() {
        List<HealthCheck.Result> r = HealthCheck.evaluate(healthy(), 300L << 20, 1200);
        for (HealthCheck.Result x : r) {
            if (x.cat == HealthCheck.Cat.SPACE) assertEquals(Health.Level.BAD, x.level);
        }
    }

    @Test public void speed_reflects_storage_pressure() {
        Snapshot s = healthy();
        s.storageFreeBytes = 2L << 30;  // ~1.5% free -> storage BAD
        assertEquals(Health.Level.BAD, HealthCheck.speedLevel(s));
    }
}

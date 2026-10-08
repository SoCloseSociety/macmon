package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Off-device tests for the scoring logic (no Android needed). */
public class HealthTest {

    private static Snapshot healthy() {
        Snapshot s = new Snapshot();
        s.batteryPct = 90; s.charging = false;
        s.storageTotalBytes = 128L << 30; s.storageFreeBytes = 64L << 30;  // 50% free
        s.memTotalBytes = 8L << 30; s.memAvailBytes = 4L << 30;            // 50% free
        s.lowMemory = false;
        s.netType = "Wi-Fi";
        s.deviceSecure = Boolean.TRUE;
        return s;
    }

    @Test public void healthy_device_scores_100_and_ok() {
        Snapshot s = healthy();
        assertEquals(100, Health.score(s));
        assertEquals(Health.Verdict.OK, Health.verdict(s));
        assertEquals("all clear", Health.subLabel(s));
    }

    @Test public void unknown_dimensions_are_not_penalised() {
        Snapshot s = new Snapshot();   // all sentinels
        assertEquals(100, Health.score(s));  // nothing known -> nothing counted against it
        assertEquals("all clear", Health.subLabel(s));
    }

    @Test public void low_storage_is_bad_and_drives_the_verdict() {
        Snapshot s = healthy();
        s.storageFreeBytes = 2L << 30;  // ~1.5% of 128GB -> BAD
        assertEquals(Health.Level.BAD, Health.storage(s));
        assertEquals(70, Health.score(s));            // 100 - 30
        assertEquals(Health.Verdict.WATCH, Health.verdict(s));  // a BAD forces at least WATCH
        assertEquals("storage almost full", Health.subLabel(s));
    }

    @Test public void critical_battery_when_not_charging_is_bad() {
        Snapshot s = healthy();
        s.batteryPct = 8; s.charging = false;
        assertEquals(Health.Level.BAD, Health.battery(s));
        s.charging = true;   // charging softens it to WARN
        assertEquals(Health.Level.WARN, Health.battery(s));
    }

    @Test public void no_screen_lock_is_a_warning() {
        Snapshot s = healthy();
        s.deviceSecure = Boolean.FALSE;
        assertEquals(Health.Level.WARN, Health.lock(s));
        assertEquals(88, Health.score(s));            // 100 - 12
        assertEquals("no screen lock set", Health.subLabel(s));
    }

    @Test public void offline_is_a_warning_not_a_failure() {
        Snapshot s = healthy();
        s.netType = "Offline";
        assertEquals(Health.Level.WARN, Health.network(s));
    }

    @Test public void many_problems_floor_the_score_at_zero_and_risk() {
        Snapshot s = new Snapshot();
        s.batteryPct = 3; s.charging = false;        // BAD
        s.storageTotalBytes = 100; s.storageFreeBytes = 1;  // BAD
        s.lowMemory = true;                           // BAD
        s.netType = "Offline";                        // WARN
        s.deviceSecure = Boolean.FALSE;               // WARN
        assertEquals(0, Health.score(s));             // clamped
        assertEquals(Health.Verdict.RISK, Health.verdict(s));
    }

    @Test public void human_bytes_reads_sensibly() {
        assertEquals("n/a", Health.humanBytes(-1));
        assertTrue(Health.humanBytes(64L << 30).endsWith("GB"));
        assertTrue(Health.humanBytes(512L << 20).endsWith("MB"));
    }

    @Test public void pct_rounds() {
        assertEquals("50%", Health.pct(0.5));
        assertEquals("n/a", Health.pct(-1));
    }
}

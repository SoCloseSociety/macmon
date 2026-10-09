package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Off-device tests for the updater's pure logic (manifest parse + comparison). */
public class UpdaterTest {

    @Test public void parses_a_manifest() throws Exception {
        String json = "{\"versionCode\":7,\"versionName\":\"0.7.0\","
                + "\"apkUrl\":\"https://example.test/aegisforge-7.apk\","
                + "\"sha256\":\"ABCDEF\"}";
        Updater.Info i = Updater.parse(json);
        assertEquals(7, i.versionCode);
        assertEquals("0.7.0", i.versionName);
        assertEquals("https://example.test/aegisforge-7.apk", i.apkUrl);
        assertEquals("abcdef", i.sha256);   // normalised to lower-case
    }

    @Test public void newer_only_when_code_is_greater() {
        assertTrue(Updater.isNewer(8, 7));
        assertFalse(Updater.isNewer(7, 7));
        assertFalse(Updater.isNewer(6, 7));
    }

    @Test public void empty_published_hash_defers_to_signature() {
        // no sha256 published -> integrity relies on Android's signature check
        assertTrue(Updater.sha256Matches(new java.io.File("/does/not/matter"), ""));
        assertTrue(Updater.sha256Matches(new java.io.File("/does/not/matter"), null));
    }
}

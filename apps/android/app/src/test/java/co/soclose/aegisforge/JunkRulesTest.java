package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

import co.soclose.aegisforge.clean.JunkCategory;
import co.soclose.aegisforge.clean.JunkRules;

/** Off-device tests for the junk classifier (pure, no Android). */
public class JunkRulesTest {

    @Test public void temp_and_log_files_are_junk() {
        assertEquals(JunkCategory.TEMP_LOG, JunkRules.classify("foo.tmp", false, false));
        assertEquals(JunkCategory.TEMP_LOG, JunkRules.classify("a/b.log", false, false));
        assertEquals(JunkCategory.TEMP_LOG, JunkRules.classify("X.LOG", false, false));   // case-insensitive
        assertEquals(JunkCategory.TEMP_LOG, JunkRules.classify("data.bak", false, false));
        assertEquals(JunkCategory.TEMP_LOG, JunkRules.classify("file~", false, false));
    }

    @Test public void ordinary_files_are_kept() {
        assertNull(JunkRules.classify("note.txt", false, false));
        assertNull(JunkRules.classify("DCIM/Camera/IMG_1.jpg", false, false));
        assertNull(JunkRules.classify("Music/song.mp3", false, false));
    }

    @Test public void apks_and_crash_logs_and_thumbnails() {
        assertEquals(JunkCategory.OLD_APK, JunkRules.classify("Download/old.apk", false, false));
        assertEquals(JunkCategory.CRASH_LOG, JunkRules.classify("tombstone_01", false, false));
        assertEquals(JunkCategory.CRASH_LOG, JunkRules.classify("app.crash", false, false));
        assertEquals(JunkCategory.THUMBNAILS, JunkRules.classify("DCIM/.thumbnails/1.jpg", false, false));
        assertEquals(JunkCategory.THUMBNAILS, JunkRules.classify(".thumbnails", true, true));
    }

    @Test public void other_apps_sandboxes_are_never_touched() {
        assertNull(JunkRules.classify("Android/data/com.x/cache/f.tmp", false, false));
        assertNull(JunkRules.classify("Android/obb/com.x/main.obb", false, false));
        assertNull(JunkRules.classify("Android", true, true));
    }

    @Test public void empty_dirs_except_standard_media_folders() {
        assertEquals(JunkCategory.EMPTY_DIR, JunkRules.classify("SomeLeftover", true, true));
        assertEquals(JunkCategory.EMPTY_DIR, JunkRules.classify("DCIM/EmptySub", true, true));  // a sub-folder
        assertNull(JunkRules.classify("DCIM", true, true));        // the media root itself: kept
        assertNull(JunkRules.classify("Download", true, true));
        assertNull(JunkRules.classify("NonEmpty", true, false));   // not empty -> kept
    }
}

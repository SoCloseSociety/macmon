package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import co.soclose.aegisforge.clean.Cleaner;
import co.soclose.aegisforge.clean.JunkCategory;
import co.soclose.aegisforge.clean.JunkScanner;
import co.soclose.aegisforge.clean.ScanResult;
import co.soclose.aegisforge.clean.Shredder;

/**
 * Host-JVM tests for the clean engine. The scanner, cleaner and shredder use
 * only java.io.File, so they run against real temp files off-device -- the
 * destructive paths are proven here, not by tapping an emulator.
 */
public class CleanEngineTest {

    private static File write(File dir, String rel, String content) throws IOException {
        File f = new File(dir, rel);
        f.getParentFile().mkdirs();
        Files.write(f.toPath(), content.getBytes(StandardCharsets.UTF_8));
        return f;
    }

    private static File seedTree() throws IOException {
        // Canonicalise: on macOS the temp dir lives under /var -> /private/var (a
        // symlink), which the scanner's loop-guard would otherwise skip.
        File root = Files.createTempDirectory("aegis-clean").toFile().getCanonicalFile();
        write(root, "foo.tmp", "aaaa");
        write(root, "logs/app.log", "bbbb");
        write(root, "Download/old.apk", "cccc");
        write(root, "DCIM/.thumbnails/t.jpg", "dddd");
        write(root, "keep.txt", "KEEP");                       // must survive
        write(root, "Android/data/com.x/cache/junk.tmp", "e"); // other app: must be skipped
        new File(root, "EmptyLeftover").mkdirs();              // empty dir: junk
        return root;
    }

    @Test public void scanner_finds_the_right_categories_and_skips_other_apps() throws IOException {
        File root = seedTree();
        ScanResult r = JunkScanner.scan(root, null);
        assertEquals("temp + log", 2, r.bucket(JunkCategory.TEMP_LOG).count());
        assertEquals("apk", 1, r.bucket(JunkCategory.OLD_APK).count());
        assertTrue("thumbnails", r.bucket(JunkCategory.THUMBNAILS).count() >= 1);
        assertEquals("empty dir", 1, r.bucket(JunkCategory.EMPTY_DIR).count());
        // the Android/data junk must NOT appear anywhere
        for (ScanResult.Bucket b : r.nonEmptyBuckets()) {
            for (String p : b.paths) assertFalse(p.replace('\\', '/').contains("/Android/data/"));
        }
    }

    @Test public void cleaner_deletes_junk_frees_bytes_and_spares_normal_files() throws IOException {
        File root = seedTree();
        ScanResult r = JunkScanner.scan(root, null);
        List<String> paths = new ArrayList<>();
        for (ScanResult.Bucket b : r.nonEmptyBuckets()) paths.addAll(b.paths);

        Cleaner.Outcome o = Cleaner.deleteAll(root, paths);
        assertTrue("freed some bytes", o.freedBytes > 0);
        assertTrue("deleted several", o.deleted >= 4);

        assertFalse(new File(root, "foo.tmp").exists());
        assertFalse(new File(root, "logs/app.log").exists());
        assertFalse(new File(root, "Download/old.apk").exists());
        assertFalse(new File(root, "EmptyLeftover").exists());
        assertTrue("normal file survives", new File(root, "keep.txt").exists());
        assertTrue("other app's file untouched",
                new File(root, "Android/data/com.x/cache/junk.tmp").exists());
    }

    @Test public void cleaner_refuses_paths_outside_the_root() throws IOException {
        File root = seedTree();
        File outside = Files.createTempFile("aegis-outside", ".tmp").toFile();
        List<String> paths = new ArrayList<>();
        paths.add(outside.getAbsolutePath());
        Cleaner.Outcome o = Cleaner.deleteAll(root, paths);
        assertEquals(0, o.deleted);
        assertEquals(1, o.skipped);
        assertTrue("outside file untouched", outside.exists());
        outside.delete();
    }

    @Test public void shredder_overwrites_and_deletes_a_real_file() throws IOException {
        File root = Files.createTempDirectory("aegis-shred").toFile();
        File f = write(root, "secret.bin", "topsecretpayload");
        assertTrue(Shredder.canShred(f));
        assertTrue(Shredder.shred(f));
        assertFalse(f.exists());
    }

    @Test public void shredder_refuses_other_app_sandboxes() throws IOException {
        File root = Files.createTempDirectory("aegis-shred2").toFile();
        File f = write(root, "Android/data/com.x/cache/s.bin", "x");
        assertFalse("never shred another app's sandbox", Shredder.canShred(f));
    }
}

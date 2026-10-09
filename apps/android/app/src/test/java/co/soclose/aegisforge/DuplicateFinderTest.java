package co.soclose.aegisforge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.List;

import co.soclose.aegisforge.clean.DuplicateFinder;

/** Host-JVM tests for the duplicate finder (pure java.io). */
public class DuplicateFinderTest {

    private static void write(File dir, String rel, char fill, int bytes) throws IOException {
        File f = new File(dir, rel);
        f.getParentFile().mkdirs();
        char[] c = new char[bytes];
        Arrays.fill(c, fill);
        Files.write(f.toPath(), new String(c).getBytes(StandardCharsets.UTF_8));
    }

    @Test public void finds_identical_content_ignores_same_size_different_content() throws IOException {
        File root = Files.createTempDirectory("aegis-dup").toFile().getCanonicalFile();
        write(root, "a/copy1.bin", 'A', 20000);   // identical pair
        write(root, "b/copy2.bin", 'A', 20000);
        write(root, "c/unique.bin", 'Z', 20000);   // same size, different content
        write(root, "c/other.bin", 'Y', 20000);    // same size, different content
        write(root, "d/tiny.bin", 'A', 100);       // below MIN_SIZE, ignored

        List<DuplicateFinder.Group> groups = DuplicateFinder.find(root, null);

        assertEquals("one duplicate group", 1, groups.size());
        DuplicateFinder.Group g = groups.get(0);
        assertEquals(2, g.paths.size());
        assertEquals(20000L, g.size);
        assertEquals(20000L, g.reclaimable());
        assertTrue(g.paths.get(0).endsWith("copy1.bin") || g.paths.get(1).endsWith("copy1.bin"));
    }

    @Test public void no_duplicates_yields_no_groups() throws IOException {
        File root = Files.createTempDirectory("aegis-dup2").toFile().getCanonicalFile();
        write(root, "x.bin", 'A', 20000);
        write(root, "y.bin", 'B', 30000);
        assertTrue(DuplicateFinder.find(root, null).isEmpty());
    }
}

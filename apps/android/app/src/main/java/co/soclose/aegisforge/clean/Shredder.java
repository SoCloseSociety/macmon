package co.soclose.aegisforge.clean;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * Secure delete, best-effort. Overwrites a file's bytes a few times before
 * deleting it, and can fill free space so previously deleted data is harder to
 * recover.
 *
 * HONEST CAVEAT: on flash storage (every phone) wear-levelling means the
 * controller may write elsewhere, so an overwrite is NOT a guarantee the old
 * bytes are gone. This raises the bar; it is not a military wipe. The UI says so.
 *
 * Guarded: refuses anything under Android/ or outside a regular, writable file.
 */
public final class Shredder {
    private Shredder() {}

    private static final SecureRandom RNG = new SecureRandom();
    private static final int PASSES = 3;
    private static final int BUF = 1 << 16;

    public static boolean canShred(File f) {
        if (f == null || !f.isFile() || !f.canWrite()) return false;
        String p = f.getAbsolutePath().toLowerCase(Locale.US).replace('\\', '/');
        return !p.contains("/android/data/") && !p.contains("/android/obb/");
    }

    /** Overwrite then delete. Returns true only if the file is gone afterwards. */
    public static boolean shred(File f) {
        if (!canShred(f)) return false;
        long len = f.length();
        byte[] buf = new byte[BUF];
        try (RandomAccessFile raf = new RandomAccessFile(f, "rws")) {
            for (int pass = 0; pass < PASSES; pass++) {
                raf.seek(0);
                long remaining = len;
                while (remaining > 0) {
                    RNG.nextBytes(buf);
                    int n = (int) Math.min(buf.length, remaining);
                    raf.write(buf, 0, n);
                    remaining -= n;
                }
                raf.getFD().sync();
            }
            raf.setLength(0);
            raf.getFD().sync();
        } catch (IOException e) {
            return false;
        }
        return f.delete() && !f.exists();
    }

    public interface Progress { void onBytes(long written); boolean cancelled(); }

    /**
     * Fill the free space of the volume holding {@code dir} with zeroes, then
     * remove the filler, so deleted data is overwritten. Best-effort and bounded
     * by {@code maxBytes} (0 = until full). Returns bytes written.
     */
    public static long wipeFreeSpace(File dir, long maxBytes, Progress progress) {
        if (dir == null || !dir.isDirectory() || !dir.canWrite()) return 0L;
        File filler = new File(dir, ".aegis_wipe.tmp");
        byte[] zeros = new byte[BUF];
        long written = 0L;
        try (RandomAccessFile raf = new RandomAccessFile(filler, "rw")) {
            while (maxBytes <= 0 || written < maxBytes) {
                if (progress != null && progress.cancelled()) break;
                raf.write(zeros);
                written += zeros.length;
                if (progress != null && (written % (BUF * 256L) == 0)) progress.onBytes(written);
            }
            raf.getFD().sync();
        } catch (IOException e) {
            // expected: the volume filled up
        } finally {
            if (filler.exists()) { boolean ignored = filler.delete(); }
        }
        return written;
    }
}

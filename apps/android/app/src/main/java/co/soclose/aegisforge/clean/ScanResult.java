package co.soclose.aegisforge.clean;

import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;

/**
 * What a junk scan found, grouped by category. Pure data: the scanner fills it,
 * the UI reads it, tests build it directly.
 */
public final class ScanResult {

    public static final class Bucket {
        public final JunkCategory category;
        public long bytes;
        public final List<String> paths = new ArrayList<>();
        Bucket(JunkCategory c) { this.category = c; }
        public int count() { return paths.size(); }
    }

    private final EnumMap<JunkCategory, Bucket> buckets = new EnumMap<>(JunkCategory.class);

    public void add(JunkCategory cat, String path, long size) {
        Bucket b = buckets.get(cat);
        if (b == null) { b = new Bucket(cat); buckets.put(cat, b); }
        b.paths.add(path);
        b.bytes += Math.max(0L, size);
    }

    public Bucket bucket(JunkCategory cat) { return buckets.get(cat); }

    public List<Bucket> nonEmptyBuckets() {
        List<Bucket> out = new ArrayList<>();
        for (JunkCategory c : JunkCategory.values()) {
            Bucket b = buckets.get(c);
            if (b != null && b.count() > 0) out.add(b);
        }
        return out;
    }

    public long totalBytes() {
        long t = 0;
        for (Bucket b : buckets.values()) t += b.bytes;
        return t;
    }

    public int totalCount() {
        int t = 0;
        for (Bucket b : buckets.values()) t += b.count();
        return t;
    }
}

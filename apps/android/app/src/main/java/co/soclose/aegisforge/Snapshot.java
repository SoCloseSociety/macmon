package co.soclose.aegisforge;

/**
 * One reading of the phone's own health. A plain data holder with no Android
 * types, so the scoring in {@link Health} can be unit-tested off-device.
 * Unknown fields keep a sentinel (-1 / null) so the UI can say so honestly
 * rather than show a made-up number.
 */
public final class Snapshot {
    public int batteryPct = -1;          // 0..100, -1 = unknown
    public boolean charging = false;
    public long storageFreeBytes = -1L;  // internal data partition
    public long storageTotalBytes = -1L;
    public long memAvailBytes = -1L;
    public long memTotalBytes = -1L;
    public boolean lowMemory = false;
    public String netType = "Unknown";   // Wi-Fi / Cellular / Ethernet / Offline / Unknown
    public Boolean deviceSecure = null;  // screen lock set; null = unknown
    public long takenAtMillis = 0L;

    public double storageFreeRatio() {
        if (storageTotalBytes <= 0L || storageFreeBytes < 0L) return -1.0;
        return (double) storageFreeBytes / (double) storageTotalBytes;
    }

    public double memAvailRatio() {
        if (memTotalBytes <= 0L || memAvailBytes < 0L) return -1.0;
        return (double) memAvailBytes / (double) memTotalBytes;
    }
}

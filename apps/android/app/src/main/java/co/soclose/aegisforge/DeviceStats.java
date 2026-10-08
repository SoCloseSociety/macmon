package co.soclose.aegisforge;

import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;
import android.os.Environment;
import android.os.StatFs;

/**
 * Reads the phone's own health into a {@link Snapshot}. Every read is wrapped,
 * so a metric that cannot be obtained stays unknown (the UI shows "n/a") instead
 * of crashing the widget. Nothing leaves the device and no runtime permission is
 * needed (ACCESS_NETWORK_STATE is "normal").
 */
public final class DeviceStats {
    private DeviceStats() {}

    public static Snapshot read(Context ctx) {
        Snapshot s = new Snapshot();
        s.takenAtMillis = System.currentTimeMillis();
        readBattery(ctx, s);
        readStorage(s);
        readMemory(ctx, s);
        readNetwork(ctx, s);
        readLock(ctx, s);
        return s;
    }

    private static void readBattery(Context ctx, Snapshot s) {
        try {
            BatteryManager bm = (BatteryManager) ctx.getSystemService(Context.BATTERY_SERVICE);
            if (bm != null) {
                int pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
                if (pct >= 0 && pct <= 100) s.batteryPct = pct;
                s.charging = bm.isCharging();
            }
        } catch (Throwable ignored) { }
    }

    private static void readStorage(Snapshot s) {
        try {
            StatFs fs = new StatFs(Environment.getDataDirectory().getPath());
            s.storageTotalBytes = fs.getTotalBytes();
            s.storageFreeBytes = fs.getAvailableBytes();
        } catch (Throwable ignored) { }
    }

    private static void readMemory(Context ctx, Snapshot s) {
        try {
            ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
            if (am != null) {
                ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
                am.getMemoryInfo(mi);
                s.memAvailBytes = mi.availMem;
                s.memTotalBytes = mi.totalMem;
                s.lowMemory = mi.lowMemory;
            }
        } catch (Throwable ignored) { }
    }

    private static void readNetwork(Context ctx, Snapshot s) {
        try {
            ConnectivityManager cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) { s.netType = "Unknown"; return; }
            Network net = cm.getActiveNetwork();
            if (net == null) { s.netType = "Offline"; return; }
            NetworkCapabilities caps = cm.getNetworkCapabilities(net);
            if (caps == null) { s.netType = "Offline"; return; }
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) s.netType = "Wi-Fi";
            else if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) s.netType = "Cellular";
            else if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) s.netType = "Ethernet";
            else s.netType = "Online";
        } catch (Throwable ignored) { s.netType = "Unknown"; }
    }

    private static void readLock(Context ctx, Snapshot s) {
        try {
            KeyguardManager km = (KeyguardManager) ctx.getSystemService(Context.KEYGUARD_SERVICE);
            if (km != null) s.deviceSecure = km.isDeviceSecure();
        } catch (Throwable ignored) { }
    }
}

package co.soclose.aegisforge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Environment;

import java.io.File;

import co.soclose.aegisforge.clean.JunkScanner;
import co.soclose.aegisforge.clean.ScanResult;

/**
 * Shared background check: scan shared storage and, if junk crosses the
 * threshold, post ONE notification that opens the cleaner. Used by the periodic
 * Smart Cleaning job and by the daily scheduled alarm. Notify only -- it never
 * deletes. Safe to call on a background thread.
 */
public final class JunkNotifier {
    private JunkNotifier() {}

    static final String CHANNEL = "aegis_smart";
    static final int NOTIF_ID = 4712;

    public static boolean hasStorageAccess(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Environment.isExternalStorageManager();
        }
        return ctx.checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    public static void scanAndNotify(Context ctx) {
        if (!hasStorageAccess(ctx)) return;
        try {
            File root = Environment.getExternalStorageDirectory();
            ScanResult res = JunkScanner.scan(root, null);
            if (res.totalBytes() >= SmartClean.THRESHOLD_BYTES) {
                notify(ctx, res.totalBytes(), res.totalCount());
            }
        } catch (Throwable ignored) {
        }
    }

    private static void notify(Context ctx, long bytes, int count) {
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(
                    CHANNEL, "Smart Cleaning", NotificationManager.IMPORTANCE_DEFAULT));
        }
        Intent open = new Intent(ctx, MainActivity.class)
                .putExtra("open_cleaner", true)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(ctx, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle("AegisForge: " + Health.humanBytes(bytes) + " of junk")
                .setContentText(count + " cleanable items found. Tap to review and clean.")
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        nm.notify(NOTIF_ID, n);
    }
}

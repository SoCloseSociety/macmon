package co.soclose.aegisforge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobParameters;
import android.app.job.JobService;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Environment;

import java.io.File;

import co.soclose.aegisforge.clean.JunkScanner;
import co.soclose.aegisforge.clean.ScanResult;

/**
 * The Smart Cleaning job: scan shared storage off the main thread, and if the
 * junk crosses the threshold, post ONE notification that opens the cleaner. It
 * never deletes anything itself -- notify only.
 */
public class SmartCleanJob extends JobService {

    private static final String CHANNEL = "aegis_smart";
    private static final int NOTIF_ID = 4712;

    @Override
    public boolean onStartJob(JobParameters params) {
        if (!hasStorageAccess()) { return false; }  // nothing to do without access
        new Thread(() -> {
            try {
                File root = Environment.getExternalStorageDirectory();
                ScanResult res = JunkScanner.scan(root, null);
                if (res.totalBytes() >= SmartClean.THRESHOLD_BYTES) {
                    notifyJunk(res.totalBytes(), res.totalCount());
                }
            } catch (Throwable ignored) {
            } finally {
                jobFinished(params, false);
            }
        }, "aegis-smartclean").start();
        return true;  // work continues on the thread
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;  // reschedule if the system stopped us early
    }

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Environment.isExternalStorageManager();
        }
        return checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void notifyJunk(long bytes, int count) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(
                    CHANNEL, "Smart Cleaning", NotificationManager.IMPORTANCE_DEFAULT));
        }
        Intent open = new Intent(this, MainActivity.class)
                .putExtra("open_cleaner", true)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification n = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle("AegisForge: " + Health.humanBytes(bytes) + " of junk")
                .setContentText(count + " cleanable items found. Tap to review and clean.")
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        nm.notify(NOTIF_ID, n);
    }
}

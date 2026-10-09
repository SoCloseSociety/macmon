package co.soclose.aegisforge;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;

/**
 * Smart Cleaning: a periodic background scan that notifies when junk crosses a
 * threshold, so the phone stays clean without being asked. Framework JobScheduler
 * only (no WorkManager dependency). Honest: it only ever notifies -- it never
 * deletes on its own; the user opens the cleaner and decides.
 */
public final class SmartClean {
    private SmartClean() {}

    static final int JOB_ID = 4711;
    static final long THRESHOLD_BYTES = 50L * 1024 * 1024;   // notify above ~50 MB of junk
    private static final long PERIOD_MS = 12L * 60 * 60 * 1000;  // every 12h
    private static final String PREFS = "aegis_widgets";
    private static final String KEY = "smart_clean";

    public static boolean isEnabled(Context c) {
        return prefs(c).getBoolean(KEY, false);
    }

    public static void setEnabled(Context c, boolean on) {
        prefs(c).edit().putBoolean(KEY, on).apply();
        if (on) schedule(c); else cancel(c);
    }

    public static void schedule(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        JobInfo job = new JobInfo.Builder(JOB_ID, new ComponentName(c, SmartCleanJob.class))
                .setPeriodic(PERIOD_MS)
                .setPersisted(true)
                .setRequiresDeviceIdle(false)
                .build();
        js.schedule(job);
    }

    public static void cancel(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js != null) js.cancel(JOB_ID);
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}

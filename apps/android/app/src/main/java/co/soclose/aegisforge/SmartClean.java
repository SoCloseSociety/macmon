package co.soclose.aegisforge;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.util.Calendar;

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
    private static final String KEY_HOUR = "daily_hour";   // 0..23, or -1 = off
    static final String ACTION_DAILY = "co.soclose.aegisforge.DAILY";
    private static final int DAILY_RC = 4713;

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

    // ---- daily scheduled scan (AlarmManager) ----

    /** The hour (0..23) of the daily scan, or -1 if off. */
    public static int dailyHour(Context c) {
        return prefs(c).getInt(KEY_HOUR, -1);
    }

    /** Set the daily scan hour (0..23), or -1 to turn it off. */
    public static void setDailyHour(Context c, int hour) {
        prefs(c).edit().putInt(KEY_HOUR, hour).apply();
        if (hour < 0 || hour > 23) cancelDaily(c); else scheduleDaily(c, hour);
    }

    public static void rearmAfterBoot(Context c) {
        int h = dailyHour(c);
        if (h >= 0 && h <= 23) scheduleDaily(c, h);
        if (isEnabled(c)) schedule(c);
    }

    private static void scheduleDaily(Context c, int hour) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        Calendar when = Calendar.getInstance();
        when.set(Calendar.HOUR_OF_DAY, hour);
        when.set(Calendar.MINUTE, 0);
        when.set(Calendar.SECOND, 0);
        when.set(Calendar.MILLISECOND, 0);
        if (when.getTimeInMillis() <= System.currentTimeMillis()) {
            when.add(Calendar.DAY_OF_YEAR, 1);  // next occurrence
        }
        am.setInexactRepeating(AlarmManager.RTC, when.getTimeInMillis(),
                AlarmManager.INTERVAL_DAY, dailyIntent(c));
    }

    private static void cancelDaily(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(dailyIntent(c));
    }

    private static PendingIntent dailyIntent(Context c) {
        Intent i = new Intent(c, DailyScanReceiver.class).setAction(ACTION_DAILY);
        return PendingIntent.getBroadcast(c, DAILY_RC, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}

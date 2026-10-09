package co.soclose.aegisforge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Fired by the daily scheduled alarm (see {@link SmartClean#setDailyHour}) and
 * after a reboot to re-arm it. Scans for junk and notifies if it crosses the
 * threshold. goAsync() keeps the process alive for the short background scan.
 */
public class DailyScanReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            SmartClean.rearmAfterBoot(context);
            return;
        }
        final Context app = context.getApplicationContext();
        final PendingResult pending = goAsync();
        new Thread(() -> {
            try { JunkNotifier.scanAndNotify(app); }
            finally { pending.finish(); }
        }, "aegis-daily").start();
    }
}

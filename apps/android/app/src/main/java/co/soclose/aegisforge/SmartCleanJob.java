package co.soclose.aegisforge;

import android.app.job.JobParameters;
import android.app.job.JobService;

/**
 * The periodic Smart Cleaning job: scan shared storage off the main thread and
 * notify if junk crosses the threshold (see {@link JunkNotifier}). Notify only.
 */
public class SmartCleanJob extends JobService {

    @Override
    public boolean onStartJob(JobParameters params) {
        if (!JunkNotifier.hasStorageAccess(this)) return false;
        new Thread(() -> {
            try { JunkNotifier.scanAndNotify(this); }
            finally { jobFinished(params, false); }
        }, "aegis-smartclean").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;  // reschedule if the system stopped us early
    }
}

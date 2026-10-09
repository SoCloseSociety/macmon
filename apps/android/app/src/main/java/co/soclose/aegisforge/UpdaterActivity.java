package co.soclose.aegisforge;

import android.app.Activity;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;

/**
 * Self-update screen. Checks the published manifest over HTTPS and, if a newer
 * AegisForge build exists, downloads it, verifies its SHA-256, and hands it to
 * the system installer. This is the ONLY place the app touches the network, and
 * only when the user taps Check.
 */
public class UpdaterActivity extends Activity {

    private Button check, action;
    private TextView status, note;
    private volatile boolean busy = false, cancelled = false;
    private Updater.Info pending;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(getColor(R.color.ground));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(24), dp(24), dp(24), dp(24));
        scroll.addView(col);

        TextView t = new TextView(this);
        t.setText(getString(R.string.upd_title)); t.setAllCaps(true);
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextColor(getColor(R.color.text)); t.setTextSize(22f); t.setLetterSpacing(0.06f);
        col.addView(t);

        TextView installed = new TextView(this);
        installed.setText("Installed: v" + versionName());
        installed.setTextColor(getColor(R.color.mint)); installed.setTextSize(13f);
        installed.setPadding(0, dp(4), 0, dp(16));
        col.addView(installed);

        check = new Button(this);
        check.setText(getString(R.string.upd_check)); check.setAllCaps(false);
        check.setTypeface(null, Typeface.BOLD);
        check.setBackgroundTintList(getColorStateList(R.color.mint));
        check.setTextColor(getColor(R.color.mint_ink));
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52));
        check.setLayoutParams(cp);
        check.setOnClickListener(v -> check());
        col.addView(check);

        status = new TextView(this);
        status.setTextColor(getColor(R.color.text)); status.setTextSize(14f);
        status.setPadding(0, dp(16), 0, 0);
        col.addView(status);

        action = new Button(this);
        action.setAllCaps(false); action.setTypeface(null, Typeface.BOLD);
        action.setBackgroundTintList(getColorStateList(R.color.mint));
        action.setTextColor(getColor(R.color.mint_ink));
        LinearLayout.LayoutParams ap = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52));
        ap.topMargin = dp(12);
        action.setLayoutParams(ap);
        action.setVisibility(android.view.View.GONE);
        action.setOnClickListener(v -> downloadAndInstall());
        col.addView(action);

        note = new TextView(this);
        note.setText(getString(R.string.upd_note));
        note.setTextColor(getColor(R.color.text_2)); note.setTextSize(11f);
        note.setLineSpacing(0, 1.25f); note.setPadding(0, dp(24), 0, 0);
        col.addView(note);

        setContentView(scroll);
    }

    @Override protected void onDestroy() { cancelled = true; super.onDestroy(); }

    private void check() {
        if (busy) return;
        busy = true; cancelled = false;
        check.setEnabled(false);
        action.setVisibility(android.view.View.GONE);
        status.setText(getString(R.string.upd_checking));
        new Thread(() -> {
            try {
                Updater.Info info = Updater.parse(Updater.fetchManifest());
                int installed = Updater.installedVersionCode(this);
                runOnUiThread(() -> {
                    busy = false; check.setEnabled(true);
                    if (Updater.isNewer(info.versionCode, installed)) {
                        pending = info;
                        status.setText("Update available: v" + info.versionName);
                        action.setText(getString(R.string.upd_install));
                        action.setVisibility(android.view.View.VISIBLE);
                    } else {
                        status.setText(getString(R.string.upd_uptodate));
                    }
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    busy = false; check.setEnabled(true);
                    status.setText(getString(R.string.upd_failed));
                });
            }
        }, "aegis-updcheck").start();
    }

    private void downloadAndInstall() {
        if (busy || pending == null) return;
        busy = true;
        action.setEnabled(false);
        status.setText(getString(R.string.upd_downloading));
        final Updater.Info info = pending;
        new Thread(() -> {
            File apk = new File(getCacheDir(), "aegisforge-update.apk");
            boolean ok = Updater.download(info.apkUrl, apk, new Updater.Progress() {
                long last = 0;
                public void onBytes(long got, long total) {
                    long now = System.currentTimeMillis();
                    if (now - last > 150) {
                        last = now;
                        runOnUiThread(() -> status.setText("Downloading: " + Health.humanBytes(got)
                                + (total > 0 ? " / " + Health.humanBytes(total) : "")));
                    }
                }
                public boolean cancelled() { return cancelled; }
            });
            if (!ok || !Updater.sha256Matches(apk, info.sha256)) {
                runOnUiThread(() -> {
                    busy = false; action.setEnabled(true);
                    status.setText(getString(R.string.upd_verify_failed));
                });
                return;
            }
            runOnUiThread(() -> {
                busy = false; action.setEnabled(true);
                status.setText(getString(R.string.upd_installing));
                try { Updater.install(this, apk); }
                catch (Exception e) { Toast.makeText(this, "Install failed", Toast.LENGTH_LONG).show(); }
            });
        }, "aegis-upddl").start();
    }

    private String versionName() {
        try { return getPackageManager().getPackageInfo(getPackageName(), 0).versionName; }
        catch (Exception e) { return "?"; }
    }

    private int dp(int d) { return (int) (d * getResources().getDisplayMetrics().density); }
}

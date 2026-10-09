package co.soclose.aegisforge;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import co.soclose.aegisforge.clean.Cleaner;
import co.soclose.aegisforge.clean.JunkScanner;
import co.soclose.aegisforge.clean.ScanResult;
import co.soclose.aegisforge.clean.Shredder;

/**
 * The cleaner funnel, CCleaner-style: Analyze -> categorized junk -> Clean. All
 * on-device, honest (it reports only what it actually freed), and guarded (it
 * scans shared storage only, never another app's sandbox). Also wipes free space.
 * Framework-only: a background thread scans/cleans, the UI thread renders.
 */
public class CleanerActivity extends Activity {

    private LinearLayout permBox, scanBox, results;
    private Button btnAnalyze, btnClean, btnWipe;
    private TextView progress, total;

    private final List<CheckBox> boxes = new ArrayList<>();
    private final List<ScanResult.Bucket> buckets = new ArrayList<>();
    private volatile boolean cancelled = false;
    private volatile boolean busy = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_cleaner);
        permBox = findViewById(R.id.perm_box);
        scanBox = findViewById(R.id.scan_box);
        results = findViewById(R.id.results);
        btnAnalyze = findViewById(R.id.btn_analyze);
        btnClean = findViewById(R.id.btn_clean);
        btnWipe = findViewById(R.id.btn_wipe);
        progress = findViewById(R.id.progress);
        total = findViewById(R.id.total);

        findViewById(R.id.btn_grant).setOnClickListener(v -> requestAccess());
        btnAnalyze.setOnClickListener(v -> analyze());
        btnClean.setOnClickListener(v -> confirmClean());
        btnWipe.setOnClickListener(v -> confirmWipe());
    }

    @Override
    protected void onResume() {
        super.onResume();
        gate();
    }

    @Override
    protected void onDestroy() {
        cancelled = true;
        super.onDestroy();
    }

    private void gate() {
        boolean ok = hasStorageAccess();
        permBox.setVisibility(ok ? View.GONE : View.VISIBLE);
        scanBox.setVisibility(ok ? View.VISIBLE : View.GONE);
    }

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Environment.isExternalStorageManager();
        }
        return checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void requestAccess() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName()));
                startActivity(i);
            } else {
                requestPermissions(new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 1);
            }
        } catch (Exception e) {
            startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
        }
    }

    @Override
    public void onRequestPermissionsResult(int rc, String[] perms, int[] grants) {
        super.onRequestPermissionsResult(rc, perms, grants);
        gate();
    }

    // ---- scan ----

    private void analyze() {
        if (busy) return;
        busy = true; cancelled = false;
        results.removeAllViews();
        boxes.clear(); buckets.clear();
        total.setVisibility(View.GONE);
        btnClean.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        progress.setText(getString(R.string.scanning) + "...");
        btnAnalyze.setEnabled(false);

        final File root = Environment.getExternalStorageDirectory();
        new Thread(() -> {
            final long[] last = {0};
            ScanResult res = JunkScanner.scan(root, new JunkScanner.Progress() {
                public void onDir(String name) {
                    long now = System.currentTimeMillis();
                    if (now - last[0] > 120) {
                        last[0] = now;
                        runOnUiThread(() -> progress.setText(getString(R.string.scanning) + ": " + name));
                    }
                }
                public boolean cancelled() { return cancelled; }
            });
            runOnUiThread(() -> showResult(res));
        }, "aegis-scan").start();
    }

    private void showResult(ScanResult res) {
        busy = false;
        btnAnalyze.setEnabled(true);
        progress.setVisibility(View.GONE);
        results.removeAllViews();
        boxes.clear(); buckets.clear();

        List<ScanResult.Bucket> found = res.nonEmptyBuckets();
        if (found.isEmpty()) {
            total.setVisibility(View.VISIBLE);
            total.setText(getString(R.string.nothing_found));
            btnClean.setVisibility(View.GONE);
            return;
        }
        for (ScanResult.Bucket b : found) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(0, dp(6), 0, dp(6));

            CheckBox cb = new CheckBox(this);
            cb.setChecked(true);
            cb.setText(b.category.label + "  (" + b.count() + ")");
            cb.setTextColor(getColor(R.color.text));
            cb.setLayoutParams(new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

            TextView size = new TextView(this);
            size.setText(Health.humanBytes(b.bytes));
            size.setTextColor(getColor(R.color.text_2));
            size.setTypeface(android.graphics.Typeface.MONOSPACE);

            row.addView(cb);
            row.addView(size);
            results.addView(row);
            boxes.add(cb);
            buckets.add(b);
        }
        total.setVisibility(View.VISIBLE);
        total.setText("Cleanable: " + Health.humanBytes(res.totalBytes()) + "  (" + res.totalCount() + " items)");
        btnClean.setVisibility(View.VISIBLE);
    }

    // ---- clean ----

    private List<String> selectedPaths() {
        List<String> paths = new ArrayList<>();
        for (int i = 0; i < boxes.size(); i++) {
            if (boxes.get(i).isChecked()) paths.addAll(buckets.get(i).paths);
        }
        return paths;
    }

    private void confirmClean() {
        final List<String> paths = selectedPaths();
        if (paths.isEmpty()) { toast("Nothing selected"); return; }
        new AlertDialog.Builder(this)
                .setTitle("Clean")
                .setMessage("Delete " + paths.size() + " item(s)? This cannot be undone.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Clean", (d, w) -> doClean(paths))
                .show();
    }

    private void doClean(List<String> paths) {
        if (busy) return;
        busy = true;
        btnClean.setEnabled(false);
        final File root = Environment.getExternalStorageDirectory();
        new Thread(() -> {
            Cleaner.Outcome o = Cleaner.deleteAll(root, paths);
            runOnUiThread(() -> {
                busy = false;
                btnClean.setEnabled(true);
                toast("Freed " + Health.humanBytes(o.freedBytes) + "  (" + o.deleted + " removed, " + o.skipped + " skipped)");
                analyze();  // re-scan to reflect the new state honestly
            });
        }, "aegis-clean").start();
    }

    // ---- wipe free space ----

    private void confirmWipe() {
        new AlertDialog.Builder(this)
                .setTitle("Wipe free space")
                .setMessage(getString(R.string.wipe_note) + "\n\nThis can take a while and temporarily fills storage. Continue?")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Wipe", (d, w) -> doWipe())
                .show();
    }

    private void doWipe() {
        if (busy) return;
        busy = true; cancelled = false;
        btnWipe.setEnabled(false);
        progress.setVisibility(View.VISIBLE);
        progress.setText("Wiping free space...");
        final File dir = Environment.getExternalStorageDirectory();
        new Thread(() -> {
            long written = Shredder.wipeFreeSpace(dir, 0L, new Shredder.Progress() {
                public void onBytes(long w) {
                    runOnUiThread(() -> progress.setText("Wiping free space: " + Health.humanBytes(w)));
                }
                public boolean cancelled() { return cancelled; }
            });
            runOnUiThread(() -> {
                busy = false;
                btnWipe.setEnabled(true);
                progress.setVisibility(View.GONE);
                toast("Wiped " + Health.humanBytes(written) + " of free space");
            });
        }, "aegis-wipe").start();
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }

    private int dp(int d) {
        return (int) (d * getResources().getDisplayMetrics().density);
    }
}

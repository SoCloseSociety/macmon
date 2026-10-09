package co.soclose.aegisforge;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.util.List;

import co.soclose.aegisforge.clean.JunkScanner;
import co.soclose.aegisforge.clean.ScanResult;

/**
 * The unified Health Check dashboard, the CCleaner one-tap experience: one scan
 * folds junk + device pressure + screen-lock posture into Space / Speed /
 * Security cards with an overall score, and each card offers the fix that
 * Android actually allows (open the cleaner, open security settings). Honest: no
 * fake "boost", no category the OS forbids assessing.
 */
public class HealthCheckActivity extends Activity {

    private LinearLayout col, cards;
    private Button scan;
    private TextView scoreView, verdictView, status;
    private volatile boolean busy = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(getColor(R.color.ground));
        col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(24), dp(24), dp(24), dp(24));
        scroll.addView(col);

        col.addView(title(getString(R.string.hc_title)));
        col.addView(tag(getString(R.string.hc_tag)));

        scoreView = new TextView(this);
        scoreView.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        scoreView.setTextColor(getColor(R.color.text));
        scoreView.setTextSize(52f);
        scoreView.setPadding(0, dp(16), 0, 0);
        col.addView(scoreView);

        verdictView = new TextView(this);
        verdictView.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        verdictView.setTextSize(13f);
        col.addView(verdictView);

        scan = new Button(this);
        scan.setText(getString(R.string.hc_scan));
        scan.setAllCaps(false);
        scan.setTypeface(null, Typeface.BOLD);
        scan.setBackgroundTintList(getColorStateList(R.color.mint));
        scan.setTextColor(getColor(R.color.mint_ink));
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56));
        sp.topMargin = dp(20);
        scan.setLayoutParams(sp);
        scan.setOnClickListener(v -> run());
        col.addView(scan);

        status = new TextView(this);
        status.setTextColor(getColor(R.color.text_2));
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextSize(12f);
        status.setPadding(0, dp(12), 0, 0);
        col.addView(status);

        cards = new LinearLayout(this);
        cards.setOrientation(LinearLayout.VERTICAL);
        cards.setPadding(0, dp(8), 0, 0);
        col.addView(cards);

        setContentView(scroll);
        run();  // scan on open, CCleaner-style
    }

    private void run() {
        if (busy) return;
        busy = true;
        scan.setEnabled(false);
        cards.removeAllViews();
        status.setText(getString(R.string.scanning) + "...");
        new Thread(() -> {
            Snapshot snap = DeviceStats.read(this);
            long junkBytes = 0; int junkCount = 0;
            if (hasStorageAccess()) {
                File root = Environment.getExternalStorageDirectory();
                ScanResult r = JunkScanner.scan(root, null);
                junkBytes = r.totalBytes(); junkCount = r.totalCount();
            }
            final long jb = junkBytes; final int jc = junkCount;
            List<HealthCheck.Result> results = HealthCheck.evaluate(snap, jb, jc);
            int score = combinedScore(snap, jb);
            runOnUiThread(() -> render(score, results, jc));
        }, "aegis-healthcheck").start();
    }

    private int combinedScore(Snapshot s, long junkBytes) {
        int score = Health.score(s);
        Health.Level space = HealthCheck.spaceLevel(junkBytes);
        if (space == Health.Level.BAD) score -= 15;
        else if (space == Health.Level.WARN) score -= 8;
        return Math.max(0, Math.min(100, score));
    }

    private void render(int score, List<HealthCheck.Result> results, int junkCount) {
        busy = false;
        scan.setEnabled(true);
        status.setText(hasStorageAccess()
                ? "Scan complete"
                : "Grant All Files Access in the Cleaner to include junk");
        scoreView.setText(String.valueOf(score));
        Health.Verdict v = score >= 80 ? Health.Verdict.OK : score >= 50 ? Health.Verdict.WATCH : Health.Verdict.RISK;
        verdictView.setText(Health.verdictText(v) + "  /  " + score + " / 100");
        verdictView.setTextColor(levelColor(v == Health.Verdict.OK ? Health.Level.OK
                : v == Health.Verdict.WATCH ? Health.Level.WARN : Health.Level.BAD));

        cards.removeAllViews();
        for (HealthCheck.Result r : results) cards.addView(card(r));
    }

    private View card(HealthCheck.Result r) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundResource(R.drawable.widget_bg);
        box.setPadding(dp(16), dp(14), dp(16), dp(14));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        box.setLayoutParams(lp);

        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView label = new TextView(this);
        label.setText(r.title);
        label.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        label.setLetterSpacing(0.1f);
        label.setTextColor(getColor(R.color.text_2));
        label.setTextSize(12f);
        label.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView pill = new TextView(this);
        pill.setText(pillText(r.level));
        pill.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        pill.setTextSize(11f);
        pill.setPadding(dp(9), dp(2), dp(9), dp(2));
        pill.setBackgroundResource(pillBg(r.level));
        pill.setTextColor(getColor(pillInk(r.level)));
        head.addView(label);
        head.addView(pill);
        box.addView(head);

        TextView detail = new TextView(this);
        detail.setText(r.detail);
        detail.setTextColor(getColor(R.color.text));
        detail.setTextSize(14f);
        detail.setPadding(0, dp(6), 0, 0);
        box.addView(detail);

        Button action = actionFor(r);
        if (action != null) box.addView(action);
        return box;
    }

    private Button actionFor(HealthCheck.Result r) {
        String text; Runnable act;
        if (r.cat == HealthCheck.Cat.SPACE) {
            if (r.level == Health.Level.OK) return null;
            text = getString(R.string.hc_clean);
            act = () -> startActivity(new Intent(this, CleanerActivity.class));
        } else if (r.cat == HealthCheck.Cat.SECURITY) {
            if (r.level == Health.Level.OK) return null;
            text = getString(R.string.hc_fix);
            act = () -> startActivity(new Intent(Settings.ACTION_SECURITY_SETTINGS));
        } else {
            return null;  // SPEED is informational (the OS manages memory)
        }
        Button b = new Button(this);
        b.setText(text); b.setAllCaps(false);
        b.setBackgroundTintList(getColorStateList(R.color.ground_2));
        b.setTextColor(getColor(R.color.mint));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(8);
        b.setLayoutParams(lp);
        b.setOnClickListener(v -> act.run());
        return b;
    }

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) return Environment.isExternalStorageManager();
        return checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private int levelColor(Health.Level l) {
        return getColor(l == Health.Level.BAD ? R.color.alert : l == Health.Level.WARN ? R.color.amber : R.color.mint);
    }
    private String pillText(Health.Level l) { return l == Health.Level.OK ? "OK" : l == Health.Level.WARN ? "WATCH" : "RISK"; }
    private int pillBg(Health.Level l) { return l == Health.Level.BAD ? R.drawable.pill_bad : l == Health.Level.WARN ? R.drawable.pill_warn : R.drawable.pill_ok; }
    private int pillInk(Health.Level l) { return l == Health.Level.BAD ? R.color.alert_ink : l == Health.Level.WARN ? R.color.amber_ink : R.color.mint_ink; }

    private TextView title(String s) {
        TextView t = new TextView(this);
        t.setText(s); t.setAllCaps(true); t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextColor(getColor(R.color.text)); t.setTextSize(22f); t.setLetterSpacing(0.06f);
        return t;
    }
    private TextView tag(String s) {
        TextView t = new TextView(this);
        t.setText(s); t.setTextColor(getColor(R.color.mint)); t.setTextSize(13f);
        t.setPadding(0, dp(4), 0, 0);
        return t;
    }
    private int dp(int d) { return (int) (d * getResources().getDisplayMetrics().density); }
}

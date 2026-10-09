package co.soclose.aegisforge;

import android.app.Activity;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Environment;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import co.soclose.aegisforge.clean.Cleaner;
import co.soclose.aegisforge.clean.DuplicateFinder;

/**
 * Duplicate finder: scan shared storage for identical files and, per group, keep
 * one copy and delete the rest. Honest sizes, guarded deletes (reuses Cleaner,
 * which re-validates each path). Needs All Files Access (grant it on the Cleaner
 * screen).
 */
public class DuplicatesActivity extends Activity {

    private LinearLayout col, list;
    private Button scan;
    private TextView status;
    private volatile boolean cancelled = false, busy = false;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(getColor(R.color.ground));
        col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(24), dp(24), dp(24), dp(24));
        scroll.addView(col);

        TextView t = new TextView(this);
        t.setText(getString(R.string.dupes_title)); t.setAllCaps(true);
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextColor(getColor(R.color.text)); t.setTextSize(22f); t.setLetterSpacing(0.06f);
        col.addView(t);
        TextView tg = new TextView(this);
        tg.setText(getString(R.string.dupes_tag)); tg.setTextColor(getColor(R.color.mint));
        tg.setTextSize(13f); tg.setPadding(0, dp(4), 0, dp(20));
        col.addView(tg);

        scan = new Button(this);
        scan.setText(getString(R.string.btn_find_dupes)); scan.setAllCaps(false);
        scan.setBackgroundTintList(getColorStateList(R.color.mint));
        scan.setTextColor(getColor(R.color.mint_ink)); scan.setTypeface(null, Typeface.BOLD);
        scan.setOnClickListener(v -> find());
        col.addView(scan);

        status = new TextView(this);
        status.setTextColor(getColor(R.color.text_2)); status.setTextSize(12f);
        status.setTypeface(Typeface.MONOSPACE); status.setPadding(0, dp(12), 0, 0);
        col.addView(status);

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        col.addView(list);

        setContentView(scroll);
    }

    @Override protected void onDestroy() { cancelled = true; super.onDestroy(); }

    private void find() {
        if (busy) return;
        busy = true; cancelled = false;
        list.removeAllViews();
        scan.setEnabled(false);
        status.setText(getString(R.string.scanning) + "...");
        final File root = Environment.getExternalStorageDirectory();
        new Thread(() -> {
            List<DuplicateFinder.Group> groups = DuplicateFinder.find(root, new DuplicateFinder.Progress() {
                long last = 0;
                public void onFile(String name) {
                    long now = System.currentTimeMillis();
                    if (now - last > 150) { last = now; runOnUiThread(() -> status.setText("Hashing: " + name)); }
                }
                public boolean cancelled() { return cancelled; }
            });
            runOnUiThread(() -> show(groups, root));
        }, "aegis-dupes").start();
    }

    private void show(List<DuplicateFinder.Group> groups, File root) {
        busy = false;
        scan.setEnabled(true);
        list.removeAllViews();
        if (groups.isEmpty()) { status.setText(getString(R.string.dupes_none)); return; }
        long totalReclaim = 0;
        for (DuplicateFinder.Group g : groups) totalReclaim += g.reclaimable();
        status.setText(groups.size() + " duplicate group(s), " + Health.humanBytes(totalReclaim) + " reclaimable");

        for (DuplicateFinder.Group g : groups) {
            LinearLayout box = new LinearLayout(this);
            box.setOrientation(LinearLayout.VERTICAL);
            box.setPadding(0, dp(12), 0, dp(12));

            TextView head = new TextView(this);
            head.setText(g.paths.size() + " copies  " + Health.humanBytes(g.size)
                    + " each  (free " + Health.humanBytes(g.reclaimable()) + ")");
            head.setTextColor(getColor(R.color.text)); head.setTextSize(13f);
            head.setTypeface(Typeface.MONOSPACE);
            box.addView(head);

            for (String p : g.paths) {
                TextView path = new TextView(this);
                path.setText(shorten(p, root));
                path.setTextColor(getColor(R.color.text_2)); path.setTextSize(11f);
                path.setMaxLines(1); path.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
                box.addView(path);
            }

            Button del = new Button(this);
            del.setText(getString(R.string.btn_del_extras)); del.setAllCaps(false);
            del.setBackgroundTintList(getColorStateList(R.color.surface));
            del.setTextColor(getColor(R.color.mint));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.topMargin = dp(6);
            del.setLayoutParams(lp);
            del.setOnClickListener(v -> deleteExtras(g, root, box));
            box.addView(del);

            View sep = new View(this);
            sep.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)));
            sep.setBackgroundColor(getColor(R.color.line));
            box.addView(sep);

            list.addView(box);
        }
    }

    private void deleteExtras(DuplicateFinder.Group g, File root, View box) {
        if (g.paths.size() < 2) return;
        List<String> extras = new ArrayList<>(g.paths.subList(1, g.paths.size()));  // keep the first
        new Thread(() -> {
            Cleaner.Outcome o = Cleaner.deleteAll(root, extras);
            runOnUiThread(() -> {
                Toast.makeText(this, "Freed " + Health.humanBytes(o.freedBytes), Toast.LENGTH_SHORT).show();
                box.setVisibility(View.GONE);
            });
        }, "aegis-dupdel").start();
    }

    private String shorten(String abs, File root) {
        String r = root.getAbsolutePath();
        return abs.startsWith(r) ? "." + abs.substring(r.length()) : abs;
    }

    private int dp(int d) { return (int) (d * getResources().getDisplayMetrics().density); }
}

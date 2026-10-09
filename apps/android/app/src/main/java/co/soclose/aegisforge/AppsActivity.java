package co.soclose.aegisforge;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * App manager + updater. Lists the user's apps biggest-first (by APK size, which
 * needs no special permission), and for each one offers Store (opens its store
 * page, where Update appears if available) and Uninstall (the system dialog).
 * Honest about the platform: an app cannot update or remove another app silently.
 */
public class AppsActivity extends Activity {

    private LinearLayout list;

    private static final class Row { String label, pkg; long size; }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(getColor(R.color.ground));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(24), dp(24), dp(24), dp(24));
        scroll.addView(col);

        col.addView(title(getString(R.string.apps_title)));
        col.addView(tag(getString(R.string.apps_tag)));
        col.addView(note(getString(R.string.updater_note)));

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(0, dp(12), 0, 0);
        col.addView(list);

        TextView loading = new TextView(this);
        loading.setText("Loading...");
        loading.setTextColor(getColor(R.color.text_2));
        list.addView(loading);

        setContentView(scroll);
        load();
    }

    private void load() {
        new Thread(() -> {
            PackageManager pm = getPackageManager();
            List<Row> rows = new ArrayList<>();
            for (ApplicationInfo ai : pm.getInstalledApplications(0)) {
                if (pm.getLaunchIntentForPackage(ai.packageName) == null) continue;  // user-facing only
                Row r = new Row();
                r.pkg = ai.packageName;
                r.label = String.valueOf(pm.getApplicationLabel(ai));
                try { r.size = new File(ai.sourceDir).length(); } catch (Exception e) { r.size = 0; }
                rows.add(r);
            }
            Collections.sort(rows, (a, b) -> Long.compare(b.size, a.size));
            runOnUiThread(() -> render(rows));
        }, "aegis-apps").start();
    }

    private void render(List<Row> rows) {
        list.removeAllViews();
        for (Row r : rows) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(0, dp(8), 0, dp(8));

            LinearLayout texts = new LinearLayout(this);
            texts.setOrientation(LinearLayout.VERTICAL);
            texts.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            TextView name = new TextView(this);
            name.setText(r.label);
            name.setTextColor(getColor(R.color.text));
            name.setTextSize(14f);
            TextView meta = new TextView(this);
            meta.setText(Health.humanBytes(r.size) + "  " + r.pkg);
            meta.setTextColor(getColor(R.color.text_2));
            meta.setTextSize(11f);
            meta.setTypeface(Typeface.MONOSPACE);
            texts.addView(name);
            texts.addView(meta);

            row.addView(texts);
            row.addView(smallButton(getString(R.string.btn_store), v -> openStore(r.pkg)));
            row.addView(smallButton(getString(R.string.btn_uninstall), v -> uninstall(r.pkg)));
            list.addView(row);
        }
    }

    private void openStore(String pkg) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=" + pkg)));
        } catch (Exception e) {
            startActivity(new Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://play.google.com/store/apps/details?id=" + pkg)));
        }
    }

    private void uninstall(String pkg) {
        startActivity(new Intent(Intent.ACTION_DELETE, Uri.parse("package:" + pkg)));
    }

    // ---- tiny view helpers ----

    private Button smallButton(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(12f);
        b.setBackgroundTintList(getColorStateList(R.color.surface));
        b.setTextColor(getColor(R.color.mint));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMarginStart(dp(6));
        b.setLayoutParams(lp);
        b.setOnClickListener(l);
        return b;
    }

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

    private TextView note(String s) {
        TextView t = new TextView(this);
        t.setText(s); t.setTextColor(getColor(R.color.text_2)); t.setTextSize(12f);
        t.setLineSpacing(0, 1.25f); t.setPadding(0, dp(16), 0, 0);
        return t;
    }

    private int dp(int d) { return (int) (d * getResources().getDisplayMetrics().density); }
}

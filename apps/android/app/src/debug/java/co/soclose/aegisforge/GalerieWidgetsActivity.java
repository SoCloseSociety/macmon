package co.soclose.aegisforge;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.RemoteViews;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * DEBUG-ONLY. A launcher-free gallery that renders the widget at every size by
 * applying the exact same RemoteViews the provider builds, into framed cells.
 * Used for deterministic screenshots on an emulator (screencap), proving each
 * size renders rather than failing silently in a real launcher picker. Declared
 * exported only in src/debug/AndroidManifest.xml, so it never ships in release.
 */
public class GalerieWidgetsActivity extends Activity {

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#05070A"));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(16), dp(16), dp(16), dp(16));
        scroll.addView(col);

        int id = 990000;
        addCell(col, "S  170x170", WidgetRender.Size.S, 170, 170, id++);
        addCell(col, "S-tall  170x300 (-> L card)", WidgetRender.Size.L, 170, 300, id++);
        addCell(col, "M  340x170", WidgetRender.Size.M, 340, 170, id++);
        addCell(col, "L  340x340", WidgetRender.Size.L, 340, 340, id++);

        setContentView(scroll);
    }

    private void addCell(LinearLayout col, String caption, WidgetRender.Size size,
                         int wdp, int hdp, int fakeId) {
        TextView cap = new TextView(this);
        cap.setText(caption);
        cap.setTextColor(Color.parseColor("#9FB0C2"));
        cap.setTextSize(12f);
        cap.setPadding(0, dp(14), 0, dp(6));
        col.addView(cap);

        FrameLayout cell = new FrameLayout(this);
        cell.setLayoutParams(new LinearLayout.LayoutParams(dp(wdp), dp(hdp)));
        RemoteViews rv = WidgetRender.render(this, size, fakeId);
        View v = rv.apply(this, cell);
        cell.addView(v, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER));
        col.addView(cell);
    }

    private int dp(int d) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, d,
                getResources().getDisplayMetrics());
    }
}

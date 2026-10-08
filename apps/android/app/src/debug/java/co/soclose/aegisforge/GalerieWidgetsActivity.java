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
 * DEBUG-ONLY. A launcher-free gallery that renders the widget at every size,
 * style and metric by applying the exact RemoteViews the providers build, into
 * framed cells. Used for deterministic screenshots on an emulator (screencap),
 * proving each combination renders. Exported only in src/debug/AndroidManifest.xml.
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
        // Health, the three styles, medium
        health(col, "Health M  Lunar", Styles.Style.LUNAR, 340, 170, id++);
        health(col, "Health M  Soft", Styles.Style.SOFT, 340, 170, id++);
        health(col, "Health M  Glass", Styles.Style.GLASS, 340, 170, id++);
        health(col, "Health L  Lunar", Styles.Style.LUNAR, 340, 300, id++);
        // Metric widgets, small
        metric(col, "Battery S  Lunar", Styles.Style.LUNAR, Styles.Metric.BATTERY, id++);
        metric(col, "Storage S  Lunar", Styles.Style.LUNAR, Styles.Metric.STORAGE, id++);
        metric(col, "Memory S  Soft", Styles.Style.SOFT, Styles.Metric.MEMORY, id++);
        metric(col, "Network S  Glass", Styles.Style.GLASS, Styles.Metric.NETWORK, id++);

        setContentView(scroll);
    }

    private void health(LinearLayout col, String cap, Styles.Style style, int w, int h, int id) {
        cell(col, cap, WidgetRender.render(this, size(w, h), style, Styles.Metric.HEALTH, id, HealthWidget.class), w, h);
    }

    private void metric(LinearLayout col, String cap, Styles.Style style, Styles.Metric m, int id) {
        cell(col, cap, WidgetRender.render(this, WidgetRender.Size.S, style, m, id, MetricWidget.class), 170, 170);
    }

    private WidgetRender.Size size(int w, int h) {
        if (h >= 200) return WidgetRender.Size.L;
        if (w >= 200) return WidgetRender.Size.M;
        return WidgetRender.Size.S;
    }

    private void cell(LinearLayout col, String caption, RemoteViews rv, int wdp, int hdp) {
        TextView cap = new TextView(this);
        cap.setText(caption);
        cap.setTextColor(Color.parseColor("#9FB0C2"));
        cap.setTextSize(12f);
        cap.setPadding(0, dp(14), 0, dp(6));
        col.addView(cap);

        FrameLayout frame = new FrameLayout(this);
        frame.setLayoutParams(new LinearLayout.LayoutParams(dp(wdp), dp(hdp)));
        View v = rv.apply(this, frame);
        frame.addView(v, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER));
        col.addView(frame);
    }

    private int dp(int d) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, d,
                getResources().getDisplayMetrics());
    }
}

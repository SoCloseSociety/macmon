package co.soclose.aegisforge;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProviderInfo;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;

/**
 * The placement screen: pick a style (Lunar / Soft / Glass) and, for a metric
 * widget, which metric. Shown by the launcher before the widget is added (the
 * provider declares android:configure). Result CANCELED until the user confirms,
 * so backing out adds nothing.
 */
public class ChoixWidgetActivity extends Activity {

    private int widgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private boolean isMetric = false;
    private Styles.Style style = Styles.Style.LUNAR;
    private Styles.Metric metric = Styles.Metric.BATTERY;

    private Button bLunar, bSoft, bGlass, bBat, bSto, bMem, bNet;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setResult(RESULT_CANCELED);

        Bundle extras = getIntent().getExtras();
        if (extras != null) {
            widgetId = extras.getInt(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        }
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return; }

        setContentView(R.layout.activity_config);

        AppWidgetProviderInfo info = AppWidgetManager.getInstance(this).getAppWidgetInfo(widgetId);
        isMetric = info != null && info.provider != null
                && MetricWidget.class.getName().equals(info.provider.getClassName());
        if (!isMetric) {
            metric = Styles.Metric.HEALTH;
            findViewById(R.id.metric_label).setVisibility(View.GONE);
            findViewById(R.id.metric_row1).setVisibility(View.GONE);
            findViewById(R.id.metric_row2).setVisibility(View.GONE);
        }

        bLunar = findViewById(R.id.btn_lunar);
        bSoft = findViewById(R.id.btn_soft);
        bGlass = findViewById(R.id.btn_glass);
        bBat = findViewById(R.id.btn_battery);
        bSto = findViewById(R.id.btn_storage);
        bMem = findViewById(R.id.btn_memory);
        bNet = findViewById(R.id.btn_network);

        bLunar.setOnClickListener(v -> { style = Styles.Style.LUNAR; mark(); });
        bSoft.setOnClickListener(v -> { style = Styles.Style.SOFT; mark(); });
        bGlass.setOnClickListener(v -> { style = Styles.Style.GLASS; mark(); });
        bBat.setOnClickListener(v -> { metric = Styles.Metric.BATTERY; mark(); });
        bSto.setOnClickListener(v -> { metric = Styles.Metric.STORAGE; mark(); });
        bMem.setOnClickListener(v -> { metric = Styles.Metric.MEMORY; mark(); });
        bNet.setOnClickListener(v -> { metric = Styles.Metric.NETWORK; mark(); });

        findViewById(R.id.btn_add).setOnClickListener(v -> add());
        mark();
    }

    private void mark() {
        sel(bLunar, style == Styles.Style.LUNAR);
        sel(bSoft, style == Styles.Style.SOFT);
        sel(bGlass, style == Styles.Style.GLASS);
        sel(bBat, metric == Styles.Metric.BATTERY);
        sel(bSto, metric == Styles.Metric.STORAGE);
        sel(bMem, metric == Styles.Metric.MEMORY);
        sel(bNet, metric == Styles.Metric.NETWORK);
    }

    private void sel(Button b, boolean on) {
        b.setTextColor(on ? getColor(R.color.mint) : getColor(R.color.text_2));
    }

    private void add() {
        Styles.save(this, widgetId, style, metric);
        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        mgr.updateAppWidget(widgetId, isMetric
                ? WidgetRender.buildMetric(this, mgr, widgetId)
                : WidgetRender.buildHealth(this, mgr, widgetId));
        setResult(RESULT_OK, new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId));
        finish();
    }
}

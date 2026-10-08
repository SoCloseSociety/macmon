package co.soclose.aegisforge;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/**
 * A single-metric widget: battery, storage, memory or network, picked at
 * placement on the config screen. Same card + style system as the health widget,
 * just one number big. Redraws on update, resize, and tap.
 */
public class MetricWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context ctx, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) mgr.updateAppWidget(id, WidgetRender.buildMetric(ctx, mgr, id));
    }

    @Override
    public void onAppWidgetOptionsChanged(Context ctx, AppWidgetManager mgr, int id, Bundle opts) {
        mgr.updateAppWidget(id, WidgetRender.buildMetric(ctx, mgr, id));
    }

    @Override
    public void onDeleted(Context ctx, int[] ids) {
        for (int id : ids) Styles.clear(ctx, id);
    }

    @Override
    public void onReceive(Context ctx, Intent intent) {
        super.onReceive(ctx, intent);
        if (WidgetRender.ACTION_REFRESH.equals(intent.getAction())) {
            AppWidgetManager mgr = AppWidgetManager.getInstance(ctx);
            int[] ids = intent.getIntArrayExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS);
            if (ids == null || ids.length == 0) {
                ids = mgr.getAppWidgetIds(new ComponentName(ctx, MetricWidget.class));
            }
            for (int id : ids) mgr.updateAppWidget(id, WidgetRender.buildMetric(ctx, mgr, id));
        }
    }
}

package co.soclose.aegisforge;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/**
 * The home-screen health widget. Redraws on the periodic update, when the user
 * resizes it (options change picks the S/M/L card), and when the card is tapped
 * (our REFRESH action). Each redraw takes a fresh on-device reading.
 */
public class HealthWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context ctx, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) mgr.updateAppWidget(id, WidgetRender.build(ctx, mgr, id));
    }

    @Override
    public void onAppWidgetOptionsChanged(Context ctx, AppWidgetManager mgr, int id, Bundle opts) {
        mgr.updateAppWidget(id, WidgetRender.build(ctx, mgr, id));
    }

    @Override
    public void onReceive(Context ctx, Intent intent) {
        super.onReceive(ctx, intent);
        if (WidgetRender.ACTION_REFRESH.equals(intent.getAction())) {
            AppWidgetManager mgr = AppWidgetManager.getInstance(ctx);
            int[] ids = intent.getIntArrayExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS);
            if (ids == null || ids.length == 0) {
                ids = mgr.getAppWidgetIds(new ComponentName(ctx, HealthWidget.class));
            }
            for (int id : ids) mgr.updateAppWidget(id, WidgetRender.build(ctx, mgr, id));
        }
    }
}

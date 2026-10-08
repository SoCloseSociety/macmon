package co.soclose.aegisforge;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.text.format.DateFormat;
import android.widget.RemoteViews;

import java.util.Date;
import java.util.List;

/**
 * Turns a {@link Snapshot} into the card the launcher draws, picking the S / M /
 * L layout from the widget's measured size. Every id it touches exists in all
 * three cards, so a call never references a missing view. Only RemoteViews calls
 * are used (no custom view): the launcher inflates it in its own process.
 */
public final class WidgetRender {
    private WidgetRender() {}

    public static final String ACTION_REFRESH = "co.soclose.aegisforge.REFRESH";

    /** Visible so a debug gallery can render each size without a real widget id. */
    public enum Size { S, M, L }

    public static RemoteViews build(Context ctx, AppWidgetManager mgr, int appWidgetId) {
        return render(ctx, sizeOf(mgr, appWidgetId), appWidgetId);
    }

    /** Build the card for an explicit size (the launcher path computes the size
     *  from the widget options; the gallery passes it directly). */
    public static RemoteViews render(Context ctx, Size size, int appWidgetId) {
        int layout = size == Size.S ? R.layout.widget_card_s
                : size == Size.L ? R.layout.widget_card_l : R.layout.widget_card_m;
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), layout);

        Snapshot snap = DeviceStats.read(ctx);
        Health.Verdict v = Health.verdict(snap);

        rv.setTextViewText(R.id.wg_title, "AEGISFORGE");
        rv.setTextViewText(R.id.wg_value, String.valueOf(Health.score(snap)));
        rv.setTextViewText(R.id.wg_sub, Health.subLabel(snap));

        // verdict pill: fill colour by verdict, a dark ink on it (measured AA)
        rv.setTextViewText(R.id.wg_verdict, Health.verdictText(v));
        int pill, ink;
        switch (v) {
            case OK:    pill = R.drawable.pill_ok;   ink = R.color.mint_ink;  break;
            case WATCH: pill = R.drawable.pill_warn; ink = R.color.amber_ink; break;
            default:    pill = R.drawable.pill_bad;  ink = R.color.alert_ink; break;
        }
        rv.setInt(R.id.wg_verdict, "setBackgroundResource", pill);
        rv.setTextColor(R.id.wg_verdict, ctx.getColor(ink));

        // lines: battery / storage / memory / network, up to what the size holds
        rv.removeAllViews(R.id.wg_lines);
        int maxRows = size == Size.S ? 0 : size == Size.M ? 3 : 5;
        if (maxRows > 0) {
            List<Health.Dim> dims = Health.dimensions(snap);
            int n = Math.min(maxRows, dims.size());
            for (int i = 0; i < n; i++) {
                Health.Dim d = dims.get(i);
                RemoteViews row = new RemoteViews(ctx.getPackageName(), R.layout.widget_row);
                row.setTextViewText(R.id.row_label, d.label);
                row.setTextViewText(R.id.row_value, d.value);
                row.setTextColor(R.id.row_value, ctx.getColor(levelColor(d.level)));
                rv.addView(R.id.wg_lines, row);
            }
        }

        rv.setTextViewText(R.id.wg_footer,
                "updated " + DateFormat.getTimeFormat(ctx).format(new Date(snap.takenAtMillis)));

        // tap the card to refresh now
        Intent intent = new Intent(ctx, HealthWidget.class);
        intent.setAction(ACTION_REFRESH);
        intent.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, new int[]{appWidgetId});
        PendingIntent pi = PendingIntent.getBroadcast(ctx, appWidgetId, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.wg_root, pi);
        return rv;
    }

    private static int levelColor(Health.Level l) {
        if (l == Health.Level.BAD) return R.color.alert;
        if (l == Health.Level.WARN) return R.color.amber;
        return R.color.text;
    }

    private static Size sizeOf(AppWidgetManager mgr, int appWidgetId) {
        try {
            Bundle opts = mgr.getAppWidgetOptions(appWidgetId);
            int w = opts.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0);
            int h = opts.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 0);
            if (h >= 200) return Size.L;
            if (w >= 200) return Size.M;
            if (w == 0 && h == 0) return Size.M;   // no options yet: the initial layout
            return Size.S;
        } catch (Throwable ignored) {
            return Size.M;
        }
    }
}

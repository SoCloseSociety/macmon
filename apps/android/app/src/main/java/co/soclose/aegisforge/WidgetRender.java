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
 * Turns a {@link Snapshot} into the card the launcher draws: the right S / M / L
 * layout for the measured size, the chosen style's colours applied to EVERY view
 * (not just the background), and either the whole-device health or a single
 * metric. Only RemoteViews calls are used, and every id it touches exists in all
 * three cards, so a call never references a missing view.
 */
public final class WidgetRender {
    private WidgetRender() {}

    public static final String ACTION_REFRESH = "co.soclose.aegisforge.REFRESH";

    /** Visible so a debug gallery can render each size without a real widget id. */
    public enum Size { S, M, L }

    public static RemoteViews buildHealth(Context ctx, AppWidgetManager mgr, int id) {
        return render(ctx, sizeOf(mgr, id), Styles.style(ctx, id), Styles.Metric.HEALTH, id, HealthWidget.class);
    }

    public static RemoteViews buildMetric(Context ctx, AppWidgetManager mgr, int id) {
        return render(ctx, sizeOf(mgr, id), Styles.style(ctx, id), Styles.metric(ctx, id), id, MetricWidget.class);
    }

    /** Build the card for explicit parameters (the gallery path; the launcher path
     *  reads size + prefs first). */
    public static RemoteViews render(Context ctx, Size size, Styles.Style style,
                                     Styles.Metric metric, int appWidgetId, Class<?> provider) {
        int layout = size == Size.S ? R.layout.widget_card_s
                : size == Size.L ? R.layout.widget_card_l : R.layout.widget_card_m;
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), layout);
        Styles.Palette pal = Styles.palette(ctx, style);
        rv.setInt(R.id.wg_root, "setBackgroundResource", pal.bgRes);

        Snapshot snap = DeviceStats.read(ctx);

        String title, value, sub;
        Health.Level level;
        boolean isHealth = metric == Styles.Metric.HEALTH;
        if (isHealth) {
            Health.Verdict v = Health.verdict(snap);
            title = "AEGISFORGE";
            value = String.valueOf(Health.score(snap));
            sub = Health.subLabel(snap);
            level = v == Health.Verdict.OK ? Health.Level.OK
                    : v == Health.Verdict.WATCH ? Health.Level.WARN : Health.Level.BAD;
        } else {
            Metric m = metricView(snap, metric);
            title = m.label; value = m.big; sub = m.detail; level = m.level;
        }

        rv.setTextViewText(R.id.wg_title, title);
        rv.setTextColor(R.id.wg_title, pal.text2);
        rv.setTextViewText(R.id.wg_value, value);
        rv.setTextColor(R.id.wg_value, pal.text);
        rv.setTextViewText(R.id.wg_sub, sub);
        rv.setTextColor(R.id.wg_sub, pal.text2);

        // verdict pill: solid level colour with a dark ink (reads on dark and light cards)
        rv.setTextViewText(R.id.wg_verdict, pillText(level));
        int pill, ink;
        switch (level) {
            case OK:   pill = R.drawable.pill_ok;   ink = R.color.mint_ink;  break;
            case WARN: pill = R.drawable.pill_warn; ink = R.color.amber_ink; break;
            default:   pill = R.drawable.pill_bad;  ink = R.color.alert_ink; break;
        }
        rv.setInt(R.id.wg_verdict, "setBackgroundResource", pill);
        rv.setTextColor(R.id.wg_verdict, ctx.getColor(ink));

        // lines: only the health overview carries them
        rv.removeAllViews(R.id.wg_lines);
        int maxRows = (isHealth && size != Size.S) ? (size == Size.M ? 3 : 5) : 0;
        if (maxRows > 0) {
            List<Health.Dim> dims = Health.dimensions(snap);
            int n = Math.min(maxRows, dims.size());
            for (int i = 0; i < n; i++) {
                Health.Dim d = dims.get(i);
                RemoteViews row = new RemoteViews(ctx.getPackageName(), R.layout.widget_row);
                row.setTextViewText(R.id.row_label, d.label);
                row.setTextColor(R.id.row_label, pal.text2);
                row.setTextViewText(R.id.row_value, d.value);
                // on the light style, dim status colours fail contrast -> use the text colour
                row.setTextColor(R.id.row_value, pal.light ? pal.text : ctx.getColor(levelColor(d.level)));
                rv.addView(R.id.wg_lines, row);
            }
        }

        rv.setTextViewText(R.id.wg_footer,
                "updated " + DateFormat.getTimeFormat(ctx).format(new Date(snap.takenAtMillis)));
        rv.setTextColor(R.id.wg_footer, pal.text2);

        Intent intent = new Intent(ctx, provider).setAction(ACTION_REFRESH);
        intent.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, new int[]{appWidgetId});
        PendingIntent pi = PendingIntent.getBroadcast(ctx, appWidgetId, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.wg_root, pi);
        return rv;
    }

    /** One metric as a big value + detail + level (reuses the tested Health logic). */
    static final class Metric {
        final String label, big, detail;
        final Health.Level level;
        Metric(String label, String big, String detail, Health.Level level) {
            this.label = label; this.big = big; this.detail = detail; this.level = level;
        }
    }

    static Metric metricView(Snapshot s, Styles.Metric m) {
        switch (m) {
            case BATTERY:
                return new Metric("BATTERY",
                        s.batteryPct < 0 ? "n/a" : s.batteryPct + "%",
                        s.batteryPct < 0 ? "" : (s.charging ? "charging" : "on battery"),
                        Health.battery(s));
            case STORAGE:
                return new Metric("STORAGE",
                        Health.humanBytes(s.storageFreeBytes),
                        s.storageTotalBytes < 0 ? "" : Health.humanBytes(s.storageTotalBytes) + " total",
                        Health.storage(s));
            case MEMORY:
                return new Metric("MEMORY",
                        s.memAvailRatio() < 0 ? "n/a" : Health.pct(s.memAvailRatio()),
                        s.memTotalBytes < 0 ? ""
                                : "free, " + Health.humanBytes(s.memAvailBytes) + " of " + Health.humanBytes(s.memTotalBytes),
                        Health.memory(s));
            case NETWORK:
                return new Metric("NETWORK", s.netType,
                        "Offline".equals(s.netType) ? "no connection" : "connected",
                        Health.network(s));
            default:
                return new Metric("AEGISFORGE", String.valueOf(Health.score(s)), Health.subLabel(s), Health.Level.OK);
        }
    }

    private static String pillText(Health.Level l) {
        return l == Health.Level.OK ? "OK" : l == Health.Level.WARN ? "WATCH" : "RISK";
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
            if (w == 0 && h == 0) return Size.M;
            return Size.S;
        } catch (Throwable ignored) {
            return Size.M;
        }
    }
}

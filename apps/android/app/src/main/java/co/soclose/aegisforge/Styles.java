package co.soclose.aegisforge;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * The per-widget choices made at placement: a visual style (Lunar / Soft / Glass)
 * and, for a metric widget, which metric it shows. Stored per widget id in
 * SharedPreferences. The palette resolves a style to the colours applied to
 * EVERY view, not just the background (the light-style lesson: white-on-cream is
 * invisible).
 */
public final class Styles {
    private Styles() {}

    public enum Style { LUNAR, SOFT, GLASS }
    public enum Metric { HEALTH, BATTERY, STORAGE, MEMORY, NETWORK }

    private static final String PREFS = "aegis_widgets";

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static void save(Context c, int id, Style s, Metric m) {
        prefs(c).edit().putString("style_" + id, s.name()).putString("metric_" + id, m.name()).apply();
    }

    public static Style style(Context c, int id) {
        try { return Style.valueOf(prefs(c).getString("style_" + id, Style.LUNAR.name())); }
        catch (Exception e) { return Style.LUNAR; }
    }

    public static Metric metric(Context c, int id) {
        try { return Metric.valueOf(prefs(c).getString("metric_" + id, Metric.HEALTH.name())); }
        catch (Exception e) { return Metric.HEALTH; }
    }

    public static void clear(Context c, int id) {
        prefs(c).edit().remove("style_" + id).remove("metric_" + id).apply();
    }

    /** Colours for a style, resolved to ints, plus the root background drawable. */
    public static final class Palette {
        public final int bgRes, text, text2, line;
        public final boolean light;
        Palette(int bgRes, int text, int text2, int line, boolean light) {
            this.bgRes = bgRes; this.text = text; this.text2 = text2; this.line = line; this.light = light;
        }
    }

    public static Palette palette(Context c, Style s) {
        switch (s) {
            case SOFT:
                return new Palette(R.drawable.widget_bg_soft,
                        c.getColor(R.color.doux_texte), c.getColor(R.color.doux_texte2),
                        c.getColor(R.color.doux_ligne), true);
            case GLASS:
                return new Palette(R.drawable.widget_bg_glass,
                        c.getColor(R.color.text), c.getColor(R.color.text_2),
                        c.getColor(R.color.verre_ligne), false);
            default:
                return new Palette(R.drawable.widget_bg,
                        c.getColor(R.color.text), c.getColor(R.color.text_2),
                        c.getColor(R.color.line), false);
        }
    }
}

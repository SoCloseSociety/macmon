package co.soclose.aegisforge;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Button;
import android.widget.TextView;

import java.util.Locale;

/**
 * A plain info screen: what the widget shows and how to place it, a live text
 * snapshot of this phone's health, and a button that refreshes any placed
 * widgets. Extends the framework Activity so the APK keeps zero third-party deps.
 */
public class MainActivity extends Activity {

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_main);
        Button refresh = findViewById(R.id.main_refresh);
        refresh.setOnClickListener(v -> { refreshWidgets(); showSnapshot(); });
        findViewById(R.id.btn_clean_open).setOnClickListener(
                v -> startActivity(new Intent(this, CleanerActivity.class)));
        showSnapshot();
    }

    private void showSnapshot() {
        Snapshot s = DeviceStats.read(this);
        StringBuilder sb = new StringBuilder();
        sb.append("score   ").append(Health.score(s)).append(" / 100   (")
                .append(Health.verdictText(Health.verdict(s))).append(")\n\n");
        for (Health.Dim d : Health.dimensions(s)) {
            sb.append(String.format(Locale.US, "%-9s %s%n", d.label, d.value));
        }
        TextView t = findViewById(R.id.main_snapshot);
        t.setText(sb.toString());
    }

    private void refreshWidgets() {
        sendBroadcast(new Intent(this, HealthWidget.class).setAction(WidgetRender.ACTION_REFRESH));
    }
}

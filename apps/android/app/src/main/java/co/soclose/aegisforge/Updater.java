package co.soclose.aegisforge;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.os.Build;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import co.soclose.aegisforge.clean.DuplicateFinder;

/**
 * Self-update, the ONLY network feature in the app. It fetches a small JSON
 * manifest over HTTPS, compares its versionCode to the installed one, and (on the
 * user's tap) downloads the release APK, checks its SHA-256, and hands it to the
 * system installer. Everything else in the app stays fully offline.
 *
 * Integrity: HTTPS + the published SHA-256, and Android itself refuses to install
 * an APK signed by a different key over the installed one, so an update can only
 * be a genuine AegisForge build.
 */
public final class Updater {
    private Updater() {}

    /** The "latest version" pointer, served from the repo (raw), pointing at a
     *  GitHub Release asset for the APK. */
    public static final String MANIFEST_URL =
            "https://raw.githubusercontent.com/SoCloseSociety/macmon/main/apps/android/update/version.json";

    private static final int CONNECT_MS = 10000, READ_MS = 20000;
    private static final long MAX_APK = 80L * 1024 * 1024;

    public static final class Info {
        public int versionCode;
        public String versionName, apkUrl, sha256;
    }

    static Info parse(String json) throws JSONException {
        JSONObject o = new JSONObject(json);
        Info i = new Info();
        i.versionCode = o.getInt("versionCode");
        i.versionName = o.optString("versionName", "");
        i.apkUrl = o.getString("apkUrl");
        i.sha256 = o.optString("sha256", "").toLowerCase();
        return i;
    }

    public static boolean isNewer(int manifestCode, int installedCode) {
        return manifestCode > installedCode;
    }

    public static int installedVersionCode(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                    ? (int) pi.getLongVersionCode() : pi.versionCode;
        } catch (Exception e) {
            return Integer.MAX_VALUE;  // unknown -> never claim an update
        }
    }

    public interface Progress { void onBytes(long got, long total); boolean cancelled(); }

    /** Fetch the manifest text. Caller runs this off the main thread. */
    public static String fetchManifest() throws IOException {
        HttpURLConnection c = open(MANIFEST_URL);
        try {
            if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode());
            byte[] buf = new byte[8192];
            StringBuilder sb = new StringBuilder();
            try (InputStream in = c.getInputStream()) {
                int n; int total = 0;
                while ((n = in.read(buf)) > 0) {
                    total += n;
                    if (total > 256 * 1024) throw new IOException("manifest too large");
                    sb.append(new String(buf, 0, n, StandardCharsets.UTF_8));
                }
            }
            return sb.toString();
        } finally {
            c.disconnect();
        }
    }

    /** Download the APK to dest. Returns true on success (size-bounded). */
    public static boolean download(String apkUrl, File dest, Progress p) {
        HttpURLConnection c = null;
        try {
            c = open(apkUrl);
            if (c.getResponseCode() != 200) return false;
            long total = c.getContentLengthLong();
            byte[] buf = new byte[1 << 16];
            long got = 0;
            try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(dest)) {
                int n;
                while ((n = in.read(buf)) > 0) {
                    if (p != null && p.cancelled()) return false;
                    out.write(buf, 0, n);
                    got += n;
                    if (got > MAX_APK) return false;
                    if (p != null) p.onBytes(got, total);
                }
            }
            return true;
        } catch (IOException e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    public static boolean sha256Matches(File f, String expected) {
        if (expected == null || expected.isEmpty()) return true;  // no hash published -> rely on signature
        String actual = DuplicateFinder.sha256(f);
        return actual != null && actual.equalsIgnoreCase(expected);
    }

    /** Install via the framework PackageInstaller session (no FileProvider, no
     *  third-party dependency). Commit triggers the system's install prompt, since
     *  AegisForge is not a privileged installer. Needs REQUEST_INSTALL_PACKAGES. */
    public static void install(Context c, File apk) throws IOException {
        PackageInstaller pi = c.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params =
                new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        int sessionId = pi.createSession(params);
        PackageInstaller.Session session = pi.openSession(sessionId);
        try (OutputStream out = session.openWrite("aegis.apk", 0, apk.length());
             InputStream in = new FileInputStream(apk)) {
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            session.fsync(out);
        }
        Intent intent = new Intent(c, MainActivity.class).setAction("co.soclose.aegisforge.INSTALL_RESULT");
        PendingIntent pending = PendingIntent.getActivity(c, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
        session.commit(pending.getIntentSender());
        session.close();
    }

    private static HttpURLConnection open(String urlStr) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(urlStr).openConnection();
        c.setConnectTimeout(CONNECT_MS);
        c.setReadTimeout(READ_MS);
        c.setInstanceFollowRedirects(true);
        c.setRequestProperty("User-Agent", "AegisForge-Updater");
        return c;
    }
}

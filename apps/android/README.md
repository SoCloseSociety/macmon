# AegisForge for Android

A home-screen **widget** that shows this phone's own health -- battery, storage,
memory, network and screen-lock state -- in the Sentinel House "Lunar" look. It is
the mobile companion to the `macmon` / AegisForge system monitor.

- **On-device only.** No account, no server, no network call, no tracking. Every
  number is read from the phone itself. The one permission (`ACCESS_NETWORK_STATE`)
  is "normal" and reports only the connection *type* (Wi-Fi / Cellular / ...).
- **Honest.** An unknown metric shows `n/a` and is never scored as a failure.
- **Zero third-party runtime dependencies.** The APK is a few hundred KB.

The design is **re-implemented** from the Sentinel House widget kit's documented
patterns (tokens, sizes, the RemoteViews allow-list, honest states). No code from
the (private) Sentinel House repo is copied here.

## What it shows

Two widgets, both in the Sentinel House look:

- **Health** -- a card with a verdict pill (**OK / WATCH / RISK**), a 0--100 health
  score, the worst dimension in one line, and -- at medium/large sizes -- a line per
  dimension, with an `updated HH:MM` footer.
- **Metric** -- one metric big: **battery**, **storage**, **memory** or **network**.

Three sizes: small (2x2), medium (4x2), large (4x3+). Tap a card to refresh now.

When you place a widget you pick a **style** -- **Lunar** (dark, the default),
**Soft** (light), **Glass** (translucent over the wallpaper) -- and, for a metric
widget, which metric. The style colours apply to every element, not just the
background.

## Health Check (the one-tap dashboard)

The home screen leads with **Health Check**: one scan that folds everything into
three CCleaner-style cards with an overall score.

- **Space** -- how much junk is cleanable, with a Clean action.
- **Speed** -- device memory / storage / battery pressure (informational; the OS
  manages memory, so there is no fake "boost").
- **Security** -- screen-lock posture, with a Fix action that opens the system
  security settings.

Each card shows an OK / WATCH / RISK verdict. Only categories Android actually lets
an app assess are shown; there is no pretend "privacy scan".

## Cleaner (CCleaner-class, honest)

Beyond the widgets, the app has a **Cleaner**: a one-tap funnel that scans shared
storage for junk, groups it (temp & logs, thumbnail caches, leftover APKs, empty
folders, crash logs) with real sizes, and removes what you select. It also wipes
free space (overwrites it so deleted files are harder to recover).

Honest about Android's limits (no root), the same walls CCleaner's own Android app
hits:

- It CANNOT clear another app's cache (the OS blocks that since Android 6), update
  or uninstall other apps silently, or truly "free RAM". It never pretends to.
- It CANNOT reach another app's sandbox (`Android/data`, `Android/obb`) -- the OS
  blocks it, and the scanner refuses those paths anyway.
- Secure-wipe on flash storage raises the bar but is not a guarantee (wear
  levelling); the UI says so.

The cleaner needs **All Files Access** to scan shared storage; the widgets need
none. Nothing leaves the device. Every number reported is what was actually freed,
never an estimate. The delete/scan/shred logic is covered by off-device tests.

### Also

- **Smart Cleaning** -- an opt-in background scan (JobScheduler) that notifies you
  when junk builds past a threshold. It only notifies; it never deletes on its own.
- **Apps & updates** -- installed apps sorted by size, with Store (opens the app's
  store page, where Update appears) and Uninstall (the system dialog). Android
  updates apps through the store, so the app never updates another app itself.
- **Duplicates** -- finds identical files (size, then SHA-256), keeps one per group
  and frees the rest, with guarded deletes.

## Build

Toolchain (the fleet's proven recipe): **AGP 8.9.3**, **Gradle 8.11.1** (the wrapper
fetches it), **JDK 17**, Android SDK with platform 35 + build-tools.

```bash
cd apps/android
./build.sh
# -> dist/aegisforge-<versionCode>.apk
```

`build.sh` picks a JDK 17 automatically (Android Studio's bundled JBR, or set
`JAVA17=/path/to/jdk17`), writes a git-ignored `local.properties` pointing at your
SDK (`ANDROID_HOME` overrides), runs the off-device unit tests, then assembles the
release APK.

Run just the logic tests: `./gradlew testReleaseUnitTest`.

## Signing

The release is signed with the **local debug keystore** (`~/.android/debug.keystore`).
That keystore is:

- **stable** across releases, so an already-installed phone can update with
  `adb install -r` instead of hitting `INSTALL_FAILED_UPDATE_INCOMPATIBLE`;
- **never committed** -- a keystore (or its password) in a public repo is a signing
  key exposed for the life of the git history. It stays under `~/.android/`.

AegisForge uses a **distinct application id** (`co.soclose.aegisforge`), so it never
conflicts with the fleet's own Android apps regardless of their keystore. If you cut
a real public release, create your own stable keystore outside the repo and point
`signingConfigs` at it; keep using the same one for every later release.

## Install on a phone (e.g. the Galaxy Fold)

```bash
adb install -r dist/aegisforge-<versionCode>.apk
```

Then long-press the home screen, choose **Widgets**, find **AegisForge**, and drop
the size you want. (Installing on a personal device is the owner's own action.)

## Layout

```
apps/android/
  settings.gradle  build.gradle  gradle.properties  version.properties  build.sh
  app/
    build.gradle  proguard-rules.pro
    src/main/
      AndroidManifest.xml
      java/co/soclose/aegisforge/
        Snapshot.java      # plain data holder (no Android), unit-testable
        Health.java        # scoring + verdict + formatting (pure logic)
        DeviceStats.java   # on-device reads -> Snapshot
        WidgetRender.java  # Snapshot -> RemoteViews, by size
        HealthWidget.java  # AppWidgetProvider (update / resize / tap-refresh)
        MainActivity.java  # info screen + live snapshot + refresh
      res/ (layout, drawable, xml, values)
    src/test/java/.../HealthTest.java   # off-device scoring tests
```

A portability guard in the Python suite (`tests/test_android_widget.py`) checks
that every widget layout uses only RemoteViews-inflatable views and that the app
carries no em dash.

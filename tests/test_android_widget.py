"""Portability guard for the AegisForge Android widget (apps/android).

A home-screen widget is drawn by RemoteViews, which only inflates a closed list
of view classes. A view outside that list (a bare <View>, a ScrollView) fails
SILENTLY on the user's launcher ("Can't load widget"), never at compile time. So
these tests read the widget layouts and assert they use only permitted views,
that every resource a widget references exists, and that the app carries no em
dash. Pure file reads: no Android toolchain, runs on every OS in CI.

Re-implemented from the pattern documented in Sentinel House's KIT-WIDGETS.md;
no code is copied from that (private) repo.
"""
import re
from pathlib import Path

import pytest

ANDROID = Path(__file__).resolve().parents[1] / "apps" / "android" / "app" / "src" / "main"
RES = ANDROID / "res"

# The views RemoteViews can inflate (Android docs, "App Widgets: layouts").
ALLOWED = {
    "FrameLayout", "LinearLayout", "RelativeLayout", "GridLayout", "AnalogClock", "Button",
    "Chronometer", "ImageButton", "ImageView", "ProgressBar", "TextView", "ViewFlipper",
    "ListView", "GridView", "StackView", "AdapterViewFlipper", "ViewStub", "TextClock",
    "CheckBox", "Switch", "RadioButton", "RadioGroup",
}

pytestmark = pytest.mark.skipif(not ANDROID.exists(), reason="android app not present")


def _widget_layouts():
    """Layouts that are widget templates: those named by xml/widget_*.xml and those
    the widget code draws (R.layout.widget_*). Activity layouts are excluded."""
    names = set()
    xml_dir = RES / "xml"
    if xml_dir.exists():
        for f in xml_dir.glob("widget_*.xml"):
            names |= set(re.findall(r"@layout/([a-z0-9_]+)", f.read_text(encoding="utf-8")))
    java_dir = ANDROID / "java"
    if java_dir.exists():
        for f in java_dir.rglob("Widget*.java"):
            names |= set(re.findall(r"R\.layout\.(widget_[a-z0-9_]+)", f.read_text(encoding="utf-8")))
    return sorted(names)


def test_widget_layouts_use_only_remoteviews_safe_views():
    names = _widget_layouts()
    assert len(names) >= 4, names
    for name in names:
        text = (RES / "layout" / f"{name}.xml").read_text(encoding="utf-8")
        tags = set(re.findall(r"<([A-Za-z][A-Za-z0-9_.]*)[\s>/]", text)) - {"?xml"}
        assert tags - ALLOWED == set(), f"{name}: view rejected by RemoteViews: {tags - ALLOWED}"


def test_widget_provider_references_resolve():
    for f in sorted((RES / "xml").glob("widget_*.xml")):
        text = f.read_text(encoding="utf-8")
        for d in re.findall(r"@drawable/([a-z0-9_]+)", text):
            assert list(RES.glob(f"drawable*/{d}.*")), f"{f.name}: drawable {d} missing"
        for lay in re.findall(r"@layout/([a-z0-9_]+)", text):
            assert (RES / "layout" / f"{lay}.xml").exists(), f"{f.name}: layout {lay} missing"


def test_each_manifest_receiver_has_its_provider_xml():
    manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
    providers = set(re.findall(r'android:resource="@xml/(widget_[a-z_]+)"', manifest))
    assert len(providers) >= 1
    for p in providers:
        assert (RES / "xml" / f"{p}.xml").exists(), f"provider xml {p} missing"


def test_no_em_dash_anywhere_in_the_app():
    files = list((ANDROID / "java").rglob("*.java")) + list(RES.rglob("*.xml"))
    offenders = [str(f) for f in files if "—" in f.read_text(encoding="utf-8")]
    assert offenders == [], f"em dash found (use --): {offenders}"

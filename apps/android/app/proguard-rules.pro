# minify is OFF (see app/build.gradle). These rules are here for the day it is
# turned on: keep the widget provider and activities, which the framework
# instantiates by name from the manifest (reflection).
-keep class co.soclose.aegisforge.** { *; }

#!/usr/bin/env python3
"""Apply a conservative Android 10 compatibility patch to liamelui/zomdroid."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one occurrence in {path}, found {count}. "
            "The upstream source changed; refusing to patch blindly."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_import(text: str, old_import: str, new_import: str) -> str:
    if new_import in text:
        return text
    if old_import not in text:
        raise RuntimeError(f"Cannot locate import anchor: {old_import}")
    return text.replace(old_import, old_import + new_import, 1)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: patch_android10.py <zomdroid-repo-dir> <workflow-file>")
        return 2

    root = Path(sys.argv[1]).resolve()
    workflow_source = Path(sys.argv[2]).resolve()
    required = [
        root / "app/build.gradle.kts",
        root / "app/src/main/java/com/zomdroid/LauncherActivity.java",
        root / "app/src/main/java/com/zomdroid/GameActivity.java",
        root / "app/src/main/java/com/zomdroid/InstallerService.java",
        root / "app/src/main/java/com/zomdroid/fragments/LauncherFragment.java",
        root / "app/src/main/java/com/zomdroid/LauncherPreferences.java",
        root / "app/src/main/java/com/zomdroid/C.java",
        root / "app/src/main/AndroidManifest.xml",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("Not a complete Zomdroid source tree; missing:\n" + "\n".join(missing))
    if not workflow_source.is_file():
        raise RuntimeError(f"Workflow file not found: {workflow_source}")

    # The Android 10 platform is API 29. Keep compileSdk 35 for current build tools.
    gradle = required[0]
    replace_once(gradle, "minSdk = 30", "minSdk = 29")
    replace_once(gradle, "targetSdk = 35", "targetSdk = 29")
    # Use a distinct package ID so this debug-signed build can coexist with an
    # official Zomdroid APK instead of requiring its removal or data migration.
    replace_once(gradle, 'applicationId = "com.zomdroid"', 'applicationId = "com.zomdroid.android10"')
    replace_once(required[6], 'public static final String STORAGE_PROVIDER_AUTHORITY = "com.zomdroid.STORAGE_PROVIDER_AUTHORITY";',
                 'public static final String STORAGE_PROVIDER_AUTHORITY = "com.zomdroid.android10.STORAGE_PROVIDER_AUTHORITY";')
    replace_once(required[7], 'android:authorities="com.zomdroid.STORAGE_PROVIDER_AUTHORITY"',
                 'android:authorities="com.zomdroid.android10.STORAGE_PROVIDER_AUTHORITY"')
    replace_once(gradle, "versionCode = 3", "versionCode = 4")
    replace_once(gradle, 'versionName = "1.2.0"', 'versionName = "1.2.0-android10"')

    # LauncherActivity: replace API-30 framework insets with AndroidX compatibility APIs.
    p = required[1]
    s = p.read_text(encoding="utf-8")
    s = s.replace("import android.graphics.Insets;\n", "")
    s = s.replace("import android.view.WindowInsets;\n", "")
    s = add_import(s, "import androidx.appcompat.app.AppCompatActivity;\n",
                   "import androidx.core.graphics.Insets;\n"
                   "import androidx.core.view.ViewCompat;\n"
                   "import androidx.core.view.WindowInsetsCompat;\n")
    old_listener = '''        binding.appbarLayout.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @NonNull
            @Override
            public WindowInsets onApplyWindowInsets(@NonNull View v, @NonNull WindowInsets windowInsets) {
                Insets insets = windowInsets.getInsets(WindowInsets.Type.systemBars());
                v.setPadding(
                        v.getPaddingLeft(),
                        insets.top,
                        v.getPaddingRight(),
                        //v.getPaddingBottom()
                        insets.bottom
                );

                return windowInsets;
            }
        });'''
    new_listener = '''        ViewCompat.setOnApplyWindowInsetsListener(binding.appbarLayout, (v, windowInsets) -> {
            Insets insets = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars());
            v.setPadding(
                    v.getPaddingLeft(),
                    insets.top,
                    v.getPaddingRight(),
                    //v.getPaddingBottom()
                    insets.bottom
            );
            return windowInsets;
        });'''
    if old_listener not in s:
        raise RuntimeError("Could not find LauncherActivity insets listener; refusing partial patch")
    s = s.replace(old_listener, new_listener, 1)
    p.write_text(s, encoding="utf-8")

    # GameActivity: use WindowCompat/WindowInsetsControllerCompat instead of API-30 methods.
    p = required[2]
    s = p.read_text(encoding="utf-8")
    s = s.replace("import android.view.WindowInsets;\n", "")
    s = s.replace("import android.view.WindowInsetsController;\n", "")
    s = add_import(s, "import androidx.appcompat.app.AppCompatActivity;\n",
                   "import androidx.core.view.WindowCompat;\n"
                   "import androidx.core.view.WindowInsetsCompat;\n"
                   "import androidx.core.view.WindowInsetsControllerCompat;\n")
    old_fullscreen = '''        getWindow().setDecorFitsSystemWindows(false);
        final WindowInsetsController controller = getWindow().getInsetsController();
        if (controller != null) {
            controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
            controller.setSystemBarsBehavior(
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        }'''
    new_fullscreen = '''        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), binding.getRoot());
        controller.hide(WindowInsetsCompat.Type.systemBars());
        controller.setSystemBarsBehavior(
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);'''
    if old_fullscreen not in s:
        raise RuntimeError("Could not find GameActivity fullscreen block; refusing partial patch")
    s = s.replace(old_fullscreen, new_fullscreen, 1)
    p.write_text(s, encoding="utf-8")

    # LauncherFragment: the same API-30 insets call is used in the main list.
    p = required[4]
    s = p.read_text(encoding="utf-8")
    s = s.replace("import android.graphics.Insets;\n", "")
    s = s.replace("import android.view.WindowInsets;\n", "")
    s = add_import(s, "import androidx.core.view.MenuHost;\n",
                   "import androidx.core.graphics.Insets;\n"
                   "import androidx.core.view.ViewCompat;\n"
                   "import androidx.core.view.WindowInsetsCompat;\n")
    old_fragment_listener = '''        binding.gameInstancesRv.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @NonNull
            @Override
            public WindowInsets onApplyWindowInsets(@NonNull View v, @NonNull WindowInsets windowInsets) {
                Insets insets = windowInsets.getInsets(WindowInsets.Type.systemBars());
                v.setPadding(
                        v.getPaddingLeft(),
                        v.getPaddingTop(),
                        v.getPaddingRight(),
                        insets.bottom
                );

                return windowInsets;
            }
        });'''
    new_fragment_listener = '''        ViewCompat.setOnApplyWindowInsetsListener(binding.gameInstancesRv, (v, windowInsets) -> {
            Insets insets = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars());
            v.setPadding(
                    v.getPaddingLeft(),
                    v.getPaddingTop(),
                    v.getPaddingRight(),
                    insets.bottom
            );
            return windowInsets;
        });'''
    if old_fragment_listener not in s:
        raise RuntimeError("Could not find LauncherFragment insets listener; refusing partial patch")
    s = s.replace(old_fragment_listener, new_fragment_listener, 1)
    p.write_text(s, encoding="utf-8")

    # InstallerService: Android 10 has no POST_NOTIFICATIONS runtime permission.
    p = required[3]
    s = p.read_text(encoding="utf-8")
    s = add_import(s, "import android.os.Binder;\n", "import android.os.Build;\n")
    old_permission_check = '''            if (ActivityCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                return;
            }'''
    new_permission_check = '''            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                    && ActivityCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                return;
            }'''
    if old_permission_check not in s:
        raise RuntimeError("Could not find InstallerService notification permission check")
    s = s.replace(old_permission_check, new_permission_check, 1)
    p.write_text(s, encoding="utf-8")

    # java.lang.Math.clamp was added after the Android 10 runtime API surface.
    # Replace every three-argument clamp(x, min, max) with max(min, min(x, max)).
    java_root = root / "app/src/main/java"
    clamp_pattern = re.compile(r"Math\.clamp\(([^,\n]+),\s*([^,\n]+),\s*([^\)\n]+)\)")
    patched_clamps = 0
    for java_file in java_root.rglob("*.java"):
        source = java_file.read_text(encoding="utf-8")
        source, count = clamp_pattern.subn(r"Math.max(\2, Math.min(\1, \3))", source)
        if count:
            java_file.write_text(source, encoding="utf-8")
            patched_clamps += count
    if patched_clamps < 6:
        raise RuntimeError(f"Expected at least 6 Math.clamp calls, patched {patched_clamps}; refusing incomplete patch")

    workflow_dest = root / ".github/workflows/android10-build.yml"
    workflow_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workflow_source, workflow_dest)

    print("Android 10 compatibility patch applied.")
    print(f"Math.clamp replacements: {patched_clamps}")
    print("minSdk=29, targetSdk=29, compileSdk remains 35.")
    print("Build will run in GitHub Actions; this script does not build an APK on the phone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PATCH ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

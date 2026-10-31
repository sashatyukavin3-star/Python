#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

PATCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HOME/zomdroid-android10-build"

printf '\n=== Zomdroid Android 10 build preparation ===\n'
printf 'This creates a separate fork/branch. The original liamelui repository is not changed.\n\n'

if ! command -v pkg >/dev/null 2>&1; then
  echo "This script must be run inside Termux."
  exit 1
fi

pkg update -y
pkg install -y git gh python

if ! gh auth status >/dev/null 2>&1; then
  echo "\nGitHub sign-in is required to create your own fork and start its build."
  gh auth login --hostname github.com --git-protocol https --web
fi

gh auth status >/dev/null

if [ -e "$WORK" ]; then
  echo "Directory already exists: $WORK"
  echo "To avoid overwriting anything, move or rename it and run this script again."
  exit 1
fi

echo "\n[1/5] Downloading source and native submodules..."
git clone --recurse-submodules https://github.com/liamelui/zomdroid.git "$WORK"

if [ ! -f "$WORK/app/src/main/cpp/box64/CMakeLists.txt" ] || [ ! -f "$WORK/app/src/main/cpp/glfw/CMakeLists.txt" ]; then
  echo "Submodules were not downloaded completely. No build was started."
  exit 1
fi

echo "\n[2/5] Applying Android 10 compatibility changes..."
python "$PATCH_DIR/patch_android10.py" "$WORK" "$PATCH_DIR/android10-build.yml"

cd "$WORK"
git switch -c android10-compat
git add app/build.gradle.kts \
  app/src/main/java/com/zomdroid/LauncherActivity.java \
  app/src/main/java/com/zomdroid/GameActivity.java \
  app/src/main/java/com/zomdroid/InstallerService.java \
  app/src/main/java/com/zomdroid/LauncherPreferences.java \
  app/src/main/java/com/zomdroid/C.java \
  app/src/main/AndroidManifest.xml \
  app/src/main/java/com/zomdroid/fragments/LauncherFragment.java \
  app/src/main/java/com/zomdroid/input \
  .github/workflows/android10-build.yml
git commit -m "Add Android 10 compatibility build"

echo "\n[3/5] Creating a separate fork under your GitHub account..."
GH_USER="$(gh api user --jq .login)"
FORK_REPO="$GH_USER/zomdroid-android10"
if gh repo view "$FORK_REPO" >/dev/null 2>&1; then
  echo "Fork repository already exists: $FORK_REPO"
  echo "This script will not overwrite it. Rename/remove the repository or adapt the script."
  exit 1
fi
gh api --method POST /repos/liamelui/zomdroid/forks -f name=zomdroid-android10 >/dev/null

for attempt in $(seq 1 24); do
  if gh repo view "$FORK_REPO" >/dev/null 2>&1; then break; fi
  sleep 5
done
gh repo view "$FORK_REPO" >/dev/null 2>&1 || {
  echo "GitHub did not make the fork available in time: $FORK_REPO"
  exit 1
}
git remote add fork "https://github.com/$FORK_REPO.git"
git push -u fork android10-compat

echo "\n[4/5] Enable GitHub Actions for the new fork."
echo "Open: https://github.com/$FORK_REPO/actions"
echo "Press the GitHub button to enable workflows for this fork."
read -r -p "After enabling Actions, return here and press Enter: " _

echo "\nStarting the cloud build..."
gh workflow run android10-build.yml --repo "$FORK_REPO" --ref android10-compat

RUN_ID=""
for attempt in $(seq 1 36); do
  RUN_ID="$(gh run list --repo "$FORK_REPO" --workflow android10-build.yml --branch android10-compat --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)"
  [ -n "$RUN_ID" ] && break
  sleep 5
done

if [ -z "$RUN_ID" ]; then
  echo "The workflow run was not found. Open https://github.com/$FORK_REPO/actions and check whether Actions are enabled."
  exit 1
fi

echo "\n[5/5] Waiting for GitHub Actions build $RUN_ID..."
gh run watch "$RUN_ID" --repo "$FORK_REPO" --exit-status

OUT="$WORK/build-output"
mkdir -p "$OUT"
gh run download "$RUN_ID" --repo "$FORK_REPO" --name zomdroid-android10-apk --dir "$OUT"
APK="$(find "$OUT" -type f -name '*.apk' -print -quit)"
if [ -z "$APK" ]; then
  echo "Build completed but APK was not found in the downloaded artifact: $OUT"
  exit 1
fi

if [ -d "$HOME/storage/downloads" ]; then
  DEST="$HOME/storage/downloads/zomdroid-android10.apk"
else
  DEST="$HOME/zomdroid-android10.apk"
fi
cp "$APK" "$DEST"
echo "\nAPK saved to: $DEST"
echo "Package has minSdk 29 (Android 10). It is a debug-signed test build."
echo "The app's installation does not guarantee that Project Zomboid will run on this device's GPU/RAM."

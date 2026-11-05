# Test: `android` runner

You're in a session on the `android` runner type: a Linux VM with the Android SDK and
an emulator (`emulator-5554`) running inside it on KVM, thrown away after the session.
Build and test a small app the way Android work would, and write a report. If a check
fails, record it and carry on. Time every step. Build the app in a scratch directory
(`$AGENT_SCRATCH/runner-test` (`$HOME/runner-test` if that isn't set)), and write outputs to
`tests/sessions/results/android/<UTC timestamp>/` in the repo.

## 1. Facts

The shared facts (see [linux.md](linux.md), step 1), plus: `kvm-ok`, `java -version`,
the installed SDK platforms, build tools and emulator version (Google's `android`
CLI, or `sdkmanager --list_installed` if that's what's there),
`agent-emulator status`, and `adb devices`.

## 2. Emulator

Run `agent-emulator wait` and time it. Pass if `adb shell getprop sys.boot_completed`
returns `1`. Record the API level and ABI (`adb shell getprop ro.build.version.sdk`,
`ro.product.cpu.abi`).

## 3. App

Create a minimal app from scratch: Kotlin, Jetpack Compose, one activity with a text
showing a count and a button that increments it. Use the newest stable Android Gradle
Plugin, Kotlin and Compose BOM (look them up, don't guess), the Gradle wrapper, and
compile and target SDK set to the installed platform. Keep the counting logic in a
small class so it can be unit-tested.

- `./gradlew testDebugUnitTest`: a unit test of the counter class.
- `./gradlew assembleDebug`: record the APK size.
- `./gradlew connectedDebugAndroidTest`: a Compose UI test that taps the button three
  times and asserts the text shows 3.

Record cold build time (first `assembleDebug`) and warm (a second run after touching
one source file).

## 4. On the device

Install the APK, launch it, tap the button twice with `adb shell input tap`, and
capture `adb exec-out screencap -p > counter.png`. Pass if the PNG isn't blank and
`adb logcat -d` has no `FATAL EXCEPTION` for the app's package.

## 5. Git and report

Commit the results directory (report, `counter.png`, the test result XML) on the
session's branch and push; no pull request. Write `report.md` in the format from
[README.md](README.md) and end the session with the check table and the cold and warm
build times.

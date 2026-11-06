# android runner test, 20261106T052947Z

| Check | Result | Value |
| --- | --- | --- |
| Host: Ubuntu 24.04.5, x86_64, 8 vCPU, 31 GiB RAM, 96 GB disk | pass | `facts-android.txt` |
| Claude Code version vs published latest | pass | 2.1.318 = 2.1.318 |
| `sudo -n true` | pass | works (VM, passwordless sudo) |
| `$DISPLAY` | pass | `:99` |
| `kvm-ok` | pass | /dev/kvm usable |
| `java -version` | pass | OpenJDK 21.0.12.1 (`$JAVA_HOME` set) |
| SDK platforms / build tools | pass | platform `android-37.0`, build-tools `37.0.0`, NDK `30.0.16248370` |
| Emulator | pass | 37.2.13.0; `agent-emulator status`: running, KVM accel |
| `adb devices` | pass | `emulator-5554 device` |
| Env var names: no unexpected credential names | pass | `env-names.txt`; `CLAUDE_*`, `CCR_*`, `SELF_HOSTED_RUNNER_*`, `GIT_CONFIG_*` noted separately, not recorded by value |
| Session notice lines (cost, idle timeout) | skipped | `AGENT_COST_USD_PER_HOUR` and `AGENT_RETIRE_AT` unset; I did not verify the opening notice text myself |
| `agent-emulator wait` | pass | 0.25 s; device was already booted at session start, so this is not a cold boot |
| `sys.boot_completed` | pass | `1` |
| API level / ABI | pass | API 37 (Android 17), `x86_64` |
| `./gradlew testDebugUnitTest` | pass | `CounterTest`: 1 test, 0 failures |
| `./gradlew assembleDebug` | pass | APK 11,662,490 bytes (11.1 MiB) |
| `./gradlew connectedDebugAndroidTest` | pass, after a fix | 1 test, 0 failures on API 37 (first attempt failed, see Notes) |
| Install, launch, tap button twice, screenshot | pass | UI dump shows `Count: 2`; `counter.png` 1080x2400, 450 distinct colors |
| `adb logcat` FATAL for app package | pass | 0 `FATAL EXCEPTION` entries |
| Git push of results | pending | see end of session |

## Timings

Wall clock, from the session's own runs.

| Step | Time | Notes |
| --- | --- | --- |
| `agent-emulator wait` | 0.25 s | already booted |
| Gradle wrapper fetch (9.8.2) | 57 s | includes distribution download |
| `testDebugUnitTest` (first run) | 45 s | includes dependency download |
| **Cold `assembleDebug`** | **54 s** | `clean` first; Gradle and dependency caches warm from the unit-test run |
| **Warm `assembleDebug`** | **19 s** | one-line source edit to `Counter.kt`, then reverted |
| `connectedDebugAndroidTest` (failing, Espresso 3.5.0) | 36 s | |
| `connectedDebugAndroidTest` (passing, Espresso 3.7.0) | 38 s | |
| `adb install -r` | < 1 s | |

Cold build caveat: a true cold build (empty Gradle caches) needs a fresh session. This number excludes dependency download.

## Versions used (looked up, not from memory)

- AGP 9.5.1, Kotlin 2.4.21 (with `org.jetbrains.kotlin.plugin.compose`), Compose BOM 2026.09.00, Gradle 9.8.2
- compileSdk and targetSdk 37, minSdk 24, JVM target 17
- core-ktx 1.19.1, activity-compose 1.13.0, test ext junit 1.3.0, espresso-core 3.7.0, JUnit 4.13.2

Maven metadata's `<release>` tags pointed at pre-releases (AGP 9.6.0-alpha03, Kotlin 2.5.0-Beta2), so I took the highest non-pre-release version from each version list.

## Missing from the image

- `AGENT_OUTPUTS` and `AGENT_DATASETS` are unset, so the >10 MB rule has nowhere to send the APK. I left the APK out of the repo (the copy in `results/` was removed). The build output stays in `/scratch/runner-test/counter-app/app/build/`.
- `AGENT_COST_USD_PER_HOUR` and `AGENT_RETIRE_AT` are unset on this deploy.

## Notes

- **Finding, test stack (not the image):** `connectedDebugAndroidTest` failed on API 37 with `NoSuchMethodException: android.hardware.input.InputManager.getInstance`. Compose's `ui-test-junit4` pulled in `espresso-core` 3.5.0 transitively, and it reflects on an InputManager method that API 37 no longer has. Pinning `espresso-core:3.7.0` in `androidTestImplementation` fixed it. The failing XML and log are in `attempt1-espresso-3.5.0/`. Any Android project on this runner that uses Compose UI tests will hit this unless it pins Espresso. Worth a line in the image docs or the project template.
- **My own error, corrected:** my first tap script parsed the button bounds wrong and tapped the status bar at y=0. No app effect. The second pass tapped the center of `[457,1206][623,1259]` at (540, 1232) twice, and the count went to 2.
- `touch` on a source file does not trigger a rebuild under Gradle's content hashing, so my first "warm" run was all up-to-date. Re-ran with a real edit.
- App source is in `/scratch/runner-test/counter-app/`, not committed. This results directory holds the report, the screenshot, the UI dumps, the logs, and the test XML.
- `xml/` holds the passing unit test and passing connected test XML. `attempt1-espresso-3.5.0/` holds the failing run.

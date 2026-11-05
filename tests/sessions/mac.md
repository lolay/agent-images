# Test: `mac` runner

You're in a session on the `mac` runner type: a macOS VM with Xcode and a logged-in
GUI session, thrown away after the session. It's for Mac development, iOS included.
Build and test the way an Apple developer would, and write a report. If a check
fails, record it and carry on. Time every step. Work in `$HOME/runner-test`, and
write outputs to `tests/sessions/results/mac/<UTC timestamp>/` in the repo.

## 1. Facts

The shared facts (see [linux.md](linux.md), step 1, with `sw_vers` and
`sysctl -n machdep.cpu.brand_string hw.memsize` in place of the Linux commands),
plus: `xcodebuild -version`, `xcrun simctl list runtimes`, available iPhone
simulators, and `xcodegen`, `swiftlint`, `swiftformat`, `xcbeautify` versions.

## 2. Swift package on macOS

`swift package init --type executable`, add a small library target with one function
and a Swift Testing test, `swift build -c release` and `swift test`. Pass if tests
pass. Record the build time.

## 3. iOS app

With `xcodegen`, create a SwiftUI iOS app: one screen with a count and a button that
increments it, a unit test target for the counting logic, and a UI test target that
taps the button three times and asserts the label shows 3. Run `swiftlint` on it.

- `xcodebuild test` on an iPhone simulator for both test targets, piped through
  `xcbeautify`. Pass if all tests pass.
- Boot the simulator, install and launch the app, and capture
  `xcrun simctl io booted screenshot app.png`. Pass if it isn't blank.
- Screenshot the whole Mac display (`screencapture -x display.png`) with the
  Simulator window showing, which proves the GUI session is there.

Record cold and warm build times.

## 4. Git and report

Commit the results directory (report, PNGs, the `.xcresult` summary as JSON from
`xcrun xcresulttool`) on the session's branch and push; no pull request. Write
`report.md` in the format from [README.md](README.md) and end the session with the
check table and the cold and warm build times.

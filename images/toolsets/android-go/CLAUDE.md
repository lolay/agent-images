- gomobile and gobind, for Go libraries on Android, against `$ANDROID_NDK_HOME`:
  `gomobile bind -target=android/arm64,android/amd64 -androidapi 24 -o lib.aar .`
  (the module needs `go get golang.org/x/mobile/bind`). gomobile is experimental
  upstream, with no tagged releases.

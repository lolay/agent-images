# shellcheck shell=sh
# The go toolset (images/toolsets/go): what `go install` installs (gomobile, on
# the Android image) goes to GOPATH's bin, ~/go/bin by default, at the back of
# PATH. No `go env GOPATH`: every shell sources this, and the default is Go's.
case ":$PATH:" in
*":${GOPATH:-$HOME/go}/bin:"*) ;;
*) PATH="$PATH:${GOPATH:-$HOME/go}/bin" ;;
esac

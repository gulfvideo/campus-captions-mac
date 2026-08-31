#!/bin/bash
# Double-click this in Finder to start Campus Captions on macOS.
#
# If macOS refuses to open it ("cannot be opened because it is from an
# unidentified developer"), the folder was downloaded rather than cloned with
# git. Right-click the file and choose Open, once; or run:
#     xattr -dr com.apple.quarantine "$(dirname "$0")"

cd "$(dirname "$0")" || exit 1

# tomllib needs 3.11+. Prefer a specific new version, fall back to plain python3.
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo
    echo "  Python 3.11 or newer is not installed on this Mac."
    echo
    echo "  The Python that ships with macOS is too old. Install a current one:"
    echo
    echo "      from https://www.python.org/downloads/   (double-click installer)"
    echo "      or, if you use Homebrew:  brew install python@3.12"
    echo
    echo "  Then run this file again."
    echo
    read -n 1 -s -r -p "Press any key to close "
    echo
    exit 1
fi

"$PY" launcher/launch.py

echo
read -n 1 -s -r -p "Press any key to close this window "
echo

#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$PROJECT_ROOT/dist}"
VERSION="$(PYTHONPATH="$PROJECT_ROOT/src" python3 -c 'from zellno_trader import __version__; print(__version__)')"
PACKAGE_NAME="zellno-trader-account-tool"
BUILD_ROOT="$(mktemp -d)"
PACKAGE_ROOT="$BUILD_ROOT/${PACKAGE_NAME}_${VERSION}_all"

cleanup() {
    rm -rf -- "$BUILD_ROOT"
}
trap cleanup EXIT

mkdir -p \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/$PACKAGE_NAME" \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME"

tar \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "$PROJECT_ROOT/src" \
    -cf - zellno_trader |
    tar -C "$PACKAGE_ROOT/usr/lib/$PACKAGE_NAME" -xf -
cp -- "$PROJECT_ROOT/LICENSE" \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/copyright"
cp -- "$PROJECT_ROOT/README.md" \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/README.md"

cat >"$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10)
Maintainer: Noob Open Source
Description: Safe administration and economic auditing for TraderPlus accounts
 External command-line tool for inspecting, auditing and safely preparing or
 deploying changes to TraderPlus account files.
EOF

cat >"$PACKAGE_ROOT/usr/bin/zellno-trader" <<'EOF'
#!/bin/sh
PYTHONPATH=/usr/lib/zellno-trader-account-tool exec python3 -m zellno_trader "$@"
EOF
chmod 0755 "$PACKAGE_ROOT/usr/bin/zellno-trader"

find "$PACKAGE_ROOT" -type d -exec chmod 0755 {} +
find "$PACKAGE_ROOT" -type f ! -path '*/usr/bin/zellno-trader' -exec chmod 0644 {} +

mkdir -p -- "$OUTPUT_DIR"
dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" \
    "$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_all.deb"

echo "$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_all.deb"

#!/usr/bin/env bash
# Encrypted build state for the PUBLIC repo: the release asset is unreadable without STATE_KEY (a GitHub secret).
#   scripts/state.sh pull   # download + decrypt into data/state and data/raw (no-op if there is none yet)
#   scripts/state.sh push   # encrypt + upload
set -euo pipefail
cd "$(dirname "$0")/.."
: "${STATE_KEY:?STATE_KEY missing}"
case "${1:-}" in
  pull)
    mkdir -p data/state data/raw
    if gh release download state -p 'state.enc' -D /tmp --clobber 2>/dev/null; then
      openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:STATE_KEY -in /tmp/state.enc | tar xz
      echo "state restored"
    else
      echo "no previous state"
    fi ;;
  push)
    tar cz data/state data/raw $([ -d data/weekly ] && echo data/weekly) | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY -out /tmp/state.enc
    gh release view state >/dev/null 2>&1 || gh release create state --title "build state (encrypted)" --notes "Encrypted catalogue state between builds. Not a software release." --latest=false
    gh release upload state /tmp/state.enc --clobber
    echo "state saved ($(du -h /tmp/state.enc | cut -f1))" ;;
  *) echo "usage: $0 pull|push"; exit 2 ;;
esac

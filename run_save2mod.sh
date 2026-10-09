#!/usr/bin/env sh
# Launch the CK3 -> EU5 converter GUI
cd "$(dirname "$0")" && exec python3 -m save2mod "$@"

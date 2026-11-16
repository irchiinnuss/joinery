#!/usr/bin/env bash
# Run a command in the Godot check worker. Godot runs headless here, so unlike
# the tool workers no virtual display is started.
#
# Native Linux bind mounts belong to the invoking host UID, which has no
# writable image home: give it a private disposable one (worker_identity.py).
set -euo pipefail

if [[ "${JOINERY_EPHEMERAL_HOME:-}" == "1" ]]; then
  mkdir -p "${HOME}"
  chmod 700 "${HOME}"
fi

exec "$@"

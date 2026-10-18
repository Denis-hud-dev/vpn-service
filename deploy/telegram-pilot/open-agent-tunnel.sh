#!/bin/sh
# Review-only local tunnel. It does not call the agent or change a VPN user.
set -eu
: "${PILOT_SSH_TARGET:?Set the existing JustHost SSH target}"
: "${PILOT_SSH_IDENTITY_FILE:?Set the private SSH identity path}"
test -r "$PILOT_SSH_IDENTITY_FILE" || { echo 'Pilot SSH identity unavailable' >&2; exit 1; }
exec ssh -N -T \
  -L 127.0.0.1:19444:127.0.0.1:19443 \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -i "$PILOT_SSH_IDENTITY_FILE" "$PILOT_SSH_TARGET"

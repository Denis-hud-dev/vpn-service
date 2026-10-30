#!/bin/sh
# Pause ONLY new backup automation. Preserve all archives, keys and app data.
set -eu
test "$(id -u)" = 0
for unit in kudavpn-backup.timer kudavpn-backup-retry.timer kudavpn-backup-verify.timer kudavpn-backup-seal.timer kudavpn-backup-health.timer kudavpn-backup-lab-cleanup.timer; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl disable --now "$unit"
    fi
done
printf '%s\n' 'Backup timers paused. Existing backups/credentials and working services preserved.'
printf '%s\n' 'An already running read-only backup may finish; no database restore was performed.'

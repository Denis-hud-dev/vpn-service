#!/usr/bin/env bash
# Only the new website/exporter. Preserve DB/config for diagnosis; never restore
# a canonical DB or touch protected units, ports, firewall, users or subscriptions.
set -euo pipefail
[[ $EUID = 0 ]]
backup=/var/backups/kudavpn-website-review/20261026
[[ -s "$backup/baseline.json" && -s "$backup/baseline.py" ]]
if [[ "${CHECK_ONLY:-0}" = 1 ]]; then
 python3 "$backup/baseline.py" verify "$backup/baseline.json"
 echo rollback_preflight=passed
 exit 0
fi
for unit in kudavpn-website-review.service kudavpn-website-export.timer; do
 if systemctl list-unit-files "$unit" --no-legend | awk '{print $1}' | grep -Fxq "$unit"; then
  systemctl disable --now "$unit"
 fi
done
if systemctl is-active --quiet kudavpn-website-export.service; then systemctl stop kudavpn-website-export.service; fi
[[ -z "$(ss -ltnH 'sport = :20443')" ]]
python3 "$backup/baseline.py" verify "$backup/baseline.json"
echo rollback=passed_new_listener_closed_protected_services_unchanged

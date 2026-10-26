#!/usr/bin/env bash
# One reviewed JustHost test contour only. No migrations or firewall changes.
set -euo pipefail
umask 077
revision=${1:?commit required}
checksum=${2:?archive SHA256 required}
[[ "$revision" =~ ^[a-f0-9]{40}$ && "$checksum" =~ ^[a-f0-9]{64}$ ]]
base=/opt/kudavpn-telegram-pilot
old=$base/app-tariff-199-3a74b6f-bot
release=$base/app-checkout-${revision:0:7}
compat=$base/app-checkout-${revision:0:7}-rollback
backup=$base/backups/checkout-${revision:0:7}
archive=/root/kuda-checkout-$revision.tar.gz
bot=kudavpn-telegram-pilot-bot.service
checkout=kudavpn-test-checkout.service
dropins=/etc/systemd/system/$bot.d
rollback=/root/kuda-checkout-rollback-${revision:0:7}.sh
switched=0
failed() {
 rc=$?
 trap - ERR
 if [[ "$switched" = 1 ]]; then
  if ! "$rollback"; then echo 'rollback=FAILED operator_required' >&2; fi
 fi
 echo 'checkout_deploy=failed' >&2
 exit "$rc"
}
trap failed ERR
[[ $EUID = 0 ]]
[[ "$(systemctl show -P WorkingDirectory "$bot")" = "$old" ]]
systemctl is-active --quiet "$bot"
systemctl is-active --quiet kudavpn-telegram-pilot-feed.service
[[ -z "$(ss -ltnH 'sport = :18543')" ]]
[[ ! -e /etc/systemd/system/$checkout && ! -e "$release" && ! -e "$backup" ]]
printf '%s  %s\n' "$checksum" "$archive" | sha256sum -c - >/dev/null
install -d -m 0700 "$backup"
cp -a /etc/systemd/system/$bot "$backup/bot.unit"
cp -a "$dropins" "$backup/dropins"
python3 - "$bot" "$backup" <<'PY'
import pathlib,re,subprocess,sys,shutil
files=subprocess.check_output(['systemctl','show','-P','EnvironmentFiles',sys.argv[1]],text=True)
for item in re.findall(r'(/[^ ;]+)',files):
 p=pathlib.Path(item)
 if p.is_file():
  target=pathlib.Path(sys.argv[2])/'env'/p.name
  target.parent.mkdir(mode=0o700,exist_ok=True)
  shutil.copy2(p,target); target.chmod(0o600)
PY
runuser -u postgres -- pg_dump -Fc kudavpn_telegram_pilot > "$backup/test-db.dump"
pg_restore --list "$backup/test-db.dump" > /dev/null
ss -ltnpH | awk '$4 ~ /:(443|8443|9443|10443|18443)$/ {print $4,$6}' | sort > "$backup/listeners.before"
# Preserve exact protected Xray configuration digests without displaying them.
python3 - "$backup" <<'PY'
import pathlib,subprocess,re,json,hashlib,sys
rows=subprocess.check_output(['ss','-ltnpH'],text=True).splitlines(); state={}
for row in rows:
 cols=row.split()
 if len(cols)<5 or not re.search(r':(443|8443|9443|10443)$',cols[3]): continue
 pid=re.search(r'pid=(\d+)',row).group(1)
 args=pathlib.Path('/proc/'+pid+'/cmdline').read_bytes().decode().split('\0')
 for i,arg in enumerate(args[:-1]):
  if arg in ('-config','-c','--config'):
   p=pathlib.Path(args[i+1]); state[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
assert len(state)>=3
(pathlib.Path(sys.argv[1])/'xray-config-digests.json').write_text(json.dumps(state))
PY
install -d -m 0755 "$release"
tar -xzf "$archive" -C "$release"
cp -a "$old" "$compat"
# Rollback keeps price-version recognition and the prohibition of external
# checkout in Telegram, even after a new-version payment has been confirmed.
for file in bot/services/__init__.py bot/services/subscriptions.py bot/handlers/subscription.py bot/screens.py; do
 install -m 0644 "$release/$file" "$compat/$file"
done
/opt/kudavpn-telegram-pilot/venv/bin/python -m compileall -q "$release/bot" "$compat/bot"
if ! id kudavpn_trial_checkout >/dev/null 2>&1; then
 useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin kudavpn_trial_checkout
fi
install -d -m 0700 /etc/kudavpn-telegram-pilot/checkout
python3 - "$bot" <<'PY'
import pathlib,subprocess,json
pid=subprocess.check_output(['systemctl','show','-P','MainPID','kudavpn-telegram-pilot-bot.service'],text=True).strip()
e=dict(x.split('=',1) for x in pathlib.Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in x)
assert e['DATABASE_URL'].rsplit('/',1)[-1]=='kudavpn_telegram_pilot'
assert pathlib.Path(e['PILOT_YOOKASSA_TEST_SHOP_ID_FILE']).read_text().strip()=='1503208'
assert pathlib.Path(e['PILOT_YOOKASSA_TEST_SECRET_FILE']).read_text().strip().startswith('test_')
credentials='/run/credentials/kudavpn-test-checkout.service/'
values={key:e[key] for key in ['DATABASE_URL','HAPP_LINK_SECRET','PILOT_TRIAL_OWNER_ID','SUBSCRIPTION_PUBLIC_URL']}
values.update(KUDAVPN_CHECKOUT_MODE='test-owner-only',CHECKOUT_BIND_HOST='0.0.0.0',CHECKOUT_PORT='18543',
 CHECKOUT_PUBLIC_ORIGIN='https://r1276248.s2578.vm.mydomain.zone:18543',
 CHECKOUT_SESSION_FILE='/var/lib/kudavpn-test-checkout/sessions.sqlite',
 PILOT_YOOKASSA_TEST_SHOP_ID_FILE=credentials+'shop',PILOT_YOOKASSA_TEST_SECRET_FILE=credentials+'secret',
 CHECKOUT_TLS_CERT=credentials+'tls.crt',CHECKOUT_TLS_KEY=credentials+'tls.key',PYTHONDONTWRITEBYTECODE='1')
p=pathlib.Path('/etc/kudavpn-telegram-pilot/checkout/checkout.env')
p.write_text(''.join(key+'='+json.dumps(value)+'\n' for key,value in values.items())); p.chmod(0o600)
PY
sed "s|@RELEASE@|$release|g" "$release/deploy/test-checkout/kudavpn-test-checkout.service.template" > /etc/systemd/system/$checkout
chmod 0644 /etc/systemd/system/$checkout
cat > "$rollback" <<ROLLBACK
#!/usr/bin/env bash
set -euo pipefail
backup='$backup'
compat='$compat'
bot='$bot'
checkout='$checkout'
[[ -d "\$compat" && -s "\$backup/test-db.dump" ]]
cmp -s "\$backup/bot.unit" /etc/systemd/system/\$bot
pg_restore --list "\$backup/test-db.dump" > /dev/null
if [[ "\${CHECK_ONLY:-0}" = 1 ]]; then echo rollback_preflight=passed; exit 0; fi
systemctl disable --now "\$checkout"
rm -f '$dropins/97-test-checkout.conf'
printf '[Service]\\nWorkingDirectory=%s\\n' "\$compat" > '$dropins/98-checkout-rollback.conf'
systemctl daemon-reload
systemctl restart "\$bot"
for attempt in \$(seq 1 40); do
 if systemctl is-active --quiet "\$bot" && curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:18081/ready 2>/dev/null; then echo rollback=ready; exit 0; fi
 sleep 1
done
exit 1
ROLLBACK
chmod 0700 "$rollback"
bash -n "$rollback"
CHECK_ONLY=1 "$rollback"
systemd-analyze verify /etc/systemd/system/$checkout 2> "$backup/unit-verify.log"
switched=1
systemctl daemon-reload
systemctl enable --now "$checkout"
ready=0
for attempt in $(seq 1 40); do
 if systemctl is-active --quiet "$checkout" && curl -fsS --max-time 2 --resolve r1276248.s2578.vm.mydomain.zone:18543:127.0.0.1 -o /dev/null https://r1276248.s2578.vm.mydomain.zone:18543/ready 2>/dev/null; then ready=1; break; fi
 sleep 1
done
[[ "$ready" = 1 ]]
printf '[Service]\nWorkingDirectory=%s\n' "$release" > "$dropins/97-test-checkout.conf"
systemctl daemon-reload
systemctl restart "$bot"
ready=0
for attempt in $(seq 1 40); do
 if systemctl is-active --quiet "$bot" && curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:18081/ready 2>/dev/null; then ready=1; break; fi
 sleep 1
done
[[ "$ready" = 1 ]]
ss -ltnpH | awk '$4 ~ /:(443|8443|9443|10443|18443)$/ {print $4,$6}' | sort > "$backup/listeners.after"
cmp -s "$backup/listeners.before" "$backup/listeners.after"
python3 - "$backup" <<'PY'
import pathlib,json,hashlib,sys
state=json.loads((pathlib.Path(sys.argv[1])/'xray-config-digests.json').read_text())
assert all(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()==digest for p,digest in state.items())
print('xray_configurations=unchanged')
PY
systemctl is-active --quiet kudavpn-telegram-pilot-feed.service
PYTHONPATH="$release" /opt/kudavpn-telegram-pilot/venv/bin/python /root/kuda-status-private-check.py
systemctl show "$checkout" "$bot" -p Id -p ActiveState -p NRestarts -p WorkingDirectory
echo checkout_deploy=passed

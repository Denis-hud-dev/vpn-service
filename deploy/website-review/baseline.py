"""Private pre/post manifest; never print paths, configuration or credentials."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

UNITS=('xray.service','kudavpn-xray-alt8443.service','kudavpn-pilot-node.service',
       'kudavpn-pilot-two-node.service','kudavpn-telegram-pilot-bot.service',
       'kudavpn-telegram-pilot-feed.service','kudavpn-test-checkout.service')


def collect():
    result={'units':{},'files':{}}
    for unit in UNITS:
        raw=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','MainPID','-p','FragmentPath','-p','DropInPaths'],text=True)
        values=dict(row.split('=',1) for row in raw.splitlines() if '=' in row)
        if values['ActiveState']!='active' or int(values['MainPID'])<2:raise ValueError('Protected service unavailable')
        result['units'][unit]={'pid':values['MainPID'],'state':values['ActiveState']}
        files=[values['FragmentPath'],*values['DropInPaths'].split()]
        if unit in UNITS[:4]:
            args=Path('/proc/'+values['MainPID']+'/cmdline').read_bytes().decode().split('\0')
            files += [args[i+1] for i,x in enumerate(args[:-1]) if x in {'-config','-c','--config'}]
        for item in files:
            p=Path(item)
            if p.is_file():result['files'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    # No protected listener may be moved or replaced.
    rows=subprocess.check_output(['ss','-ltnpH'],text=True).splitlines()
    result['listeners']=sorted(row.split()[3]+' '+re.search(r'pid=(\d+)',row).group(1)
        for row in rows if re.search(r':(443|8443|9443|10443|18443|18543)$',row.split()[3]))
    for row in rows:
        if not re.search(r':(443|8443|9443|10443)$',row.split()[3]):continue
        pid=re.search(r'pid=(\d+)',row).group(1)
        args=Path('/proc/'+pid+'/cmdline').read_bytes().decode().split('\0')
        for i,arg in enumerate(args[:-1]):
            if arg in {'-config','-c','--config'}:
                p=Path(args[i+1])
                result['files'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    if len(result['listeners'])<6:raise ValueError('Protected listener unavailable')
    return result


def main():
    try:
        path=Path(sys.argv[2]);current=collect()
        if sys.argv[1]=='save':
            if path.exists():raise ValueError('Manifest already exists')
            path.write_text(json.dumps(current));path.chmod(0o600)
        elif sys.argv[1]=='verify':
            if current!=json.loads(path.read_text()):raise ValueError('Protected state changed')
        else:raise ValueError('Invalid mode')
        print('protected_baseline='+sys.argv[1]+'_passed')
    except Exception:
        raise SystemExit('Protected baseline check failed; details withheld') from None


if __name__=='__main__':main()

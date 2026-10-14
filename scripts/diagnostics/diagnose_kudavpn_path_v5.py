#!/usr/bin/python3
"""One read-only, synchronized KudaVPN probe from direct Wi-Fi.

Mac and server capture TCP headers and sequence ranges, never packet contents.
The server observer is an ephemeral SSH process; neither side changes a service.
The saved report contains no raw packet lines, client IP, URI, or keys.
"""
import ast
import datetime
import hashlib
import json
import os
import pathlib
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

# Runtime profiles and reports stay outside Git; the source can be versioned.
BASE = pathlib.Path(os.environ.get('KUDAVPN_PRIVATE_DIR', '~/Documents/KudaVPN-private')).expanduser().resolve()
XRAY = BASE / 'bin' / 'xray'
LINK = BASE / 'owner-happ.txt'
REPORTS = BASE / 'diagnostic-reports'
SERVER = '209.38.196.186'
XRAY_SHA256 = '5d9dd24c0aba4b6cfcc6a33a5d67f854816ee17f392bf932ec8176da46f7e404'
FINISHED = 'ПРОВЕРКА ЗАВЕРШЕНА. МОЖНО ВКЛЮЧИТЬ ЩУКА VPN'
SSH = ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-o', 'StrictHostKeyChecking=yes', 'root@' + SERVER]

# This program is sent on SSH stdin; no file or setting is changed on the host.
REMOTE_OBSERVER = r'''
import datetime, hashlib, ipaddress, json, os, pathlib, re, shutil, socket, subprocess, sys, threading, time
from urllib.parse import parse_qs, urlsplit

def parse_packet(line):
    match = re.search(r'\bIP6?\s+(\S+)\s+>\s+(\S+):(?:\s+Flags|\s+tcp\b)',line)
    if not match:return None
    try:
        src,sport=match.group(1).rsplit('.',1)
        dst,dport=match.group(2).rsplit('.',1)
        ts=float(line.split()[0]);sport=int(sport);dport=int(dport)
    except (ValueError,IndexError):return None
    flag_match = re.search(r'\bFlags \[([^]]+)\]', line)
    len_match = re.search(r'\blength (\d+)\b', line)
    if not len_match: len_match = re.search(r'\btcp (\d+)\s*$', line)
    seq_match=re.search(r'\bseq (\d+)(?::(\d+))?',line)
    ack_match=re.search(r'\back (\d+)\b',line)
    return {'ts':ts,'src':src,'sport':sport,'dst':dst,'dport':dport,
            'flags':flag_match.group(1) if flag_match else '?',
            'length':int(len_match.group(1)) if len_match else -1,
            'seq_start':int(seq_match.group(1)) if seq_match else None,
            'seq_end':int(seq_match.group(2)) if seq_match and seq_match.group(2) else None,
            'ack_num':int(ack_match.group(1)) if ack_match else None,
            'sack_blocks':'sack ' in line.lower() and '{' in line}

TCP_STATES={'01':'ESTABLISHED','02':'SYN_SENT','03':'SYN_RECV',
            '04':'FIN_WAIT1','05':'FIN_WAIT2','06':'TIME_WAIT',
            '07':'CLOSE','08':'CLOSE_WAIT','09':'LAST_ACK',
            '0A':'LISTEN','0B':'CLOSING'}
def parse_proc_tcp(line):
    fields=line.split()
    if len(fields)<10 or fields[0]=='sl':return None
    try:
        local_hex,local_port=fields[1].split(':')
        dest_hex,dest_port=fields[2].split(':')
        local=socket.inet_ntoa(bytes.fromhex(local_hex)[::-1])
        dest=socket.inet_ntoa(bytes.fromhex(dest_hex)[::-1])
        return {'local':local,'local_port':int(local_port,16),
                'destination':dest,'destination_port':int(dest_port,16),
                'state':TCP_STATES.get(fields[3],fields[3]),'inode':int(fields[9])}
    except (ValueError,IndexError,OSError):return None

def parse_ss_line(line):
    fields=line.split()
    if len(fields)<5:return None
    try:
        local,local_port=fields[3].rsplit(':',1)
        dest,dest_port=fields[4].rsplit(':',1)
        owners={(int(pid),name) for name,pid in re.findall(r'"([^"\n]+)",pid=(\d+)',line)}
        if len(owners)!=1:return None
        pid,name=next(iter(owners))
        return {'local':local.strip('[]'),'local_port':int(local_port),
                'destination':dest.strip('[]'),'destination_port':int(dest_port),
                'state':fields[0],'pid':pid,'process':name}
    except (ValueError,IndexError):return None

if '--self-test' in sys.argv:
    sample='1760270000.111 IP 198.51.100.10.54321 > 203.0.113.4.443: Flags [S], seq 1, win 65535, length 0'
    p=parse_packet(sample)
    assert p and p['sport']==54321 and p['dport']==443 and p['flags']=='S' and p['length']==0
    sample='1760270000.222 IP 203.0.113.4.443 > 198.51.100.10.54321: Flags [P.], seq 1:100, ack 2, length 99'
    p=parse_packet(sample)
    assert p and p['length']==99 and p['flags']=='P.' and p['seq_start']==1 and p['seq_end']==100
    sample='1760270000.333 IP 198.51.100.10.54321 > 203.0.113.4.443: tcp 15'
    assert parse_packet(sample)['length']==15
    sample='1760270000.444 IP6 2001:db8::1.50123 > 2606:4700::1.443: Flags [P.], seq 1:101, length 100'
    assert parse_packet(sample)['dst']=='2606:4700::1'
    row='0: 0100007F:CCDD 04030201:01BB 01 00000000:00000000 00:00000000 00000000 0 0 98765'
    p=parse_proc_tcp(row)
    assert p and p['local']=='127.0.0.1' and p['destination']=='1.2.3.4'
    assert p['destination_port']==443 and p['inode']==98765
    ss='ESTAB 0 0 209.38.196.186:53000 1.2.3.4:443 users:(("xray",pid=321,fd=9))'
    p=parse_ss_line(ss)
    assert p and p['pid']==321 and p['process']=='xray' and p['destination_port']==443
    print('tcp, proc and ss parser self-test OK')
    sys.exit(0)

print_lock=threading.Lock()
ready_to_emit=threading.Event()
def emit(kind, **fields):
    if kind!='ready':ready_to_emit.wait(timeout=3)
    with print_lock:
        try:print(json.dumps({'kind':kind,'ts':time.time(),**fields},separators=(',',':')),flush=True)
        except BrokenPipeError:raise SystemExit(0)

def redact(value,secrets):
    value=value.strip()
    for secret in secrets:
        if secret: value=value.replace(secret,'[SECRET]')
    value=re.sub(r'(?i)\b[a-z][a-z0-9+.-]*://\S+','[URL]',value)
    value=re.sub(r'(?i)\b[\w.+-]+@[\w.-]+\b','[EMAIL]',value)
    value=re.sub(r'(?i)\b(password|token|pbk|sid|privatekey|uuid)\s*[:=]\s*\S+',r'\1=[SECRET]',value)
    value=re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b','[UUID]',value)
    value=re.sub(r'(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?','[IP]',value)
    value=re.sub(r'(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b','[HOST]',value)
    value=re.sub(r'(?i)\b[a-z0-9_-]{40,}\b','[OPAQUE]',value)
    value=re.sub(r'/(?:Users|home|opt|root|tmp|var)/[^\s>]+','[PATH]',value)
    return value[:350]

children=[]
try:
    peer=str(ipaddress.ip_address(os.environ['SSH_CONNECTION'].split()[0]))
    if ipaddress.ip_address(peer).version!=4:raise RuntimeError('ssh_peer_not_ipv4')
    if not shutil.which('tcpdump'):raise RuntimeError('tcpdump_unavailable')
    cfg=json.loads(pathlib.Path('/opt/kudavpn-xray/config/config.json').read_text())
    remote_link=pathlib.Path('/opt/kudavpn-xray/owner-happ.txt').read_text().strip()
    uri=urlsplit(remote_link); params=parse_qs(uri.query)
    inbound=next(x for x in cfg.get('inbounds',[]) if x.get('protocol')=='vless'
                 and x.get('streamSettings',{}).get('security')=='reality')
    reality=inbound['streamSettings']['realitySettings']
    target_setting=reality.get('target') or reality.get('dest')
    if not isinstance(target_setting,str) or ':' not in target_setting:
        raise RuntimeError('invalid_reality_target')
    target_host,target_port=target_setting.rsplit(':',1)
    if target_host!=params.get('sni',[None])[0] or target_port!='443':
        raise RuntimeError('target_sni_mismatch')
    target_candidates=set()
    for family in ('ahostsv4','ahostsv6'):
        lookup=subprocess.run(['getent',family,target_host],capture_output=True,text=True,timeout=6)
        if lookup.returncode==0:
            for row in lookup.stdout.splitlines():
                try:target_candidates.add(str(ipaddress.ip_address(row.split()[0])))
                except (ValueError,IndexError):pass
    if not target_candidates:raise RuntimeError('target_dns_unavailable')
    clients=inbound.get('settings',{}).get('clients',inbound.get('settings',{}).get('users',[]))
    compatible=(hashlib.sha256(remote_link.encode()).hexdigest()==sys.argv[1]
        and uri.hostname=='209.38.196.186' and uri.port==443
        and inbound.get('listen')=='127.0.0.1' and inbound.get('port')==10443
        and uri.username in {c.get('id') for c in clients}
        and params.get('sni',[None])[0] in reality.get('serverNames',[])
        and params.get('sid',[None])[0] in reality.get('shortIds',[])
        and params.get('flow')==['xtls-rprx-vision'])
    if not compatible:raise RuntimeError('client_server_profile_mismatch')
    if subprocess.run(['systemctl','is-active','--quiet','kudavpn-xray-front.socket']).returncode:
        raise RuntimeError('front_socket_inactive')
    state=subprocess.run(['docker','inspect','--format','{{.State.Health.Status}}',
        'kudavpn-xray-xray-1'],capture_output=True,text=True,timeout=5)
    if state.returncode or state.stdout.strip()!='healthy':raise RuntimeError('xray_container_unhealthy')
    verbose=bool(cfg.get('log',{}).get('loglevel') in ('debug','info') and reality.get('show'))
    secrets=[uri.username,params.get('pbk',[''])[0],params.get('sid',[''])[0],reality.get('privateKey','')]
    host_ip='209.38.196.186'
    meta=subprocess.run(['docker','inspect','--format',
        '{{.State.Pid}} {{.HostConfig.NetworkMode}}','kudavpn-xray-xray-1'],
        capture_output=True,text=True,timeout=5)
    if meta.returncode:raise RuntimeError('container_process_unavailable')
    meta_fields=meta.stdout.split()
    if len(meta_fields)!=2 or not meta_fields[0].isdigit() or int(meta_fields[0])<=0:
        raise RuntimeError('container_process_unavailable')
    container_pid=int(meta_fields[0]);network_mode=meta_fields[1]
    def xray_pids():
        pending=[container_pid];seen_pids=set();found=[]
        while pending and len(seen_pids)<50:
            pid=pending.pop()
            if pid in seen_pids:continue
            seen_pids.add(pid)
            try:
                comm=pathlib.Path('/proc/'+str(pid)+'/comm').read_text().strip()
                if comm=='xray':found.append(pid)
                children=pathlib.Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children').read_text()
                pending.extend(int(x) for x in children.split() if x.isdigit())
            except (OSError,ValueError):continue
        return found
    def xray_sockets():
        result={}
        for pid in xray_pids():
            try:
                fds=pathlib.Path('/proc/'+str(pid)+'/fd')
                inodes=set()
                for fd in fds.iterdir():
                    try:
                        match=re.fullmatch(r'socket:\[(\d+)\]',os.readlink(str(fd)))
                        if match:inodes.add(int(match.group(1)))
                    except OSError:continue
                rows=pathlib.Path('/proc/'+str(pid)+'/net/tcp').read_text().splitlines()[1:]
                for line in rows:
                    row=parse_proc_tcp(line)
                    if not row or row['inode'] not in inodes:continue
                    if row['destination']=='0.0.0.0' or row['destination'].startswith('127.'):continue
                    row.update(pid=pid,process='xray',source='proc_xray')
                    result[(pid,row['inode'],row['local_port'],row['destination'],row['destination_port'])]=row
            except OSError:continue
        return result
    def host_sockets():
        result={}
        try:
            data=subprocess.run(['ss','-H','-tnp'],capture_output=True,text=True,timeout=2)
            if data.returncode:return result
            for line in data.stdout.splitlines():
                row=parse_ss_line(line)
                if not row or row['process']!='xray' or row['destination_port']!=443:continue
                row['source']='host_ss'
                result[(row['pid'],row['local_port'],row['destination'],row['destination_port'])]=row
        except (OSError,subprocess.TimeoutExpired):pass
        return result
    def sample_sockets():
        prior={};count=0
        while not socket_sampler_stop.is_set():
            current={**xray_sockets(),**host_sockets()}
            for key,row in current.items():
                if key not in prior or row['state']!=prior[key]['state']:
                    count+=1
                    if count<=1000:
                        emit('socket',source=row['source'],pid=row['pid'],process=row['process'],
                             local_port=row['local_port'],destination=row['destination'],
                             destination_port=row['destination_port'],state=row['state'])
                    elif count==1001:emit('observer_error',code='socket_event_limit')
            for key,row in prior.items():
                if key not in current and count<=1000:
                    emit('socket_end',source=row['source'],pid=row['pid'],process=row['process'],
                         local_port=row['local_port'],destination=row['destination'],
                         destination_port=row['destination_port'],last_state=row['state'])
            prior=current
            socket_sampler_stop.wait(.2)
    socket_sampler_stop=threading.Event()
    flows={}; next_flow=[1]; seen=[0]
    def flow_id(key):
        if key not in flows:
            flows[key]=next_flow[0];next_flow[0]+=1
        return flows[key]
    def classify(p,interface):
        src,dst,sp,dp=p['src'],p['dst'],p['sport'],p['dport']
        if interface=='lo':
            if dp==10443:return 'front_to_xray',flow_id(('internal',src,sp,dst,dp)),True
            if sp==10443:return 'xray_to_front',flow_id(('internal',dst,dp,src,sp)),True
            return None
        if dst==host_ip and dp==443:
            return 'client_to_front',flow_id(('client',src,sp)),src==peer
        if src==host_ip and sp==443:
            return 'front_to_client',flow_id(('client',dst,dp)),dst==peer
        if dp==443 and dst=='1.1.1.1':
            return 'requested_egress',flow_id(('requested',src,sp,dst,dp)),False
        if sp==443 and src=='1.1.1.1':
            return 'requested_reply',flow_id(('requested',dst,dp,src,sp)),False
        if dp==443:
            return 'egress_candidate',flow_id(('egress',src,sp,dst,dp)),False
        if sp==443:
            return 'egress_reply_candidate',flow_id(('egress',dst,dp,src,sp)),False
        return None
    def capture(interface,filter_expr):
        # No -q: parse full tcpdump TCP summary. No payload is printed or saved.
        cmd=['tcpdump','-p','-i',interface,'-nn','-l','-tt','-S','-s','96',filter_expr]
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              text=True,bufsize=1)
        children.append(proc)
        def drain():
            for line in proc.stdout:
                if ' IP ' not in line and ' IP6 ' not in line:continue
                p=parse_packet(line)
                if p is None:
                    emit('observer_error',code='unparsed_tcp_header');continue
                item=classify(p,interface)
                if not item:continue
                category,fid,peer_match=item
                seen[0]+=1
                if seen[0]>3000:
                    if seen[0]==3001:emit('observer_error',code='packet_event_limit')
                    continue
                extra={}
                if category in ('client_to_front','front_to_client'):
                    extra['client_port']=p['sport'] if category=='client_to_front' else p['dport']
                if category in ('egress_candidate','egress_reply_candidate','requested_egress','requested_reply'):
                    outgoing=category in ('egress_candidate','requested_egress')
                    extra={'local_port':p['sport'] if outgoing else p['dport'],
                           'destination':p['dst'] if outgoing else p['src'],
                           'destination_port':p['dport'] if outgoing else p['sport']}
                emit('packet',ts=p['ts'],category=category,flow=fid,peer_match=peer_match,
                     flags=p['flags'],length=p['length'],
                     seq_start=p['seq_start'],seq_end=p['seq_end'],
                     ack_num=p['ack_num'],sack_blocks=p['sack_blocks'],**extra)
        threading.Thread(target=drain,daemon=True).start()
        return proc
    eth=capture('eth0','tcp and port 443')
    lo=capture('lo','tcp and port 10443')
    since=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def logs(command,label):
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              text=True,bufsize=1)
        children.append(proc)
        def drain():
            for line in proc.stdout:
                low=line.lower()
                if not any(x in low for x in ('failed','error','timeout','reset','unauthorized','invalid')):
                    continue
                if 'reality' in low:category='reality_error'
                elif 'auth' in low or 'vless' in low:category='vless_or_auth_error'
                elif 'outbound' in low or 'dial' in low:category='outbound_error'
                else:category='other_error'
                # No raw log line crosses SSH. Preserve the error chain after redaction.
                emit('server_log',source=label,category=category,reason=redact(line,secrets))
        threading.Thread(target=drain,daemon=True).start()
        return proc
    docker_logs=logs(['docker','logs','--follow','--since',since,'kudavpn-xray-xray-1'],'xray')
    front_logs=logs(['journalctl','--no-pager','-f','-u','kudavpn-xray-front.service',
                     '--since',since],'front')
    time.sleep(.8)
    if any(p.poll() is not None for p in (eth,lo,docker_logs)):
        raise RuntimeError('observer_start_failed')
    emit('ready',profile_match=True,server_verbose_auth=verbose,network_mode=network_mode,
         target_candidates=sorted(target_candidates),
         process_sampler_available=bool(xray_pids()))
    ready_to_emit.set()
    threading.Thread(target=sample_sockets,daemon=True).start()
    deadline=time.monotonic()+85
    while time.monotonic()<deadline:
        if eth.poll() is not None or lo.poll() is not None:
            emit('observer_error',code='packet_observer_stopped');break
        emit('heartbeat')
        time.sleep(.5)
finally:
    if 'socket_sampler_stop' in globals():socket_sampler_stop.set()
    for p in children:
        if p.poll() is None:p.terminate()
    for p in children:
        try:p.wait(timeout=2)
        except subprocess.TimeoutExpired:p.kill();p.wait()
'''

class PreflightError(RuntimeError):
    pass


def require_regular_user(euid=None):
    """Keep SSH, the Xray client and reports in the Mac user's context."""
    if euid is None:
        euid = os.geteuid()
    if euid == 0:
        raise PreflightError(
            'Запускайте сценарий без sudo. Он сам запросит права администратора '
            'только для локального захвата TCP-заголовков.'
        )


def run(command,timeout=8):
    return subprocess.run(command,capture_output=True,text=True,timeout=timeout)


def route_interface(address):
    result=run(['/sbin/route','-n','get',address],5)
    match=re.search(r'^\s*interface:\s*(\S+)',result.stdout,re.M)
    if result.returncode or not match:raise PreflightError('Не удалось определить маршрут.')
    return match.group(1)


def direct_wifi():
    if route_interface(SERVER)!='en0' or route_interface('1.1.1.1')!='en0':
        raise PreflightError('Маршрут перестал идти через en0; проверка остановлена.')
    nc=run(['/usr/sbin/scutil','--nc','list'],5)
    nwi=run(['/usr/sbin/scutil','--nwi'],5)
    if nc.returncode or nwi.returncode:raise PreflightError('Невозможно проверить состояние VPN.')
    if re.search(r'\(Connected\)|\(Connecting\)',nc.stdout) or re.search(r'^\s*utun\d+\s*:',nwi.stdout,re.M):
        raise PreflightError('Обнаружен сторонний VPN; проверка остановлена.')


class RouteGuard:
    def __init__(self):
        self.stop=threading.Event();self.changed=threading.Event();self.reason=''
        self.thread=threading.Thread(target=self._watch,daemon=True)
    def _watch(self):
        while not self.stop.is_set():
            try:direct_wifi()
            except Exception as exc:
                self.reason=str(exc) if isinstance(exc,PreflightError) else 'Ошибка проверки маршрута.'
                self.changed.set();return
            if self.stop.wait(.75):return
    def start(self):self.thread.start()
    def close(self):self.stop.set();self.thread.join(timeout=6)
    def check(self):
        if self.changed.is_set():raise PreflightError(self.reason)


def profile():
    if not XRAY.is_file() or hashlib.sha256(XRAY.read_bytes()).hexdigest()!=XRAY_SHA256:
        raise PreflightError('Локальный Xray отсутствует или отличается от проверенной версии.')
    if not LINK.is_file() or LINK.stat().st_mode & 0o077:
        raise PreflightError('Защищённая ссылка отсутствует или её права слишком широки.')
    raw=LINK.read_text().strip();uri=urlsplit(raw);params=parse_qs(uri.query)
    if (uri.scheme!='vless' or uri.hostname!=SERVER or uri.port!=443
        or params.get('security')!=['reality'] or params.get('type')!=['tcp']
        or params.get('flow')!=['xtls-rprx-vision'] or params.get('sni')!=['www.cloudflare.com']
        or params.get('fp')!=['chrome'] or not uri.username or not params.get('pbk') or not params.get('sid')):
        raise PreflightError('Локальный профиль не соответствует ожидаемой конфигурации.')
    try:uuid.UUID(uri.username)
    except ValueError:raise PreflightError('UUID локального профиля повреждён.') from None
    return uri,params,hashlib.sha256(raw.encode()).hexdigest()


def sanitize_reason(line,secrets):
    marker='failed to process outbound traffic'
    low=line.lower()
    if marker in low:line=line[low.index(marker)+len(marker):].strip(' :>\t\n')
    for secret in secrets:
        if secret:line=line.replace(secret,'[SECRET]')
    line=re.sub(r'(?i)\b[a-z][a-z0-9+.-]*://\S+','[URL]',line)
    line=re.sub(r'(?i)\b[\w.+-]+@[\w.-]+\b','[EMAIL]',line)
    line=re.sub(r'(?i)\b(password|token|pbk|sid|privatekey|uuid)\s*[:=]\s*\S+',r'\1=[SECRET]',line)
    line=re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b','[UUID]',line)
    line=re.sub(r'(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?','[IP]',line)
    line=re.sub(r'(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b','[HOST]',line)
    line=re.sub(r'(?i)\b[a-z0-9_-]{40,}\b','[OPAQUE]',line)
    line=re.sub(r'/(?:Users|home|opt|root|tmp|var)/[^\s>]+','[PATH]',line)
    return line[:350] or 'причина не указана в строке Xray'


def start_observer(digest,events):
    proc=subprocess.Popen(SSH+['python3','-u','-',digest],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
    try:
        proc.stdin.write(REMOTE_OBSERVER);proc.stdin.close()
        ready,_,_=select.select([proc.stdout],[],[],25)
        if not ready:raise PreflightError('Серверный наблюдатель недоступен.')
        first_line=proc.stdout.readline()
        if not first_line:raise PreflightError('Серверный наблюдатель не ответил.')
        first=json.loads(first_line)
        if first.get('kind')!='ready' or not first.get('profile_match'):
            raise PreflightError('Профиль или состояние сервера не прошли проверку.')
        offset=first['ts']-time.time()
        events.append(first)
        def drain():
            for line in proc.stdout:
                try:item=json.loads(line)
                except json.JSONDecodeError:
                    events.append({'kind':'observer_error','code':'invalid_event','ts':time.time()+offset});continue
                if item.get('kind') in ('packet','server_log','observer_error','socket','socket_end'):
                    if len(events)<5000:events.append(item)
                    elif len(events)==5000:events.append({'kind':'observer_error','code':'local_event_limit','ts':time.time()+offset})
        threading.Thread(target=drain,daemon=True).start()
        return proc,offset,first
    except Exception:
        if proc.poll() is None:proc.terminate()
        try:proc.wait(timeout=2)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        raise


def parse_mac_packet(line):
    match=re.search(r'\bIP\s+(\S+)\s+>\s+(\S+):(?:\s+Flags|\s+tcp\b)',line)
    if not match:return None
    try:
        src,sport=match.group(1).rsplit('.',1)
        dst,dport=match.group(2).rsplit('.',1)
        timestamp=float(line.split()[0]);sport=int(sport);dport=int(dport)
    except (ValueError,IndexError):return None
    flags=re.search(r'\bFlags \[([^]]+)\]',line)
    length=re.search(r'\blength (\d+)\b',line)
    if not length:length=re.search(r'\btcp (\d+)\s*$',line)
    seq=re.search(r'\bseq (\d+)(?::(\d+))?',line)
    ack=re.search(r'\back (\d+)\b',line)
    return {'ts':timestamp,'src':src,'sport':sport,'dst':dst,'dport':dport,
            'flags':flags.group(1) if flags else '?',
            'length':int(length.group(1)) if length else -1,
            'seq_start':int(seq.group(1)) if seq else None,
            'seq_end':int(seq.group(2)) if seq and seq.group(2) else None,
            'ack_num':int(ack.group(1)) if ack else None,
            'sack_blocks':'sack ' in line.lower() and '{' in line}


def start_mac_observer(events):
    """Read tcpdump summaries on en0; do not collect or persist packet bytes."""
    cmd=['/usr/bin/sudo','-n','/usr/sbin/tcpdump','-p','-i','en0','-nn','-l','-tt','-S',
         '-s','96','tcp and host '+SERVER+' and port 443']
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          text=True,bufsize=1)
    def drain():
        flows={};next_flow=[1]
        for line in proc.stdout:
            if ' IP ' not in line:continue
            packet=parse_mac_packet(line)
            if packet is None:
                if SERVER in line and len(events)<2500:
                    events.append({'kind':'observer_error','code':'unparsed_mac_tcp_header','ts':time.time()})
                continue
            if packet['dst']==SERVER and packet['dport']==443:
                category='mac_to_front';key=packet['sport']
            elif packet['src']==SERVER and packet['sport']==443:
                category='front_to_mac';key=packet['dport']
            else:continue
            if key not in flows:flows[key]=next_flow[0];next_flow[0]+=1
            if len(events)<2500:
                events.append({'kind':'packet','category':category,'flow':flows[key],
                               'ts':packet['ts'],'flags':packet['flags'],
                               'length':packet['length'],
                               'seq_start':packet['seq_start'],'seq_end':packet['seq_end'],
                               'ack_num':packet['ack_num'],'sack_blocks':packet['sack_blocks'],
                               'client_port':key})
            elif len(events)==2500:
                events.append({'kind':'observer_error','code':'mac_packet_limit','ts':time.time()})
    threading.Thread(target=drain,daemon=True).start()
    time.sleep(.75)
    if proc.poll() is not None:
        stop_process(proc)
        raise PreflightError('Не удалось запустить tcpdump на en0 с правами администратора.')
    return proc


def start_client(uri,params,folder,events):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    cfg={'log':{'loglevel':'debug','access':'none'},
         'inbounds':[{'tag':'diagnostic-socks','listen':'127.0.0.1','port':port,
                      'protocol':'socks','settings':{'auth':'noauth','udp':False}}],
         'outbounds':[{'tag':'remote','protocol':'vless','settings':{'vnext':[
             {'address':SERVER,'port':443,'users':[{'id':uri.username,
                 'encryption':'none','flow':params['flow'][0]}]}]},
             'streamSettings':{'network':'raw','security':'reality','realitySettings':{
                 'serverName':params['sni'][0],'fingerprint':'qq',
                 'password':params['pbk'][0],'shortId':params['sid'][0],
                 'show':True}}}]}
    path=folder/'config.json';path.write_text(json.dumps(cfg));path.chmod(0o600)
    checked=run([str(XRAY),'run','-test','-config',str(path)],8)
    if checked.returncode:raise PreflightError('Временная конфигурация Xray не прошла проверку.')
    proc=subprocess.Popen([str(XRAY),'run','-config',str(path)],
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    secrets=[uri.username,params['pbk'][0],params['sid'][0]]
    def drain():
        for line in proc.stdout:
            low=line.lower();kind=None;reason=None
            if 'uconn.verified:' in low:
                suffix=low.split('uconn.verified:',1)[1].strip()
                kind='reality_verified' if suffix.startswith('true') else 'reality_rejected'
            elif 'proxy/vless/outbound: tunneling request' in low:kind='vless_request_attempt'
            elif 'proxy/socks: tcp connect request' in low:kind='socks_request'
            elif 'failed to process outbound traffic' in low:
                kind='client_outbound_error';reason=sanitize_reason(line,secrets)
            elif 'timeout' in low or 'connection reset' in low:
                kind='client_transport_error';reason=sanitize_reason(line,secrets)
            if kind and len(events)<1000:
                event={'kind':kind,'ts':time.time()}
                if reason:event['reason']=reason
                events.append(event)
    threading.Thread(target=drain,daemon=True).start()
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if proc.poll() is not None:raise PreflightError('Временный Xray завершился при запуске.')
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.3):return proc,port
        except OSError:time.sleep(.1)
    proc.terminate();proc.wait(timeout=3)
    raise PreflightError('SOCKS временного Xray не открылся.')


def timed_events(events,start,end,offset=0):
    return [e for e in list(events) if start-.75<=e.get('ts',0)-offset<=end+1.5]


def packet_summary(events,origin=None,offset=0):
    grouped={}
    for event in events:
        if event.get('kind')!='packet':continue
        category=event['category']
        if category=='client_to_front' and not event.get('peer_match'):
            category='unmatched_to_front'
        elif category=='front_to_client' and not event.get('peer_match'):
            category='unmatched_from_front'
        key=(category,event['flow'])
        item=grouped.setdefault(key,{'packets':0,'payload_bytes':0,'syn':0,'rst':0,'fin':0,
                                     'unknown_length':0,'_ranges':[],'_unsequenced':0})
        item['packets']+=1
        if origin is not None and 'ts' in event:
            elapsed=round((event['ts']-offset-origin)*1000)
            item['first_ms']=min(item.get('first_ms',elapsed),elapsed)
            item['last_ms']=max(item.get('last_ms',elapsed),elapsed)
        if event['length']>=0:
            item['payload_bytes']+=event['length']
            if event['length']>0:
                begin,end=event.get('seq_start'),event.get('seq_end')
                if begin is not None and end is not None and end-begin==event['length']:
                    item['_ranges'].append((begin,end))
                else:item['_unsequenced']+=event['length']
        else:item['unknown_length']+=1
        for flag,field in [('S','syn'),('R','rst'),('F','fin')]:
            if flag in event['flags']:item[field]+=1
    result=[]
    for key,value in sorted(grouped.items()):
        ranges=sorted(value.pop('_ranges'))
        unsequenced=value.pop('_unsequenced')
        right=None;unique=0
        for begin,end in ranges:
            if right is None or begin>right:
                unique+=end-begin;right=end
            elif end>right:
                unique+=end-right;right=end
        complete=not unsequenced and not value['unknown_length']
        value['unique_bytes']=unique if complete else None
        value['retransmitted_bytes']=value['payload_bytes']-unique if complete else None
        value['sequence_complete']=complete
        result.append({'path':key[0],'flow':key[1],**value})
    return result


def public_tcp_evidence(mac_events,server_events):
    """Correlate by the client SYN sequence, not by the SSH source address.

    TCP sequence numbers, ports and addresses remain in memory only. The return
    value contains counts and booleans suitable for a redacted report.
    """
    def packets(rows,kind):
        return [e for e in rows if e.get('kind')=='packet' and e.get('category')==kind]
    mac_out=packets(mac_events,'mac_to_front')
    mac_in=packets(mac_events,'front_to_mac')
    server_in=packets(server_events,'client_to_front')
    server_out=packets(server_events,'front_to_client')
    mac_syns={e.get('seq_start') for e in mac_out if 'S' in e.get('flags','') and e.get('seq_start') is not None}
    candidates={e['flow'] for e in server_in if 'S' in e.get('flags','') and e.get('seq_start') in mac_syns}
    matched=next(iter(candidates)) if len(candidates)==1 else None
    selected_in=[e for e in server_in if e['flow']==matched] if matched is not None else []
    selected_out=[e for e in server_out if e['flow']==matched] if matched is not None else []
    def count(rows,flag):
        return sum(flag in e.get('flags','') for e in rows)
    def acked(rows,syns):
        if len(syns)!=1:return None
        first=next(iter(syns))
        advances=[(e['ack_num']-first-1)%(1<<32) for e in rows if e.get('ack_num') is not None]
        reasonable=[n for n in advances if n<1_000_000]
        return max(reasonable) if reasonable else None
    unmatched={e['flow'] for e in server_in if not e.get('peer_match')}
    return {
        'matched_flow':matched,
        'mac_syn':count(mac_out,'S'),
        'mac_synack_received':count(mac_in,'S'),
        'server_syn_candidates':len(candidates),
        'server_syn_matched':count(selected_in,'S'),
        'server_synack_sent':count(selected_out,'S'),
        'server_ack_sent_bytes':acked(selected_out,mac_syns),
        'mac_ack_received_bytes':acked(mac_in,mac_syns),
        'mac_sack_blocks':sum(bool(e.get('sack_blocks')) for e in mac_in),
        'server_sack_blocks':sum(bool(e.get('sack_blocks')) for e in selected_out),
        'server_peer_matches_ssh':bool(selected_in and all(e.get('peer_match') for e in selected_in)),
        'source_port_preserved':bool(selected_in and mac_out and
                                     selected_in[0].get('client_port')==mac_out[0].get('client_port')),
        'unmatched_incoming_flows':len(unmatched),
        'unmatched_incoming_payload':sum(max(0,e.get('length',0)) for e in server_in if not e.get('peer_match')),
        'matched_incoming_payload':sum(max(0,e.get('length',0)) for e in selected_in),
        'matched_outgoing_payload':sum(max(0,e.get('length',0)) for e in selected_out),
    }


def correlate_target(server_part,flows,target_candidates):
    """Find the sole Xray-to-target flow by endpoint and process, never by byte equality."""
    strong=[];weak=[]
    for flow in (f for f in flows if f['path']=='egress_candidate' and f['syn']>0):
        packets=[e for e in server_part if e.get('kind')=='packet' and
                 e.get('category')=='egress_candidate' and e.get('flow')==flow['flow']]
        if not packets:continue
        packet=packets[0]
        endpoint=(packet['destination'],packet['destination_port'],packet['local_port'])
        sockets=[e for e in server_part if e.get('kind') in ('socket','socket_end') and
                 (e.get('destination'),e.get('destination_port'),e.get('local_port'))==endpoint]
        exact=[e for e in sockets if e.get('kind')=='socket']
        owner=exact[0] if exact and len({(e['pid'],e['process']) for e in exact})==1 else None
        if owner and owner['process']!='xray':continue
        reply=next((f for f in flows if f['path']=='egress_reply_candidate' and
                    f['flow']==flow['flow']),None)
        item={'flow':flow,'reply':reply,'destination':endpoint[0],
              'destination_port':endpoint[1],'owner':owner,
              'matches_dns':endpoint[0] in target_candidates,
              'states':[e.get('state',e.get('last_state')) for e in sockets]}
        if owner and owner['process']=='xray':strong.append(item)
        elif item['matches_dns']:weak.append(item)
    if len(strong)==1:return strong[0]
    if not strong and len(weak)==1:return weak[0]
    return None


def path_bytes(flows,path):
    relevant=[f for f in flows if f['path']==path]
    if not relevant:return {'flows':0,'unique':0,'wire':0,'retransmit':0,'complete':True}
    complete=all(f['sequence_complete'] for f in relevant)
    return {'flows':len(relevant),
            'unique':sum(f['unique_bytes'] for f in relevant) if complete else None,
            'wire':sum(f['payload_bytes'] for f in relevant),
            'retransmit':sum(f['retransmitted_bytes'] for f in relevant) if complete else None,
            'complete':complete}


def stop_process(proc):
    if proc is None:return
    if proc.poll() is None:proc.terminate()
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()


def curl_probe(port,guard):
    command=['/usr/bin/curl','--silent','--show-error','--connect-timeout','30',
             '--max-time','30','--noproxy','','--proxy','socks5h://127.0.0.1:'+str(port),
             '--output','/dev/null','--write-out',
             '%{http_code} %{size_download} %{time_appconnect} %{time_total}',
             'https://1.1.1.1/cdn-cgi/trace']
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    began=time.monotonic()
    while True:
        if guard.changed.is_set():
            stop_process(proc);raise PreflightError(guard.reason)
        try:
            stdout,_=proc.communicate(timeout=.25)
            return proc.returncode,stdout
        except subprocess.TimeoutExpired:
            if time.monotonic()-began>34:
                stop_process(proc);return 124,''


def main():
    os.umask(0o077)
    try:
        require_regular_user()
    except PreflightError as exc:
        raise SystemExit('Проверка не запущена: '+str(exc)) from None
    if len(sys.argv)==2 and sys.argv[1]=='--self-check':
        try:
            require_regular_user(0)
        except PreflightError:
            pass
        else:
            raise AssertionError('Запуск от root должен быть запрещён до создания отчёта.')
        REPORTS.mkdir(mode=0o700,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w+',prefix='.path-v5-permission-',suffix='.txt',dir=REPORTS) as check_report:
            check_report.write('local permission check\n')
            check_report.flush()
            check_report.seek(0)
            stat=os.fstat(check_report.fileno())
            assert stat.st_uid==os.geteuid() and stat.st_mode & 0o077==0
            assert check_report.read()=='local permission check\n'
        ast.parse(REMOTE_OBSERVER)
        check=run([sys.executable,'-c',REMOTE_OBSERVER,'--self-test'],5)
        if check.returncode:raise SystemExit('ОШИБКА: локальная проверка серверного парсера не прошла.')
        sample='1760270000.222 IP 192.0.2.4.54321 > 203.0.113.4.443: Flags [P.], seq 1:1441, ack 2, length 1440'
        parsed=parse_mac_packet(sample)
        assert parsed and parsed['sport']==54321 and parsed['seq_end']==1441
        syn='1760270000.111 IP 192.0.2.4.54321 > 203.0.113.4.443: Flags [S], seq 123456, win 65535, length 0'
        assert parse_mac_packet(syn)['seq_start']==123456
        assert parse_mac_packet(syn)['seq_end'] is None
        server_parser={}
        exec(REMOTE_OBSERVER.split("if '--self-test' in sys.argv:")[0],server_parser)
        assert server_parser['parse_packet'](syn)['seq_start']==123456
        ack='1760270000.222 IP 203.0.113.4.443 > 192.0.2.4.54321: Flags [.], ack 123457, win 65535, length 0'
        assert parse_mac_packet(ack)['ack_num']==123457
        mac_pair=[{'kind':'packet','category':'mac_to_front','flow':1,'flags':'S',
                   'seq_start':123456,'length':0,'client_port':54321},
                  {'kind':'packet','category':'mac_to_front','flow':1,'flags':'P.',
                   'seq_start':123457,'seq_end':123974,'length':517,'client_port':54321}]
        server_pair=[{'kind':'packet','category':'client_to_front','flow':3,'flags':'S',
                      'seq_start':123456,'length':0,'peer_match':False,'client_port':62000},
                     {'kind':'packet','category':'client_to_front','flow':3,'flags':'P.',
                      'seq_start':123457,'seq_end':123974,'length':517,
                      'peer_match':False,'client_port':62000},
                     {'kind':'packet','category':'front_to_client','flow':3,'flags':'S.',
                      'ack_num':123457,'length':0,'peer_match':False,'client_port':62000}]
        evidence=public_tcp_evidence(mac_pair,server_pair)
        assert evidence['server_syn_candidates']==1 and evidence['matched_flow']==3
        assert evidence['matched_incoming_payload']==517
        assert evidence['server_peer_matches_ssh'] is False
        assert evidence['source_port_preserved'] is False
        assert evidence['server_ack_sent_bytes']==0
        synthetic=[{'kind':'packet','category':'mac_to_front','flow':1,'length':1440,
                    'seq_start':1,'seq_end':1441,'flags':'P.','ts':1.0},
                   {'kind':'packet','category':'mac_to_front','flow':1,'length':1440,
                    'seq_start':1,'seq_end':1441,'flags':'P.','ts':1.1},
                   {'kind':'packet','category':'mac_to_front','flow':1,'length':288,
                    'seq_start':1441,'seq_end':1729,'flags':'P.','ts':1.2}]
        counted=path_bytes(packet_summary(synthetic),'mac_to_front')
        assert counted['unique']==1728 and counted['wire']==3168 and counted['retransmit']==1440
        sample_flows=[{'path':'egress_candidate','flow':2,'payload_bytes':1440,'first_ms':251,
                       'last_ms':15000,'syn':1,'rst':0,'fin':1}]
        sample_events=[{'kind':'packet','category':'egress_candidate','flow':2,
                        'destination':'203.0.113.7','destination_port':443,'local_port':50000},
                       {'kind':'socket','destination':'203.0.113.7','destination_port':443,
                        'local_port':50000,'pid':123,'process':'xray','source':'proc_xray','state':'ESTABLISHED'}]
        correlation=correlate_target(sample_events,sample_flows,{'203.0.113.7'})
        assert correlation and correlation['owner']['pid']==123
        profile()
        synthetic_error='failed to process outbound traffic > dial tcp 198.51.100.1:443: timeout'
        assert '[IP]' in sanitize_reason(synthetic_error,[])
        print('Самопроверка пройдена: права, SYN/ACK/SEQ, смена IP и порта относительно SSH, повторы, target-сопоставление, обезличивание и локальный профиль. Сетевой тест не запускался.')
        return
    if len(sys.argv)!=1:
        raise SystemExit('Использование: diagnose-kudavpn-path-v5.command [--self-check]')
    REPORTS.mkdir(mode=0o700,exist_ok=True)
    report=REPORTS/('synced-wifi-path-v5-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.txt')
    lines=['KudaVPN — синхронная проверка Mac → 443 → Xray → REALITY target',
           'Время: '+datetime.datetime.now().astimezone().isoformat(),
           'Эксперимент: fingerprint qq; сопоставление TCP по начальному SEQ, без доверия к IP SSH. Остальные параметры не меняются.',
           'Сохраняются только TCP-заголовки в памяти и обезличенные события; содержимое пакетов и ссылки не сохраняются.']
    def note(value):
        lines.append(value);print(value,flush=True)
        report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    server=None;client=None;mac=None;guard=None
    server_events=[];client_events=[];mac_events=[]
    try:
        direct_wifi();note('Прямой маршрут через en0 без стороннего VPN: подтверждён перед тестом.')
        if not pathlib.Path('/usr/sbin/tcpdump').is_file():
            raise PreflightError('На Mac отсутствует tcpdump.')
        note('Для локального захвата только TCP-заголовков macOS может запросить пароль администратора в Terminal.')
        auth=subprocess.run(['/usr/bin/sudo','-v'],timeout=120)
        if auth.returncode:raise PreflightError('Не получен доступ к локальному tcpdump.')
        direct_wifi()
        uri,params,digest=profile()
        guard=RouteGuard();guard.start()
        server,offset,observer_info=start_observer(digest,server_events)
        guard.check()
        target_candidates=set(observer_info.get('target_candidates',[]))
        if not target_candidates:raise PreflightError('Сервер не определил IP-адреса REALITY target.')
        note('Серверный наблюдатель готов; профиль совпадает, Xray здоров, target разрешён в '+str(len(target_candidates))+' IP-адреса(ов).')
        note('Сеть рабочего Xray: '+str(observer_info.get('network_mode','неизвестно'))+'.')
        if not observer_info.get('server_verbose_auth'):
            note('Рабочий Xray не пишет положительную авторизацию в warning-log; её проверим по событиям клиента и HTTPS-ответу.')
        mac=start_mac_observer(mac_events);guard.check()
        note('Локальный захват на en0 готов; содержимое пакетов не читается и не сохраняется.')
        with tempfile.TemporaryDirectory(prefix='kuda-synced-path-v5-',dir=str(BASE)) as folder:
            client,port=start_client(uri,params,pathlib.Path(folder),client_events)
            guard.check()
            note('Временный Xray-клиент запущен; выполняется один HTTPS-запрос к числовому IP через SOCKS/VLESS.')
            probe_start=time.time();curl_code,curl_output=curl_probe(port,guard)
            probe_end=time.time();time.sleep(1.5);guard.check();direct_wifi()
            capture_running=(mac.poll() is None and server.poll() is None)
            if not capture_running:
                note('ВНИМАНИЕ: наблюдатель завершился раньше времени; нулевые счётчики недостоверны.')
            client_part=timed_events(client_events,probe_start,probe_end)
            mac_part=timed_events(mac_events,probe_start,probe_end)
            server_part=timed_events(server_events,probe_start,probe_end,offset)
            note('HTTPS через KudaVPN: curl exit='+str(curl_code)+'; время '+str(round(probe_end-probe_start,2))+' с.')
            values=curl_output.split()
            if curl_code==0 and len(values)==4 and values[0].isdigit():
                note('HTTPS-метрики: HTTP '+values[0]+', байт='+values[1]+', TLS='+values[2]+' с, всего='+values[3]+' с.')
            for event in client_part:
                if event.get('kind') in ('reality_verified','reality_rejected','vless_request_attempt','client_outbound_error','client_transport_error'):
                    note('Клиент +'+str(round((event['ts']-probe_start)*1000))+' мс: '+event['kind']+
                         ((': '+event['reason']) if event.get('reason') else ''))
            for event in server_part:
                if event.get('kind')=='server_log':
                    note('Серверный журнал +'+str(round((event['ts']-offset-probe_start)*1000))+' мс '+
                         event['source']+'/'+event['category']+': '+event['reason'])
                elif event.get('kind')=='observer_error':note('Ошибка серверного наблюдателя: '+event.get('code','unknown'))
            for event in mac_part:
                if event.get('kind')=='observer_error':note('Ошибка локального наблюдателя: '+event.get('code','unknown'))
            mac_flows=packet_summary(mac_part,probe_start)
            tcp_evidence=public_tcp_evidence(mac_part,server_part)
            # Attribute a public flow only when its SYN sequence uniquely matches
            # the Mac capture. The SSH session may use another public source IP.
            attributed_server_part=[]
            for event in server_part:
                if (event.get('kind')=='packet' and
                    event.get('category') in ('client_to_front','front_to_client') and
                    event.get('flow')==tcp_evidence['matched_flow']):
                    attributed_server_part.append({**event,'peer_match':True})
                else:
                    attributed_server_part.append(event)
            server_flows=packet_summary(attributed_server_part,probe_start,offset)
            note('TCP SYN: Mac отправил '+str(tcp_evidence['mac_syn'])+
                 ', Mac получил SYN-ACK '+str(tcp_evidence['mac_synack_received'])+
                 ', серверных потоков с тем же начальным SEQ '+str(tcp_evidence['server_syn_candidates'])+'.')
            if tcp_evidence['server_syn_candidates']==1:
                note('Сопоставленный серверный поток: SYN получено '+str(tcp_evidence['server_syn_matched'])+
                     ', SYN-ACK отправлено '+str(tcp_evidence['server_synack_sent'])+
                     ', payload принято '+str(tcp_evidence['matched_incoming_payload'])+
                     ' байт; IP совпадает с SSH-сессией: '+
                     ('да' if tcp_evidence['server_peer_matches_ssh'] else 'нет')+
                     '; исходный порт сохранён: '+
                     ('да' if tcp_evidence['source_port_preserved'] else 'нет')+'.')
                note('TCP ACK полезных байт: сервер подтвердил '+
                     str(tcp_evidence['server_ack_sent_bytes'])+
                     ', Mac получил подтверждение '+str(tcp_evidence['mac_ack_received_bytes'])+
                     '; SACK-блоков сервер/Mac: '+str(tcp_evidence['server_sack_blocks'])+
                     '/'+str(tcp_evidence['mac_sack_blocks'])+'.')
            else:
                note('Серверный поток не удалось однозначно привязать к Mac по начальному SEQ; '+
                     'нулевые счётчики для Mac не доказывают потери на маршруте.')
            note('Публичный 443, потоки с IP, отличным от SSH: '+
                 str(tcp_evidence['unmatched_incoming_flows'])+
                 ', принятый payload '+str(tcp_evidence['unmatched_incoming_payload'])+' байт.')
            correlated=correlate_target(server_part,server_flows,target_candidates)
            target_tx=(path_bytes([correlated['flow']],'egress_candidate') if correlated
                       else path_bytes([],'egress_candidate'))
            target_rx=(path_bytes([correlated['reply']],'egress_reply_candidate') if correlated and correlated['reply']
                       else path_bytes([],'egress_reply_candidate'))
            stages=[('Mac → сервер:443',path_bytes(mac_flows,'mac_to_front')),
                    ('Сервер:443 получил',path_bytes(server_flows,'client_to_front')),
                    ('Прокси → Xray:10443',path_bytes(server_flows,'front_to_xray')),
                    ('Xray → REALITY target',target_tx),
                    ('REALITY target → Xray',target_rx),
                    ('Xray → запрошенный сайт',path_bytes(server_flows,'requested_egress')),
                    ('Запрошенный сайт → Xray',path_bytes(server_flows,'requested_reply')),
                    ('Xray → прокси',path_bytes(server_flows,'xray_to_front')),
                    ('Сервер → Mac',path_bytes(server_flows,'front_to_client')),
                    ('Mac получил',path_bytes(mac_flows,'front_to_mac'))]
            for title,stats in stages:
                unique=str(stats['unique']) if stats['complete'] else 'не определено'
                repeated=str(stats['retransmit']) if stats['complete'] else 'не определено'
                note(title+': уникальных байт='+unique+', байт в пакетах='+str(stats['wire'])+
                     ', повторы='+repeated+', потоков='+str(stats['flows'])+'.')
            if correlated:
                owner=correlated['owner']
                note('Точный IP исходящего потока Xray/REALITY target: '+correlated['destination']+
                     '; совпадает с DNS-набором текущего target: '+
                     ('да' if correlated['matches_dns'] else 'нет, назначение требует отдельной проверки')+
                     '; сокет принадлежит Xray: '+
                     ('да' if owner and owner['process']=='xray' else 'не подтверждено')+'.')
            else:
                note('Единственный исходящий поток к точному IP REALITY target не установлен; адреса иных соединений не раскрываются.')
            mac_tx=path_bytes(mac_flows,'mac_to_front');server_rx=path_bytes(server_flows,'client_to_front')
            xray_rx=path_bytes(server_flows,'front_to_xray')
            mac_back=path_bytes(mac_flows,'front_to_mac')
            verified=any(e.get('kind')=='reality_verified' for e in client_part)
            vless=any(e.get('kind')=='vless_request_attempt' for e in client_part)
            capture_reliable=(capture_running and not any(e.get('kind')=='observer_error'
                              for e in mac_part+server_part))
            if not capture_reliable:
                note('Вывод: захват неполон или содержит ошибки; нулевые счётчики нельзя использовать для локализации сбоя.')
            elif curl_code==0 and values and values[0]=='200':
                note('Вывод: REALITY/VLESS и HTTPS реально работают через прямой Wi-Fi в этом прогоне.')
            elif not mac_tx['wire']:
                note('Вывод: Xray-клиент на Mac не отправил TCP payload к серверу; отказ до международного маршрута.')
            elif not server_rx['wire'] and tcp_evidence['server_syn_candidates']!=1:
                note('Вывод: Mac отправил данные, но серверный поток не сопоставлен по SYN SEQ; '+
                     'место потери по этому захвату не установлено.')
            elif not server_rx['wire']:
                note('Вывод: Mac отправил данные, а публичный порт сервера их не увидел; вероятный разрыв между Wi-Fi и сервером, если наблюдатели не теряли пакеты.')
            elif not xray_rx['wire']:
                note('Вывод: публичный порт получил данные, но на loopback к Xray их нет; проверить TCP-прокси или наблюдатель.')
            elif not target_tx['wire']:
                note('Вывод: Xray получил данные, но не отправил их REALITY target; искать ошибку обработки приветствия Xray.')
            elif not target_rx['wire']:
                if mac_tx['unique'] is not None and server_rx['unique'] is not None and mac_tx['unique']>server_rx['unique']:
                    note('Вывод: на Mac вышло больше уникальных байт, чем дошло до публичного 443; target не ответил на полученное. Признак потери на внешнем пути, если захват полон.')
                elif server_rx['unique'] is not None and xray_rx['unique'] is not None and server_rx['unique']>xray_rx['unique']:
                    note('Вывод: прокси передал Xray меньше уникальных байт, чем получил; требуется проверка этого участка.')
                else:
                    note('Вывод: Xray отправил TCP-данные target, но ответного payload нет. Без содержимого пакетов нельзя доказать, что TLS-приветствие было полным.')
            elif verified and vless:
                note('Вывод: REALITY подтверждён клиентом и VLESS-запрос отправлен; HTTPS не завершился, см. исходящий поток к сайту.')
            elif verified:
                note('Вывод: REALITY подтверждён клиентом; VLESS-запрос и HTTPS не подтверждены.')
            else:
                note('Вывод: target ответил, но REALITY/VLESS или HTTPS не завершились; точный этап ищите в клиентской ошибке выше.')
            if mac_back['wire']==0 and target_rx['wire']>0:
                note('Ответ от target виден на сервере, но обратный payload на Mac не зафиксирован.')
            note('Маршрут через en0 сохранялся при периодических проверках и в конце теста.')
            note('Ограничение: счётчики между участками сопоставимы только для одного соединения и полного захвата; Xray может менять объём после рукопожатия.')
    except PreflightError as exc:
        note('Проверка остановлена: '+str(exc))
        if guard and guard.changed.is_set():note('Результаты после смены маршрута недостоверны.')
    except Exception as exc:
        note('Проверка прервана: '+type(exc).__name__+'; сырые подробности не сохранялись.')
    finally:
        stop_process(client);stop_process(mac);stop_process(server)
        if guard:guard.close()
        note('Временные процессы остановлены; временная конфигурация удалена; сервер не изменён.')
        note('Отчёт: '+str(report))
        print('\n'+'='*72+'\n'+FINISHED+'\n'+'='*72,flush=True)
        report.write_text('\n'.join(lines)+'\n'+FINISHED+'\n',encoding='utf-8')

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()

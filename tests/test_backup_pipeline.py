"""Backup security contracts; no network, databases, services or real secrets."""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module():
    spec=importlib.util.spec_from_file_location('backup_pipeline',Path(__file__).resolve().parents[1]/'deploy/backup/backup.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.app=module();self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.app.BASE=self.base
        self.name='20261029T120000Z-abcdef12-digitalocean-live.dump.age'
        self.data=b'age-encryption.org/v1\n'+b'only-synthetic-ciphertext'*3
        self.header={'name':self.name,'bytes':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}
        self.inbox,self.vault,self.receipts=self.app.peer_paths('digitalocean')
        for p in [self.inbox,self.vault,self.receipts]:p.mkdir(parents=True)

    def tearDown(self):self.tmp.cleanup()

    def test_header_and_peer_binding(self):
        self.assertEqual(self.app.header(self.header,'digitalocean'),self.header)
        with self.assertRaises(self.app.Failure):self.app.header(self.header,'justhost')

    def test_header_rejects_path_traversal_sizes_and_extra_fields(self):
        for change in [{'name':'../escape.age'},{'bytes':True},{'bytes':self.app.MAX_FILE+1},{'bytes':0},{'sha256':'x'*64},{'delete':'yes'}]:
            with self.subTest(change=change),self.assertRaises(self.app.Failure):self.app.header({**self.header,**change},'digitalocean')

    def test_concurrent_job_cannot_acquire_same_lock(self):
        with self.app.lock(self.base/'lock'):
            with self.assertRaises(self.app.Failure):
                with self.app.lock(self.base/'lock'):pass

    def test_disk_guard_preserves_existing_files(self):
        from collections import namedtuple
        Disk=namedtuple('Disk','total used free')
        with patch.object(self.app.shutil,'disk_usage',return_value=Disk(100,99,1)),self.assertRaises(self.app.Failure):self.app.space(self.base)

    def staging(self,data=None):
        src=self.inbox/(self.name+'.upload');src.write_bytes(self.data if data is None else data)
        meta=self.inbox/(self.name+'.ready.json');meta.write_text(json.dumps(self.header))
        return src,meta

    def seal(self,meta):
        # Tests run unprivileged; ownership boundary is separately verified live.
        with patch.object(self.app.os,'chown'),patch.object(self.app,'space',return_value=10000000):
            return self.app.seal_one(meta,'digitalocean')

    def test_sealer_makes_new_inode_not_writable_upload_inode(self):
        src,meta=self.staging();fd=os.open(src,os.O_RDWR);original=src.stat().st_ino
        try:
            receipt=self.seal(meta);final=self.vault/self.name
            self.assertTrue(receipt['verified']);self.assertNotEqual(final.stat().st_ino,original)
            os.write(fd,b'hostile uploader still has old FD')
            self.assertEqual(final.read_bytes(),self.data)
            self.assertEqual(final.stat().st_mode&0o777,0o440)
        finally:os.close(fd)

    def test_old_archive_is_never_overwritten(self):
        final=self.vault/self.name;final.write_bytes(b'old archive to retain')
        src,meta=self.staging()
        with self.assertRaises(self.app.Failure):self.seal(meta)
        self.assertEqual(final.read_bytes(),b'old archive to retain')

    def test_corruption_does_not_create_receipt(self):
        src,meta=self.staging(self.data[:-1]+b'X')
        with self.assertRaises(self.app.Failure):self.seal(meta)
        self.assertFalse((self.vault/self.name).exists());self.assertFalse(any(self.receipts.iterdir()))

    def test_symlink_staging_rejected(self):
        _,meta=self.staging();src=self.inbox/(self.name+'.upload');src.unlink();src.symlink_to(self.base/'outside')
        (self.base/'outside').write_bytes(self.data)
        with self.assertRaises(OSError):self.seal(meta)

    def test_non_age_payload_rejected_even_with_matching_hash(self):
        self.data=b'plaintext secret is not accepted'+b'x'*30
        self.header.update(bytes=len(self.data),sha256=hashlib.sha256(self.data).hexdigest())
        _,meta=self.staging()
        with self.assertRaises(self.app.Failure):self.seal(meta)

    def test_duplicate_upload_drains_input_and_returns_same_ack(self):
        _,meta=self.staging();ack=self.seal(meta)
        source=io.BytesIO(json.dumps(self.header).encode()+b'\n'+self.data);output=io.StringIO()
        with patch.object(self.app.signal,'alarm'),patch.object(self.app,'space',return_value=10000000):self.app.receive('digitalocean',source,output)
        self.assertEqual(json.loads(output.getvalue()),ack);self.assertEqual(source.read(),b'')

    def test_snapshot_command_is_explicit_read_only_and_effective_database(self):
        source=object.__new__(self.app.Source);source.host='digitalocean'
        command=source.command('pg_dump','actual_live',['--format=custom'])
        self.assertIn('actual_live',command);self.assertIn('-d',command)
        self.assertTrue(any('default_transaction_read_only=on' in x for x in command))
        self.assertFalse(any('restart' in x for x in command))

    def test_manifest_hash_order_is_deterministic_across_locales(self):
        self.assertIn('ORDER BY table_name,ordinal_position',self.app.META_SQL)
        import inspect
        self.assertIn('COLLATE',inspect.getsource(self.app.Snapshot.manifest))

    def test_health_never_treats_missing_data_as_success(self):
        self.assertIn('no_data',self.app.health_errors({}))
        self.assertIn('offsite',self.app.health_errors({'offsite':False}))

    def test_success_and_stale_health(self):
        now=dt.datetime.now(dt.timezone.utc)
        value={'last_success':now.isoformat(),'state':'ok','encryption':True,'offsite':True,'ciphertext_checksums':True}
        self.assertEqual(self.app.health_errors(value,now),[])
        self.assertIn('stale',self.app.health_errors(value,now+dt.timedelta(hours=27)))

    def test_read_metadata_refuses_symlink(self):
        p=self.base/'meta';p.symlink_to(self.base/'target');(self.base/'target').write_text('{}')
        with self.assertRaises(OSError):self.app.read_json(p)

    def test_failed_copy_leaves_all_previous_archives(self):
        old=self.base/'old.age';old.write_bytes(b'keep')
        with patch.object(self.app,'space',side_effect=self.app.Failure('disk_low')):
            with self.assertRaises(self.app.Failure):self.app.space(self.base)
        self.assertEqual(old.read_bytes(),b'keep')

    def test_listener_guard_ignores_only_volatile_ss_fields(self):
        spec=importlib.util.spec_from_file_location('backup_provision',Path(__file__).resolve().parents[1]/'deploy/backup/provision.py')
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        config={'host':'justhost','units':[],'protect':[]}
        first=b'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("systemd",pid=1,fd=20))\n'
        second=b'LISTEN 1 128 0.0.0.0:22 0.0.0.0:* users:(("systemd",pid=1,fd=30))\n'
        with patch.object(app,'run',return_value=first):before=app.baseline(config)
        with patch.object(app,'run',return_value=second):after=app.baseline(config)
        self.assertEqual(before,after)
        with patch.object(app,'run',return_value=second.replace(b':22 ',b':2222 ')):changed=app.baseline(config)
        self.assertNotEqual(before,changed)

    def test_lab_never_accepts_working_container_name(self):
        spec=importlib.util.spec_from_file_location('backup_lab',Path(__file__).resolve().parents[1]/'deploy/backup/restore_lab.py')
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        for name in ['kudavpn-premium-postgres-1','kudavpn-xray-xray-1','../outside']:
            with self.assertRaises(RuntimeError):app.validate(name)

    def test_lab_rejects_host_network_disk_volume_and_public_ports(self):
        spec=importlib.util.spec_from_file_location('backup_lab',Path(__file__).resolve().parents[1]/'deploy/backup/restore_lab.py')
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        name='kudavpn-restorecheck-'+12*'a'
        info={'Config':{'Labels':{'io.kudavpn.backup-restore':name}},'HostConfig':{'NetworkMode':'none','PortBindings':{},'Memory':512*1024**2,'NanoCpus':500000000,'PidsLimit':128,'ReadonlyRootfs':True,'LogConfig':{'Type':'none'},'Tmpfs':{'/var/lib/postgresql/data':'rw'}},'Mounts':[]}
        with patch.object(app,'inspect',return_value=info):self.assertEqual(app.validate(name),info)
        for field,value in [('NetworkMode','host'),('PortBindings',{'5432/tcp':[{}]}),('Memory',0),('NanoCpus',0),('ReadonlyRootfs',False),('LogConfig',{'Type':'json-file'})]:
            bad={**info,'HostConfig':{**info['HostConfig'],field:value}}
            with patch.object(app,'inspect',return_value=bad),self.assertRaises(RuntimeError):app.validate(name)
        bad={**info,'Mounts':[{'Type':'volume','Name':'production_data'}]}
        with patch.object(app,'inspect',return_value=bad),self.assertRaises(RuntimeError):app.validate(name)

    def test_retry_uses_original_backup_time_without_redump_or_deletion(self):
        (self.base/'local').mkdir();(self.base/'local'/self.name).write_bytes(self.data)
        old=self.base/'local/old.age';old.write_bytes(b'keep previous backup')
        status={'host':'digitalocean','state':'error','error':'offsite:offsite_transfer','last_attempt':'2026-10-28T12:00:00+00:00','artifacts':[self.header]}
        (self.base/'status.json').write_text(json.dumps(status))
        with patch.object(self.app,'space',return_value=10000000000),patch.object(self.app,'upload',return_value={'sealed_utc':'now'}),patch.object(self.app,'Source') as source,contextlib.redirect_stdout(io.StringIO()):
            self.app.retry({'host':'digitalocean'})
        source.assert_not_called()
        result=json.loads((self.base/'status.json').read_text())
        self.assertEqual(result['last_success'],status['last_attempt']);self.assertEqual(result['state'],'ok')
        self.assertEqual(old.read_bytes(),b'keep previous backup')

    def test_retry_rejects_corrupted_local_ciphertext(self):
        (self.base/'local').mkdir();(self.base/'local'/self.name).write_bytes(self.data[:-1]+b'X')
        status={'host':'digitalocean','state':'error','error':'offsite:offsite_transfer','last_attempt':'2026-10-28T12:00:00+00:00','artifacts':[self.header]}
        (self.base/'status.json').write_text(json.dumps(status))
        with patch.object(self.app,'space',return_value=10000000000),patch.object(self.app,'upload') as upload,self.assertRaises(self.app.Failure):
            self.app.retry({'host':'digitalocean'})
        upload.assert_not_called()

    def test_seal_trigger_is_bounded_without_disabling_systemd_guard(self):
        text=(Path(__file__).resolve().parents[1]/'deploy/backup/provision.py').read_text()
        self.assertIn('OnUnitInactiveSec=10s',text)
        self.assertNotIn('StartLimitIntervalSec=0',text)
        self.assertNotIn('PathChanged=',text)

    def test_owner_alerts_are_deduplicated_and_recovery_sent_once(self):
        import urllib.request
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        bad={'host':'justhost','state':'error','last_success':now,'artifacts':[]}
        good={'host':'justhost','state':'ok','last_success':now,'artifacts':[],'encryption':True,'offsite':True,'ciphertext_checksums':True}
        integrity={'state':'ok','checked_utc':now}
        (self.base/'status.json').write_text(json.dumps(bad));(self.base/'integrity.json').write_text(json.dumps(integrity))
        original=self.app.read_json
        def read(path,*args):
            if str(path)=='/var/lib/kudavpn-monitor/identity.json':return {'owner_id':123456,'owner_start_verified':True}
            return original(path,*args)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'ok':True,'result':{'chat':{'id':123456},'message_id':42}}).encode()
        replies=[{}, {**good,'host':'digitalocean','integrity':integrity}]
        with patch.object(self.app,'read_json',side_effect=read),patch.object(self.app,'ssh',return_value=['mock']),patch.object(self.app,'checked',side_effect=lambda *a,**k:json.dumps(replies[0]).encode()),patch.object(self.app,'space',return_value=10000000000),patch.object(Path,'read_text',return_value='synthetic-not-real-token'),patch.object(urllib.request,'urlopen',return_value=Response()) as send,contextlib.redirect_stdout(io.StringIO()):
            config={'host':'justhost','remote_host':'digitalocean'}
            self.app.health(config);self.app.health(config)
            self.assertEqual(send.call_count,1)
            request=send.call_args.args[0];self.assertEqual(json.loads(request.data)['chat_id'],123456)
            (self.base/'status.json').write_text(json.dumps(good));replies[0]=replies[1]
            self.app.health(config);self.app.health(config)
            self.assertEqual(send.call_count,2)

    def test_normal_running_job_does_not_alert_but_stuck_job_does(self):
        now=dt.datetime.now(dt.timezone.utc)
        status={'state':'running','last_attempt':now.isoformat(),'last_success':(now-dt.timedelta(hours=24)).isoformat(),'encryption':True,'offsite':True,'ciphertext_checksums':True}
        self.assertEqual(self.app.health_errors(status,now),[])
        self.assertIn('job_stuck',self.app.health_errors(status,now+dt.timedelta(minutes=22)))

    def test_boot_readiness_only_probes_never_starts_working_service(self):
        source=object.__new__(self.app.Source);source.host='justhost';source.databases={'pilot':'test'}
        class Ready:returncode=0
        with patch.object(self.app.sp,'run',return_value=Ready()) as run:source.wait_ready()
        args=run.call_args.args[0]
        self.assertIn('pg_isready',args);self.assertNotIn('systemctl',args)

    def test_check_metadata_only_backend_can_accept_deparser_difference(self):
        import copy
        spec=importlib.util.spec_from_file_location('backup_lab',Path(__file__).resolve().parents[1]/'deploy/backup/restore_lab.py')
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        row={'relname':'subscriptions','conname':'status','convalidated':True,'definition':"CHECK ((status)::text = 'active'::text)"}
        expected={'columns':[{'name':'status','type':'varchar'}],'indexes':[{'name':'unique'}],'constraints':[row]}
        actual=copy.deepcopy(expected);actual['constraints'][0]['definition']="CHECK (status = 'active')"
        calls=[]
        self.assertTrue(app.metadata_equivalent(expected,actual,lambda c:calls.append(c) or True))
        self.assertEqual(len(calls),1)
        self.assertFalse(app.metadata_equivalent(expected,actual,lambda c:False))
        for field in ['columns','indexes']:
            changed=copy.deepcopy(actual);changed[field]=[]
            self.assertFalse(app.metadata_equivalent(expected,changed,lambda c:True))
        changed=copy.deepcopy(actual);changed['constraints'][0]['convalidated']=False
        self.assertFalse(app.metadata_equivalent(expected,changed,lambda c:True))

    def test_foreign_key_difference_is_never_normalized(self):
        spec=importlib.util.spec_from_file_location('backup_lab',Path(__file__).resolve().parents[1]/'deploy/backup/restore_lab.py')
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        source={'columns':[],'indexes':[],'constraints':[{'relname':'subscriptions','conname':'owner','convalidated':True,'definition':'FOREIGN KEY (user_id) REFERENCES users(id)'}]}
        changed=json.loads(json.dumps(source));changed['constraints'][0]['definition']='FOREIGN KEY (user_id) REFERENCES other(id)'
        canonical=[]
        self.assertFalse(app.metadata_equivalent(source,changed,lambda c:canonical.append(c) or True));self.assertEqual(canonical,[])


if __name__=='__main__':unittest.main()

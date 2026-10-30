"""Owner decrypt boundary: synthetic bytes only; no SSH or real key input."""
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def module():
    spec=importlib.util.spec_from_file_location('owner_restore',Path(__file__).resolve().parents[1]/'scripts/verify_backup_restore_owner.py')
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app);return app


class OwnerRestoreTests(unittest.TestCase):
    def test_remote_cannot_target_working_container_or_arbitrary_action(self):
        app=module()
        for action,name,label in [('start','kudavpn-premium-postgres-1',None),('shell','kudavpn-restorecheck-'+12*'a',None),('load','kudavpn-restorecheck-'+12*'a','postgres')]:
            with self.assertRaises(ValueError):app.remote(action,name,label)

    def test_private_identity_is_pipe_only_not_argv_or_env(self):
        app=module();synthetic=b'not-a-real-owner-identity'
        def child(args,**kw):
            self.assertNotIn(synthetic.decode(),str(args));self.assertNotIn('env',kw)
            fd=kw['pass_fds'][0];self.assertEqual(os.read(fd,4096),synthetic)
            self.assertIn('/dev/fd/'+str(fd),args)
            return 'mocked child'
        with patch.object(app.sp,'Popen',side_effect=child):self.assertEqual(app.age_process('/verified/age',synthetic,Path('/cipher.age')),'mocked child')

    def test_plain_metadata_rejected_after_authentication_failure(self):
        app=module()
        class Child:
            stdout=io.BytesIO(b'synthetic unauthenticated fragment');returncode=1
            def communicate(self,**kw):return b'',b'suppressed'
            def poll(self):return 1
        with patch.object(app,'age_process',return_value=Child()),self.assertRaisesRegex(ValueError,'authentication'):
            app.plain('/age',b'fixture',Path('/cipher'))

    def test_plain_metadata_is_bounded(self):
        app=module()
        class Child:
            stdout=io.BytesIO(b'A'*100);returncode=0
            def communicate(self,**kw):return b'',b''
            def poll(self):return 0
        with patch.object(app,'age_process',return_value=Child()),self.assertRaisesRegex(ValueError,'size_limit'):
            app.plain('/age',b'fixture',Path('/cipher'),maximum=50)

    def test_owner_script_has_no_plaintext_key_file_or_password_env(self):
        text=(Path(__file__).resolve().parents[1]/'scripts/verify_backup_restore_owner.py').read_text()
        self.assertIn('getpass.getpass',text);self.assertIn('pass_fds',text)
        self.assertNotIn('AGE_PASSPHRASE',text)
        self.assertNotIn('write_bytes(identity)',text)
        self.assertNotIn('getUpdates',text)

    def test_safe_remote_error_code_keeps_exact_failed_stage(self):
        app=module()
        class Result:
            returncode=1
            stdout=b'{"lab":"failed","code":"restored_metadata_mismatch"}'
        with patch.object(app.sp,'run',return_value=Result()),self.assertRaisesRegex(ValueError,'^lab_verify_restored_metadata_mismatch$'):
            app.remote('verify','kudavpn-restorecheck-'+12*'a','pilot',data=b'{}')

    def test_untrusted_remote_error_is_never_echoed(self):
        app=module()
        class Result:
            returncode=1
            stdout=b'{"lab":"failed","code":"do not echo sensitive data"}'
        with patch.object(app.sp,'run',return_value=Result()),self.assertRaisesRegex(ValueError,'^lab_verify_operation_failed$'):
            app.remote('verify','kudavpn-restorecheck-'+12*'a','pilot',data=b'{}')


if __name__=='__main__':unittest.main()

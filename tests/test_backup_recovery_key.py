"""Local key ceremony only: synthetic identities, never infrastructure access."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def module():
    spec = importlib.util.spec_from_file_location('backup_key', Path(__file__).resolve().parents[1] / 'scripts/prepare_backup_recovery_key.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class RecoveryKeyTests(unittest.TestCase):
    def setUp(self):
        self.app = module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / 'private'
        self.identity = b'AGE-SECRET-KEY-1' + b'A' * 58 + b'\n'
        self.password = 'synthetic-test-only-password'
        self.public = 'age1' + 'q' * 58

    def tearDown(self):
        self.temporary.cleanup()

    def flow(self, answers):
        def keygen(binary, *args, data=None):
            return (self.public + '\n').encode() if args else self.identity
        with patch.object(self.app, 'tool', return_value='/fake-keygen'), patch.object(self.app, 'run_keygen', side_effect=keygen):
            return self.app.prepare(self.root, '/fake-tools', prompt=lambda prompt: next(answers))

    def test_encrypted_identity_round_trip(self):
        encoded = self.app.wrap(self.identity, self.password)
        self.assertNotIn(self.identity, encoded)
        self.assertNotIn(self.password.encode(), encoded)
        self.assertEqual(self.app.unwrap(encoded, self.password), self.identity)

    def test_wrong_password_fails_without_disclosure(self):
        encoded = self.app.wrap(self.identity, self.password)
        with self.assertRaisesRegex(ValueError, '^Key unlock failed or encrypted file is invalid$'):
            self.app.unwrap(encoded, 'another-synthetic-password')

    def test_authentication_rejects_ciphertext_tampering(self):
        item = json.loads(self.app.wrap(self.identity, self.password))
        token = list(item['token']); token[45] = 'A' if token[45] != 'A' else 'B'
        item['token'] = ''.join(token)
        with self.assertRaises(ValueError):
            self.app.unwrap(json.dumps(item).encode(), self.password)

    def test_untrusted_kdf_cost_is_rejected_before_allocation(self):
        encoded = json.dumps(dict(format=self.app.FORMAT, kdf='scrypt', n=2**60, r=8, p=1)).encode()
        with patch.object(self.app, 'derive') as derive, self.assertRaises(ValueError):
            self.app.unwrap(encoded, self.password)
        derive.assert_not_called()

    def test_weak_password_rejected(self):
        with self.assertRaises(ValueError):
            self.app.wrap(self.identity, 'short')

    def test_mismatched_confirmation_does_not_create_key(self):
        with self.assertRaises(ValueError):
            self.flow(iter([self.password, 'different-confirmation']))
        self.assertFalse((self.root / 'recovery-key.encrypted.json').exists())

    def test_flow_persists_only_encrypted_identity_with_private_modes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            key = self.flow(iter([self.password, self.password]))
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for file in self.root.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(self.identity, file.read_bytes())
            self.assertNotIn(self.password.encode(), file.read_bytes())
        self.assertTrue(json.loads((self.root / 'key-ready.json').read_bytes())['verified'])
        self.assertEqual(self.app.unwrap(key.read_bytes(), self.password), self.identity)

    def test_repeat_setup_does_not_rotate_existing_key(self):
        self.flow(iter([self.password, self.password]))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.flow(iter([self.password]))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_wrong_password_cannot_replace_existing_key(self):
        self.flow(iter([self.password, self.password]))
        before = (self.root / 'recovery-key.encrypted.json').read_bytes()
        with self.assertRaises(ValueError):
            self.flow(iter(['wrong-synthetic-password']))
        self.assertEqual(before, (self.root / 'recovery-key.encrypted.json').read_bytes())

    def test_symlink_key_rejected(self):
        self.root.mkdir(mode=0o700)
        outside = self.root.parent / 'outside'; outside.write_bytes(b'do not read')
        (self.root / 'recovery-key.encrypted.json').symlink_to(outside)
        with self.assertRaises(OSError):
            self.app.read_private(self.root / 'recovery-key.encrypted.json')

    def test_public_directory_and_git_storage_rejected(self):
        self.root.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            self.app.private_directory(self.root)
        self.root.chmod(0o700)
        (self.root / '.git').write_text('gitdir: elsewhere')
        with self.assertRaises(ValueError):
            self.app.private_directory(self.root / 'secret')

    def test_unsafe_key_mode_rejected(self):
        self.root.mkdir(mode=0o700)
        file = self.root / 'key'; file.write_bytes(b'ciphertext'); file.chmod(0o644)
        with self.assertRaises(ValueError):
            self.app.read_private(file)

    def test_exclusive_write_never_overwrites(self):
        self.root.mkdir(mode=0o700)
        file = self.root / 'existing'; file.write_bytes(b'previous')
        with self.assertRaises(FileExistsError):
            self.app.exclusive_write(file, b'replacement')
        self.assertEqual(file.read_bytes(), b'previous')

    def test_modified_binary_is_not_executed(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'age').write_bytes(b'not the verified binary')
        with self.assertRaises(ValueError):
            self.app.tool(self.root, 'age')

    def test_noninteractive_cli_refuses_secret_arguments(self):
        with patch.object(self.app.sys, 'argv', ['script', 'must-not-print']), patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(self.app.main(), 1)
        self.assertNotIn('must-not-print', out.getvalue())

    @unittest.skipUnless(os.environ.get('KUDA_TEST_AGE_DIR'), 'optional official age CLI not supplied')
    def test_actual_age_archive_round_trip_and_corruption(self):
        tools = Path(os.environ['KUDA_TEST_AGE_DIR'])
        age = self.app.tool(tools, 'age'); keygen = self.app.tool(tools, 'age-keygen')
        identity = self.app.run_keygen(keygen)
        public = self.app.recipient(keygen, identity)
        wrapped = self.app.wrap(identity, self.password)
        identity = self.app.unwrap(wrapped, self.password)
        payload = b'Synthetic backup fixture. No user or production data.'
        encrypted = subprocess.run([age, '-r', public], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
        self.assertTrue(encrypted.startswith(b'age-encryption.org/v1\n'))
        def decrypt(data):
            read, write = os.pipe()
            try:
                os.write(write, identity); os.close(write); write = None
                return subprocess.run([age, '-d', '-i', '/dev/fd/' + str(read)], input=data,
                                      pass_fds=(read,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            finally:
                os.close(read)
                if write is not None: os.close(write)
        restored = decrypt(encrypted)
        self.assertEqual(restored.returncode, 0)
        self.assertEqual(restored.stdout, payload)
        damaged = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
        self.assertNotEqual(decrypt(damaged).returncode, 0)


if __name__ == '__main__':
    unittest.main()

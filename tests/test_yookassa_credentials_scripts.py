"""Offline checks for the private test-shop credential helpers."""
import importlib.util
from pathlib import Path
import stat
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_save_creates_private_file_without_overwrite(tmp_path):
    module = load_script('save_yookassa_test_keys')
    path = tmp_path / 'test-secret'
    module.save_new(path, 'dummy-secret')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == 'dummy-secret\n'
    try:
        module.save_new(path, 'replacement')
    except FileExistsError:
        pass
    else:
        raise AssertionError('Existing secret was overwritten')
    assert path.read_text() == 'dummy-secret\n'


def test_auth_check_is_one_read_only_request(tmp_path):
    module = load_script('check_yookassa_test_auth')
    tmp_path.chmod(0o700)
    for name, value in [('yookassa-test-shop-id', '1503208'),
                        ('yookassa-test-secret', 'dummy-secret')]:
        path = tmp_path / name
        path.write_text(value + '\n')
        path.chmod(0o600)

    connection = mock.Mock()
    connection.getresponse.return_value.status = 200
    with mock.patch.object(module, 'TARGET', tmp_path), \
         mock.patch.object(module.http.client, 'HTTPSConnection', return_value=connection):
        assert module.check('1503208') is True
    assert connection.request.call_count == 1
    method, path = connection.request.call_args.args
    assert (method, path) == ('GET', '/v3/payments?limit=1')
    connection.close.assert_called_once()


def test_auth_check_rejects_world_readable_secret(tmp_path):
    module = load_script('check_yookassa_test_auth')
    tmp_path.chmod(0o700)
    for name, value in [('yookassa-test-shop-id', '1503208'),
                        ('yookassa-test-secret', 'dummy-secret')]:
        path = tmp_path / name
        path.write_text(value + '\n')
        path.chmod(0o600)
    (tmp_path / 'yookassa-test-secret').chmod(0o644)
    with mock.patch.object(module, 'TARGET', tmp_path):
        try:
            module.check('1503208')
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe secret file was accepted')

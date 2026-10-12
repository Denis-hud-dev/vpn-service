"""CI diagnostics must catch private material without including its value."""

from pathlib import Path

from scripts.check_repo_hygiene import check_file
from scripts.ci_report import render


def test_hygiene_rejects_private_files_and_material(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('.env').write_text('example')
    assert 'environment file' in check_file('.env')
    Path('backup.dump').write_bytes(b'irrelevant')
    assert 'secret or database file' in check_file('backup.dump')
    Path('source.py').write_text('private=' + '-----BEGIN ' + 'PRIVATE KEY-----')
    assert 'private key' in check_file('source.py')
    Path('example.txt').write_text('BOT_TOKEN=\n')
    assert check_file('example.txt') == []


def test_report_does_not_copy_failure_detail(tmp_path):
    path = tmp_path / 'pytest.xml'
    path.write_text('''<testsuite><testcase classname="checks" name="broken"><failure>private diagnostic detail</failure></testcase><testcase classname="checks" name="ok"/></testsuite>''')
    report = render(path)
    assert 'Failed: 1' in report
    assert 'checks.broken' in report
    assert 'private diagnostic detail' not in report
    assert 'did not produce' in render(tmp_path / 'missing.xml')

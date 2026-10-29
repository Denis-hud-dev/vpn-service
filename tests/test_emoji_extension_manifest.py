"""Offline asset/config checks: extension never overrides the published pack."""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'assets/telegram-emoji-extension'


def test_manifest_preserves_existing_ids_and_matches_registered_files():
    tree=ast.parse((ROOT/'bot/premium_emoji.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EMOJI' for t in n.targets))
    actual={ast.literal_eval(k):ast.literal_eval(v.args[0]) for k,v in zip(node.value.keys,node.value.values)}
    manifest=json.loads((ASSETS/'manifest.json').read_text())
    assert len(actual)==30
    assert all(actual[k]==v for k,v in manifest['original_custom_emoji_ids'].items())
    proof=json.loads((ASSETS/'registration.json').read_text())['icons']
    assert manifest['integration_enabled'] is False
    entries=manifest['icons'];assert len(entries)==18
    assert len({e['filename'] for e in entries})==18
    assert len([e for e in entries if e['reuse_existing']])==2
    for entry in entries:
        old=entry['reuse_existing']
        assert entry['custom_emoji_id']==actual[old or Path(entry['filename']).stem]
        if not old:
            registered=proof[Path(entry['filename']).stem]
            assert registered['sha256']==entry['sha256']
            assert registered['custom_emoji_id']==entry['custom_emoji_id']
        path=ASSETS/('reused-existing' if old else 'new-webm')/entry['filename']
        assert path.is_file() and path.stat().st_size<256000
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
        if old:assert entry['sha256']==manifest['original_sha256'][old]


def test_verified_exports_are_real_alpha_vp9_with_motion_and_loop():
    report=json.loads((ASSETS/'verification.json').read_text())
    assert report['passed']==18 and report['new']==16
    for r in report['results']:
        assert r['codec']=='vp9' and r['size']==[100,100]
        assert r['fps']==24 and r['frames']==48 and r['duration_seconds']==2
        assert r['alpha_verified'] and not r['audio'] and r['moving_frames']==48
        assert 0.3<r['loop_seam_ratio']<3


def test_no_runtime_import_enables_pending_icons():
    for path in (ROOT/'bot').rglob('*.py'):
        assert 'telegram-emoji-extension' not in path.read_text()

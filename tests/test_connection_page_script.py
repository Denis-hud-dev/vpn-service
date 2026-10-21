"""Exercise copy behavior in webviews with and without the modern clipboard API."""
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which('node')
SCRIPT = Path(__file__).resolve().parents[1] / 'bot/subscription_api/static/connect.js'


@pytest.mark.skipif(NODE is None, reason='Node.js is unavailable')
@pytest.mark.parametrize('modern,legacy,expected', [
    (True, False, 'modern'),
    (False, True, 'legacy'),
    (False, False, 'manual'),
])
def test_copy_keeps_link_local_and_has_webview_fallback(modern, legacy, expected):
    harness = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const events = {};
const token = 'a'.repeat(43);
const link = 'https://subscriptions.example/s/' + token;
const el = (name) => ({
  dataset: name === 'copy-primary' ? {subscription: link} : {},
  hidden: true, classList: {add() {}}, textContent: '',
  addEventListener(type, fn) { events[name + ':' + type] = fn; },
  setAttribute() {}, style: {}, select() {}, remove() {}
});
const elements = Object.fromEntries(['copy-primary','copy-feedback','private-link',
  'copy-backup','show-link','show-qr','platform-instruction'].map(name => [name, el(name)]));
const tabs = ['android','ios'].map(name => ({dataset:{platform:name},
  addEventListener(type, fn) {events[name + ':' + type] = fn;}, setAttribute() {}}));
let calls = [];
const document = {
  getElementById(name) { return elements[name]; },
  querySelectorAll() { return tabs; },
  querySelector() { return elements['platform-instruction']; },
  createElement() { return el('textarea'); },
  body: {appendChild() {}},
  execCommand(cmd) { calls.push(cmd); return LEGACY; }
};
const navigator = {clipboard:{async writeText(value) {
  calls.push(value);
  if (!MODERN) throw new Error('webview blocked clipboard');
}}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document,navigator});
(async () => {
  await events['copy-primary:click']();
  assert.strictEqual(calls[0], link);
  assert.strictEqual(elements['private-link'].hidden, EXPECTED !== 'manual');
  assert(!elements['copy-feedback'].textContent.includes(token));
  assert.strictEqual(calls.includes('copy'), EXPECTED === 'legacy' || (EXPECTED === 'manual' && !LEGACY));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
'''
    script = (harness.replace('MODERN', str(modern).lower())
                     .replace('LEGACY', str(legacy).lower())
                     .replace('EXPECTED', repr(expected)))
    result = subprocess.run([NODE, '-e', script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr

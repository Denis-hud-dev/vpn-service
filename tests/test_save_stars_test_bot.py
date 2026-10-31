"""No real token/network. Verify TEST-only endpoint and private disabled storage."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC=importlib.util.spec_from_file_location('save_stars_test',Path(__file__).resolve().parents[1]/'scripts/save_stars_test_bot.py')
helper=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(helper)
TOKEN='123456:offline_test_token'


class Response:
    status=200
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return json.dumps(self.data).encode()[:n]


def test_only_test_namespace_and_private_disabled_file(tmp_path,capsys):
    requests=[]
    class Opener:
        def open(self,request,timeout):
            requests.append(request.full_url)
            return Response({'ok':True,'result':{'id':123456,'is_bot':True,'username':'OfflineTestBot'}})
    identity=helper.verify_test_bot(TOKEN,opener=Opener())
    assert requests==['https://api.telegram.org/bot'+TOKEN+'/test/getMe']
    folder=tmp_path/'private';folder.mkdir(mode=0o700)
    target=folder/'bot.json'
    helper.save_private(target,TOKEN,identity)
    assert target.stat().st_mode & 0o777==0o600
    saved=json.loads(target.read_text())
    assert saved['environment']=='telegram_test' and saved['enabled'] is False
    assert saved['approved_test_prices']=={'1':1,'3':2,'12':3}
    assert TOKEN not in capsys.readouterr().out
    with pytest.raises(ValueError):helper.save_private(target,TOKEN,identity)
    folder.chmod(0o755)
    with pytest.raises(ValueError):helper.save_private(folder/'another.json',TOKEN,identity)


def test_credential_bearing_exception_is_masked():
    class Opener:
        def open(self,*args,**kwargs):raise OSError('URL includes '+TOKEN)
    with pytest.raises(ValueError) as error:helper.verify_test_bot(TOKEN,opener=Opener())
    assert TOKEN not in str(error.value) and error.value.__suppress_context__
    assert helper.NoRedirect().redirect_request(None,None,None,None,None,None) is None


@pytest.mark.parametrize('body',[{'ok':False,'result':{}},
    {'ok':True,'result':{'id':999,'is_bot':True,'username':'WrongBot'}},
    {'ok':True,'result':{'id':123456,'is_bot':False,'username':'NotABot'}}])
def test_invalid_test_response_is_rejected(body):
    with pytest.raises(ValueError):helper.verify_test_bot(TOKEN,opener=SimpleNamespace(open=lambda *a,**k:Response(body)))


def test_hidden_entry_refuses_nonterminal_before_prompt(monkeypatch):
    monkeypatch.setattr(helper.sys,'stdin',SimpleNamespace(isatty=lambda:False))
    monkeypatch.setattr(helper.getpass,'getpass',lambda *a:pytest.fail('Secret prompt must not echo'))
    with pytest.raises(SystemExit,match='hidden input'):helper.main()

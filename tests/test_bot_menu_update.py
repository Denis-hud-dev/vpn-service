"""UI overlay must not replace business logic or change callable signatures."""
import importlib.util
from pathlib import Path

import pytest


def module():
    spec=importlib.util.spec_from_file_location('menu_update','deploy/telegram-pilot/update_menu.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_overlay_is_limited_to_reviewed_welcome_and_navigation_files(tmp_path):
    app=module()
    assert set(app.FILES)=={'bot/keyboards/main.py','bot/screens.py','bot/premium_emoji.py',
                           'bot/handlers/start.py','bot/navigation.py','bot/keyboards/reply.py'}
    old,new=tmp_path/'old',tmp_path/'new'
    for base in (old,new):
        for name in app.FILES:
            path=base/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('async def screen(user, *, enabled=False):\n return "fixture"\n')
        (base/'business.py').write_text('unchanged')
    app.validate_overlay(old,new)
    (new/app.FILES[0]).write_text('async def screen(user, *, enabled=False):\n return "new UI"\n')
    assert app.code_digest(old)==app.code_digest(new)
    (new/'business.py').write_text('unexpected')
    assert app.code_digest(old)!=app.code_digest(new)


def test_overlay_rejects_signature_changes_and_symlinks(tmp_path):
    app=module();old,new=tmp_path/'old',tmp_path/'new'
    for base in (old,new):
        for name in app.FILES:
            path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('def screen(user): pass')
    (new/app.FILES[0]).write_text('def screen(user, secret): pass')
    with pytest.raises(ValueError,match='signatures'):app.validate_overlay(old,new)
    path=new/app.FILES[0];path.unlink();path.symlink_to(old/app.FILES[0])
    with pytest.raises(ValueError,match='Exact UI'):app.validate_overlay(old,new)


def test_bot_readiness_uses_database_checked_endpoint_not_liveness():
    app=module()
    calls=[]
    def run(args):
        calls.append(args)
        return '{"ready": true}' if args[-1].endswith('/ready') else '{"status":"alive"}'
    assert app.bot_ready(run,18081)
    assert calls[0][-1]=='http://127.0.0.1:18081/ready'
    assert '--fail' in calls[0]
    assert not app.bot_ready(lambda args:'{"ready":false}',18081)
    assert not app.bot_ready(lambda args:'{"status":"alive"}',18081)


def test_rollback_preserves_previous_ui_override_and_security(tmp_path,monkeypatch):
    app=module();backup=tmp_path/'backup';backup.mkdir()
    dropin=tmp_path/'service.d/98-purchase-menu.conf';dropin.parent.mkdir()
    previous='[Service]\nWorkingDirectory=/previous-test-ui\n'
    (backup/'previous-dropin').write_text(previous)
    dropin.write_text('[Service]\nWorkingDirectory=/new-test-ui\n')
    security=dropin.parent/'96-security.conf';security.write_text('unchanged')
    monkeypatch.setattr(app,'DROPIN',dropin)
    app.restore_dropin(backup)
    assert dropin.read_text()==previous and security.read_text()=='unchanged'


def test_overlay_allows_only_new_information_route_and_preserves_existing_functions(tmp_path):
    app=module();old,new=tmp_path/'old',tmp_path/'new'
    for base in (old,new):
        for name in app.FILES:
            path=base/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('def screen(user): pass\n')
    handler=new/'bot/handlers/start.py'
    handler.write_text('def screen(user): pass\nasync def information(callback, navigation, config): pass\n')
    app.validate_overlay(old,new)
    handler.write_text(handler.read_text()+'def unrelated(): pass\n')
    with pytest.raises(ValueError,match='signatures'):app.validate_overlay(old,new)
    handler.write_text('async def information(callback, navigation, config): pass\n')
    with pytest.raises(ValueError,match='signatures'):app.validate_overlay(old,new)


def test_overlay_only_allows_optional_false_custom_emoji_keyword(tmp_path):
    app=module();old,new=tmp_path/'old',tmp_path/'new'
    for base in (old,new):
        for name in app.FILES:
            p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('def screen(user): pass\n')
    (old/'bot/handlers/start.py').write_text('async def install_reply_keyboard(bot,user_id,services): pass\n')
    target=new/'bot/handlers/start.py'
    target.write_text('async def install_reply_keyboard(bot,user_id,services,*,custom_emoji=False): pass\n')
    app.validate_overlay(old,new)
    for bad in ('True','None'):
        target.write_text('async def install_reply_keyboard(bot,user_id,services,*,custom_emoji='+bad+'): pass\n')
        with pytest.raises(ValueError,match='signatures'):app.validate_overlay(old,new)

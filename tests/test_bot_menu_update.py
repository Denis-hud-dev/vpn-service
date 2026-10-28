"""UI overlay must not replace business logic or change callable signatures."""
import importlib.util
from pathlib import Path

import pytest


def module():
    spec=importlib.util.spec_from_file_location('menu_update','deploy/telegram-pilot/update_menu.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_overlay_is_limited_to_three_ui_files(tmp_path):
    app=module()
    assert set(app.FILES)=={'bot/keyboards/main.py','bot/screens.py','bot/handlers/subscription.py'}
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

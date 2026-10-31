"""Offline checks for the approved public URLs and config-only rollback."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.methods import SendPhoto, SendMessage
from aiogram.types import ReplyKeyboardMarkup
from bot.config import Config
from bot.premium_emoji import decorate, EMOJI
from bot.screens import main_actions
from bot.services import Services
from bot.services.onboarding import MemoryPilotOnboarding
from tests.fake_telegram import TelegramHarness


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path("deploy/telegram-pilot")/(name+".py"))
    m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def configured(monkeypatch):
    app = module("configure_social_links")
    monkeypatch.setenv("BOT_TOKEN", "123456:offline_test_token")
    monkeypatch.setenv("KUDAVPN_PILOT_MARKER", "kudavpn-telegram-pilot-v1")
    monkeypatch.setenv("PILOT_TRIAL_OWNER_ID", "123")
    monkeypatch.setenv("PILOT_PREMIUM_ACCOUNT_ENABLED", "1")
    monkeypatch.delenv("PILOT_MENU_WEBSITE_URL", raising=False)
    for key, value in app.APPROVED.items():
        monkeypatch.setenv(key, value)
    return Config.from_env()


def test_dropin_changes_only_two_approved_social_addresses():
    app = module("configure_social_links")
    assert Path("deploy/telegram-pilot/99-social-links.conf").read_text() == app.CONTENT
    assert app.APPROVED == {
        "PILOT_MENU_CHANNEL_URL": "https://t.me/KudaVPN_official",
        "PILOT_MENU_COMMUNITY_URL": "https://t.me/KudaVPN_community"}
    assert len(app.CONTENT.splitlines()) == 3
    assert "PILOT_MENU_WEBSITE_URL" not in app.CONTENT
    assert "WorkingDirectory" not in app.CONTENT


def test_three_direct_urls_keep_layout_and_custom_icons(monkeypatch):
    links = configured(monkeypatch).pilot_menu_links
    plain = main_actions(menu_links=links)
    rows = decorate(plain).keyboard.inline_keyboard
    assert [row[0].url for row in rows[-3:]] == [
        "https://r1276248.s2578.vm.mydomain.zone:20443",
        "https://t.me/KudaVPN_official", "https://t.me/KudaVPN_community"]
    assert all(row[0].callback_data is None and row[0].web_app is None for row in rows[-3:])
    assert [row[0].icon_custom_emoji_id for row in rows[-3:]] == [EMOJI[k].id for k in ("world", "channel", "community")]
    assert rows[0][0].callback_data == "nav:tariffs" and len(rows[0]) == 1


def test_repeat_start_and_home_keep_public_links_without_duplicate_banner(monkeypatch):
    config = configured(monkeypatch)
    async def run():
        services = Services.demo()
        services.premium_account_enabled = services.pilot_welcome_enabled = True
        services.menu_links = config.pilot_menu_links
        services.onboarding = MemoryPilotOnboarding()
        app = TelegramHarness(services=services, config=config)
        try:
            await app.text("/start")
            banner = services.onboarding.rows[123]["message_id"]
            receipt = await app.bot.send_message(123, "Important subscription event")
            for action in ("nav:account", "nav:home"):
                await app.callback(action)
            await app.text("/start")
            rows = app.navigation.views[123].screen.keyboard.inline_keyboard
            assert all(row[0].url and row[0].callback_data is None for row in rows[-3:])
            assert len([c for c in app.calls if isinstance(c, SendPhoto)]) == 1
            assert (123,banner) in app.messages and (123,receipt.message_id) in app.messages
            assert any(isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardMarkup) for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


def test_menu_guard_refuses_website_or_other_button_changes():
    app = module("configure_social_links")
    old = {"inline_keyboard":[[{"text":"Buy","callback_data":"nav:tariffs"}],
        [{"text":"Site","url":"https://example.org"}],
        [{"text":"Channel","callback_data":"nav:channel"}],
        [{"text":"Community","callback_data":"nav:community"}]]}
    new = copy.deepcopy(old)
    for row, url in zip(new["inline_keyboard"][-2:], app.APPROVED.values()):
        row[0].pop("callback_data");row[0]["url"] = url
    app.verify_menu(old,new)
    new["inline_keyboard"][-3][0]["url"] = "https://changed.example.org"
    with pytest.raises(ValueError,match="existing_menu_or_website_changed"):
        app.verify_menu(old,new)


def test_only_public_url_environment_changes_are_allowed():
    app = module("configure_social_links");stage = module("update_public_menu")
    before = {"DATABASE_URL":"existing-test-database","HEALTH_PORT":"18081"}
    after = {**before, **app.APPROVED}
    assert app.unchanged_configuration(stage,before,after)
    after["DATABASE_URL"] = "unexpected-test-database"
    assert not app.unchanged_configuration(stage,before,after)


def test_changed_existing_deployment_helper_is_never_executed(tmp_path,monkeypatch):
    app = module("configure_social_links")
    marker = tmp_path/"unexpected";path = tmp_path/"changed.py"
    path.write_text("open("+repr(str(marker))+",'w').write('unsafe')")
    monkeypatch.setattr(app,"STAGE",path)
    with pytest.raises(ValueError,match="deployment_helper_changed"):
        app.load_stage()
    assert not marker.exists()


def test_rollback_recovers_without_environment_of_failed_new_process(tmp_path,monkeypatch):
    app = module("configure_social_links");backup = tmp_path/"backup";backup.mkdir()
    (backup/"before.json").write_text(json.dumps({
        "protected":{}, "previous_urls":{key:"" for key in app.APPROVED}}))
    dropin = tmp_path/"99-social-links.conf";dropin.write_text(app.CONTENT)
    other = tmp_path/"99-public-menu-links.conf";other.write_text("existing runtime and security")
    monkeypatch.setattr(app,"BACKUP",backup);monkeypatch.setattr(app,"DROPIN",dropin)
    monkeypatch.setattr(app,"protected",lambda *a:None)
    readiness = []
    def env(guard):
        assert readiness
        return {}
    stage = SimpleNamespace(wait_ready=lambda values:readiness.append(values),environment=env)
    calls = []
    guard = SimpleNamespace(run=lambda args,**kw:calls.append(args),
        save=lambda p,data:p.write_text(json.dumps(data)))
    app.rollback(stage,guard)
    assert not dropin.exists() and other.read_text()=="existing runtime and security"
    assert [c for c in calls if "restart" in c] == [["systemctl","restart",app.BOT]]
    assert list(backup.glob("rolled-back-*.json"))


@pytest.mark.parametrize("database", ["postgresql+psycopg://fixture@remote.example/test", "postgresql+psycopg://fixture@127.0.0.1/production"])
def test_deployment_refuses_nonisolated_database_without_connecting(database):
    app = module("configure_social_links")
    unit = {"WorkingDirectory":app.RUNTIME,"User":"kudavpn_trial_bot"}
    env = {"KUDAVPN_PILOT_MARKER":"kudavpn-telegram-pilot-v1", "DATABASE_URL":database}
    with pytest.raises(ValueError,match="unexpected_test_runtime"):
        app.verify_isolation(unit,env)

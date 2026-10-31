"""Presentation checks that keep navigation and private access semantics intact."""
from datetime import datetime, timezone

from bot.screens import account, connect, happ, help_center, home, tariffs, tariff_details
from bot.services.account import AccountSnapshot


def test_premium_screens_share_decorative_hierarchy_and_keep_actions():
    snapshot = AccountSnapshot(status='active', tariff_label='1 месяц — 299 ₽',
                               expires_at=datetime(2026, 11, 21, tzinfo=timezone.utc))
    screens = (
        home(premium_account=True), account(snapshot, premium_account=True),
        tariffs(test_payment=True), help_center(''), happ(),
        tariff_details('pilot_month_199', pilot=True),
    )
    for index, screen in enumerate(screens):
        assert 'KudaVPN Premium' in screen.text
        if index:  # The new compact greeting deliberately omits panel dividers.
            assert '◆' in screen.text
            assert any(mark in screen.text for mark in ('┃', '├', '╰'))
        assert screen.keyboard.inline_keyboard
    # Home deliberately uses the approved compact feature/status layout.
    greeting = home(premium_account=True, snapshot=snapshot)
    assert all(label in greeting.text for label in ("KudaVPN Premium", "Свобода начинается здесь", "🔒", "⚡", "🌍"))
    assert len(greeting.text) < 350 and greeting.keyboard.inline_keyboard
    account_screen = screens[1]
    assert 'Действует до: 21.11.2026 · UTC' in account_screen.text
    assert [button.callback_data for row in account_screen.keyboard.inline_keyboard
            for button in row if button.callback_data][:4] == [
                'nav:connect', 'nav:renew', 'nav:subscription', 'nav:devices']
    assert '299 ₽' in screens[2].text
    assert 'Android и iPhone' in screens[4].text


def test_personal_url_stays_in_owner_screen_only():
    personal = 'https://subscriptions.example/s/' + 'a' * 43
    screen = connect(personal, page_url=personal.replace('/s/', '/connect/'))
    assert personal in screen.text
    assert '◆' in screen.text
    assert [button.url for row in screen.keyboard.inline_keyboard for button in row if button.url] == [
        personal.replace('/s/', '/connect/'), personal]
    assert personal not in home(premium_account=True).text

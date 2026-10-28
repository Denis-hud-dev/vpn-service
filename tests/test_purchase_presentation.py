"""Reviewed tariff cards and private Happ delivery; no provider requests."""
from datetime import datetime,timedelta,timezone

import pytest

from bot import screens
from bot.services import PILOT_TARIFFS
from bot.services.account import AccountSnapshot


@pytest.mark.parametrize('plan',PILOT_TARIFFS.values())
def test_tariff_card_uses_catalog_without_creating_or_steering_order(plan):
    card=screens.tariff_details(plan.id,pilot=True)
    assert plan.label in card.text
    assert f'Срок: <b>{plan.months} ' in card.text
    assert f'Стоимость: <b>{plan.price_rub} ₽</b>' in card.text
    assert 'не заказ' in card.text and 'Заказ не создан' in card.text
    assert 'автоматически' in card.text
    assert not any(word in card.text for word in ('https://','Stars','XTR'))
    assert not any(b.url or b.web_app or b.pay for row in card.keyboard.inline_keyboard for b in row)


@pytest.mark.parametrize('page',[None,'https://subscriptions.example/connect/private-fixture'])
def test_premium_happ_keeps_bearer_in_buttons_not_message(page):
    link='https://subscriptions.example/s/'+'a'*43
    screen=screens.connect(link,page_url=page,premium_account=True)
    assert link not in screen.text and '<code>' not in screen.text
    buttons=[b for row in screen.keyboard.inline_keyboard for b in row]
    assert next(b.url for b in buttons if b.text.endswith('Открыть подписку'))==link
    if page:
        assert buttons[0].text=='🚀 Подключиться через Happ'
        assert buttons[0].url==page and buttons[0].style=='primary'
        assert 'Android и iPhone' in screen.text and 'QR' in screen.text
    else:
        assert 'QR' not in screen.text  # No page capability, no invented QR.


@pytest.mark.parametrize('days,label',[(2,'Подписка заканчивается'),(7,'Подписка активна'),(-1,'Срок подписки истёк')])
def test_subscription_labels_follow_real_expiry(days,label):
    snapshot=AccountSnapshot(status='active',expires_at=datetime.now(timezone.utc)+timedelta(days=days))
    assert label in screens.kuda_status(snapshot).text

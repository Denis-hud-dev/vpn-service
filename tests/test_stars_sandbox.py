"""Offline Bot API methods only. No Stars, API requests, DB writes or VPN grants."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
from aiogram.client.telegram import PRODUCTION,TEST
from aiogram.types import Chat,Message,PreCheckoutQuery,SuccessfulPayment,User

from bot.services.stars_sandbox import StarsSandbox,StarsTestOrder,approved_test_order,TEST_STARS_PRICES

NOW=datetime(2026,10,27,tzinfo=timezone.utc)


def order():
    # Owner-approved TEST price; no real Telegram endpoint is called.
    return approved_test_order(uuid4(),501,1,NOW+timedelta(minutes=30))


def query(row,**changes):
    values=dict(id='offline-query',from_user=User(id=501,is_bot=False,first_name='Test'),
                currency='XTR',total_amount=row.stars,invoice_payload=row.payload)
    return PreCheckoutQuery(**{**values,**changes})


def paid(row,**changes):
    values=dict(currency='XTR',total_amount=row.stars,invoice_payload=row.payload,
                telegram_payment_charge_id='offline-charge',provider_payment_charge_id='')
    return Message(message_id=1,date=NOW,chat=Chat(id=501,type='private'),
        from_user=User(id=501,is_bot=False,first_name='Test'),
        successful_payment=SuccessfulPayment(**{**values,**changes}))


def test_disabled_unapproved_and_production_are_rejected():
    with pytest.raises(PermissionError):StarsSandbox().invoice(order(),now=NOW)
    with pytest.raises(PermissionError):StarsSandbox(enabled=True).invoice(replace(order(),price_approved=False),now=NOW)
    with pytest.raises(ValueError):StarsSandbox(enabled=True,api=PRODUCTION)
    assert '/test/' in TEST.base


@pytest.mark.parametrize('bad',[0,-1,True,1.5,'7'])
def test_price_requires_positive_whole_stars(bad):
    with pytest.raises(ValueError):StarsSandbox(enabled=True).invoice(replace(order(),stars=bad),now=NOW)


def test_invoice_uses_official_stars_fields_without_recurrence_or_provider():
    row=order();invoice=StarsSandbox(enabled=True).invoice(row,now=NOW)
    assert invoice.__api_method__=='sendInvoice'
    assert invoice.currency=='XTR' and invoice.provider_token=='' and len(invoice.prices)==1
    assert invoice.prices[0].amount==1 and invoice.chat_id==501 and invoice.payload==row.payload
    assert invoice.start_parameter and invoice.protect_content
    assert not invoice.need_email and not invoice.need_phone_number
    assert 'subscription_period' not in invoice.model_dump(exclude_none=True)


@pytest.mark.parametrize('fault',['foreign','currency','amount','payload','expired','already_paid'])
def test_precheckout_checks_owner_amount_payload_and_state(fault):
    row=order();changes={}
    if fault=='foreign':changes['from_user']=User(id=502,is_bot=False,first_name='Other')
    if fault=='currency':changes['currency']='RUB'
    if fault=='amount':changes['total_amount']=8
    if fault=='payload':changes['invoice_payload']='other-order'
    if fault=='expired':row=replace(row,expires_at=NOW-timedelta(seconds=1))
    if fault=='already_paid':row=replace(row,pending=False)
    answer=StarsSandbox(enabled=True).precheckout(row,query(row,**changes),now=NOW)
    assert answer.__api_method__=='answerPreCheckoutQuery' and answer.ok is False
    assert answer.error_message


def test_precheckout_is_not_fulfillment_and_success_observation_is_stable():
    row=order();adapter=StarsSandbox(enabled=True)
    assert adapter.precheckout(row,query(row),now=NOW).ok is True
    with pytest.raises(ValueError):adapter.inspect_successful_payment(row,Message(message_id=2,date=NOW,
        chat=Chat(id=501,type='private'),from_user=User(id=501,is_bot=False,first_name='Test')))
    first=adapter.inspect_successful_payment(row,paid(row))
    assert adapter.inspect_successful_payment(row,paid(row))==first
    assert first.environment=='telegram_test' and first.currency=='XTR'
    assert adapter.inspect_successful_payment(replace(row,expires_at=NOW-timedelta(days=1)),paid(row))==first
    # Observation only: no lifecycle, provider client or ledger is injected.
    assert not hasattr(adapter,'fulfill')


@pytest.mark.parametrize('changes',[{'currency':'RUB'},{'total_amount':8},{'invoice_payload':'wrong'},
                                   {'telegram_payment_charge_id':''},{'is_recurring':True}])
def test_invalid_success_observations_are_rejected(changes):
    row=order()
    with pytest.raises(ValueError):StarsSandbox(enabled=True).inspect_successful_payment(row,paid(row,**changes))


def test_foreign_and_group_messages_are_rejected():
    row=order();adapter=StarsSandbox(enabled=True)
    for message in (paid(row).model_copy(update={'from_user':User(id=502,is_bot=False,first_name='Other')}),
                    paid(row).model_copy(update={'chat':Chat(id=-100,type='group')})):
        with pytest.raises(ValueError):adapter.inspect_successful_payment(row,message)


@pytest.mark.parametrize('months,stars',[(1,1),(3,2),(12,3)])
def test_owner_approved_test_catalog_keeps_xtr_units(months,stars):
    row=approved_test_order(uuid4(),501,months,NOW+timedelta(minutes=30))
    method=StarsSandbox(enabled=True).invoice(row,now=NOW)
    assert method.prices[0].amount==stars and method.currency=='XTR'
    assert row.months==months and TEST_STARS_PRICES[months]==stars
    with pytest.raises(ValueError):StarsSandbox(enabled=True).invoice(replace(row,stars=stars+1),now=NOW)
    assert not hasattr(method,'return_url')

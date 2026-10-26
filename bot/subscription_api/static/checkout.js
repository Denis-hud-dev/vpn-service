'use strict';
(() => {
  const el = id => document.getElementById(id);
  let csrf = '', order = null, paymentUrl = null, timer = null;
  const money = value => new Intl.NumberFormat('ru-RU').format(value / 100) + ' ₽';
  const states = {
    pending: ['Ожидаем оплату', 'После оплаты дождитесь независимого подтверждения ЮKassa. Возврат из браузера сам по себе не активирует доступ.'],
    succeeded: ['Оплата подтверждена', 'Подписка активирована или продлена. Личный профиль Happ сохраняется.'],
    canceled: ['Платёж отменён', 'Доступ по этому заказу не выдан. При необходимости выберите тариф снова.'],
    expired: ['Время оформления истекло', 'Не оплачивайте этот заказ повторно. Если деньги уже были подтверждены, обновите статус — успешный поздний платёж будет обработан.'],
    refunded: ['Возврат подтверждён', 'Срок доступа скорректирован по подтверждённому возврату. Другие оплаченные периоды сохранены.'],
    refund_review: ['Возврат требует проверки доступа', 'Провайдер подтвердил возврат. Спорная корректировка срока передана на ручную проверку.']
  };
  const errors = {
    provider_unavailable: 'ЮKassa временно недоступна. Заказ мог сохраниться: повторите проверку позже, не оплачивайте повторно.',
    session_unavailable: 'Сеанс истёк или недействителен. Для ограниченного теста нужен новый личный вход, подготовленный владельцем вне Telegram.',
    order_unavailable: 'Заказ недоступен для этого сеанса.',
    request_forbidden: 'Запрос не подтверждён. Обновите страницу и повторите действие.',
    request_unavailable: 'Слишком частые запросы или недоступное действие. Попробуйте немного позже.'
  };
  async function api(path, method = 'GET', body) {
    const response = await fetch('/checkout/api/' + path, {method, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(method === 'POST' && csrf ? {'X-CSRF-Token': csrf} : {})},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(errors[data.error] || 'Сервис временно недоступен. Повторите попытку позже.');
    return data;
  }
  function fail(error) {el('error').textContent = error.message; el('error').hidden = false;}
  function render(data) {
    order = data; el('order-panel').hidden = false; el('error').hidden = true;
    const state = states[data.state] || ['Проверяем платёж', 'Не оплачивайте повторно до завершения проверки.'];
    el('order-status').textContent = state[0]; el('order-description').textContent = state[1];
    el('order-title').textContent = data.label; el('order-price').textContent = money(data.amount_kopecks);
    el('status-dot').className = 'status-dot ' + (data.state === 'succeeded' ? 'success' : data.state === 'pending' ? '' : 'error');
    const provider = el('provider-link'); provider.hidden = data.state !== 'pending' || !paymentUrl;
    if (!provider.hidden) provider.href = paymentUrl; else provider.removeAttribute('href');
    if (data.state === 'pending' && !paymentUrl) el('order-description').textContent += ' Чтобы вернуться к оплате, снова выберите тот же тариф — сохранится текущий заказ.';
    const connect = el('connect-link'); connect.hidden = !data.connect_url;
    if (data.connect_url) connect.href = data.connect_url; else connect.removeAttribute('href');
    const expiry = el('subscription-expiry'); expiry.hidden = !data.subscription_expires_at;
    expiry.textContent = data.subscription_expires_at ? 'Подписка действует до: ' + new Intl.DateTimeFormat('ru-RU', {dateStyle: 'long', timeStyle: 'short', timeZone: 'UTC'}).format(new Date(data.subscription_expires_at)) + ' UTC' : '';
    if (data.access_pending) el('order-description').textContent += ' Доступ к узлу пока готовится. Обновите статус позже.';
    el('receipt-status').textContent = 'Тестовый платёж · не фискальный чек. Продавец — самозанятый (НПД), НДС не применяется.';
    if (timer) clearTimeout(timer);
    if (data.state === 'pending') timer = setTimeout(async () => {
      try {render(await api('orders/' + order.id));} catch (e) {fail(e);}
    }, 5000);
  }
  async function choose(plan, button) {
    button.disabled = true; paymentUrl = null;
    try {const data = await api('orders', 'POST', {tariff_id: plan.id}); paymentUrl = data.payment_url || null; render(data);}
    catch (e) {fail(e);} finally {button.disabled = false;}
  }
  el('refresh').addEventListener('click', async () => {
    if (!order) return;
    el('refresh').disabled = true;
    try {render(await api('orders/' + order.id + '/refresh', 'POST', {}));}
    catch (e) {fail(e);} finally {el('refresh').disabled = false;}
  });
  el('logout').addEventListener('click', async () => {
    try {await api('logout', 'POST', {}); if (timer) clearTimeout(timer); el('checkout-panel').hidden = true;
      el('session-message').hidden = false; el('session-message').textContent = 'Сеанс завершён.';}
    catch (e) {fail(e);}
  });
  async function start() {
    // An operator-issued pilot invitation travels in the fragment, never the
    // request URL. Remove it before any request and keep it out of storage.
    const invite = location.hash.slice(1); history.replaceState(null, '', location.pathname);
    try {
      const login = invite ? await api('session', 'POST', {token: invite}) : await api('session');
      csrf = login.csrf;
      const data = await api('plans');
      for (const plan of data.plans) {
        const card = document.createElement('article'); card.className = 'plan';
        const label = document.createElement('p'); label.textContent = plan.months + (plan.months === 1 ? ' месяц' : plan.months === 3 ? ' месяца' : ' месяцев');
        const price = document.createElement('strong'); price.textContent = money(plan.amount_kopecks);
        const button = document.createElement('button'); button.type = 'button'; button.className = 'button ' + (plan.style === 'success' ? 'green' : 'blue');
        button.textContent = 'Выбрать'; button.addEventListener('click', () => choose(plan, button));
        card.append(label, price, button); el('plans').append(card);
      }
      el('session-message').hidden = true; el('checkout-panel').hidden = false;
      const current = await api('orders/latest'); if (current.order) render(current.order);
    } catch (e) {el('session-message').textContent = e.message;}
  }
  start();
})();

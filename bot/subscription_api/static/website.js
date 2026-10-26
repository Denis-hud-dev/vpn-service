'use strict';
(() => {
  const el = id => document.getElementById(id);
  const money = n => new Intl.NumberFormat('ru-RU').format(n / 100) + ' ₽';
  const date = value => value ? new Date(value).toLocaleString('ru-RU', {day:'numeric',month:'long',year:'numeric'}) : '—';
  let csrf = '', current = null, busy = false, timer = null, paymentUrl = null;
  const status = {pending:'Ожидаем оплату',succeeded:'Оплата подтверждена',canceled:'Платёж отменён',expired:'Окно оплаты истекло',refunded:'Возврат подтверждён',refund_review:'Возврат требует проверки'};
  const receipt = {test_not_required:'Тест · чек НПД не формируется',not_due:'Чек пока не требуется',pending:'Ожидает выдачи чека НПД',issued:'Чек НПД зарегистрирован · ожидает передачи',delivered:'Чек НПД передан покупателю',error:'Ошибка выдачи или доставки чека',void_pending:'Ожидает корректировки после возврата',voided:'Чек аннулирован / обязательство закрыто'};
  async function api(path, data) {
    const response = await fetch('/api/'+path,{method:data === undefined?'GET':'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json',...(data === undefined?{}:{'X-CSRF-Token':csrf})},body:data===undefined?undefined:JSON.stringify(data)});
    const body = await response.json();
    if (!response.ok) {const issue=new Error(response.status===401?'Сеанс закончился. Войдите снова.':response.status===429?'Подождите немного перед повторной попыткой.':'Сейчас действие недоступно. Попробуйте позже.');issue.status=response.status;throw issue;}
    return body;
  }
  function error(message) {el('site-error').textContent=message;el('site-error').hidden=false;}
  function link(element, value) {
    element.hidden=true; element.removeAttribute('href');
    if (!value) return;
    const url=new URL(value,location.origin);
    if (url.protocol!=='https:' && !(location.hostname==='127.0.0.1' && url.origin===location.origin)) return;
    element.href=url.href; element.hidden=false;
  }
  async function account() {
    const data=await api('account'); el('account-panel').hidden=false;el('auth-message').hidden=true;
    const labels={active:'Подписка активна',expired:'Срок подписки истёк',inactive:'Подписки пока нет',pending:'Ожидает активации',blocked:'Доступ ограничен'};
    el('account-status').textContent=labels[data.status]||'Статус недоступен';
    el('account-dot').className='status-dot'+(data.status==='active'?' success':'');
    el('account-plan').textContent=data.tariff||'Ваш следующий шаг — выбрать подписку';
    el('account-expiry').textContent=date(data.expires_at);
    el('renew-link').textContent=data.status==='inactive'?'Выбрать подписку':'Продлить';
    const seconds=data.expires_at ? (new Date(data.expires_at)-Date.now())/1000 : 0;
    el('account-left').textContent=seconds>86400?Math.ceil(seconds/86400)+' дн.':seconds>0?Math.ceil(seconds/3600)+' ч.':'—';
    el('node-status').textContent=data.nodes.length?'Узлы подписки: '+data.nodes.join(' · '):'Список доступных узлов пока не подтверждён.';
    el('access-status').textContent=data.access_pending?'Подписка активна. Доступ готовится; повторите проверку позже.':'';
    link(el('happ-link'),data.connect_url);
    const payments=await api('payments'); el('history-panel').hidden=false; el('payment-history').replaceChildren();
    if (!payments.payments.length) {const p=document.createElement('p');p.className='small';p.textContent='Покупок пока нет.';el('payment-history').append(p);}
    for (const p of payments.payments) {
      const row=document.createElement('article');row.className='payment-row';
      for (const [title,caption] of [[p.tariff,date(p.created_at)],[money(p.amount_kopecks),status[p.status]||'Статус уточняется'],[receipt[p.receipt]||'Проверка чека',p.receipt_error?'Требуется проверка поддержки':'']]) {
        const box=document.createElement('div'), strong=document.createElement('strong'), small=document.createElement('p');strong.textContent=title;small.textContent=caption;box.append(strong,small);row.append(box);
      }
      el('payment-history').append(row);
    }
  }
  async function render(order) {
    if (!current || current.id!==order.id) paymentUrl=null;
    if (order.payment_url) paymentUrl=order.payment_url;
    current=order; el('order-panel').hidden=false;el('order-status').textContent=status[order.state]||'Статус уточняется';
    el('order-summary').textContent=order.label+' · тестовый заказ';
    link(el('pay-link'),order.state==='pending'?paymentUrl:null);
    el('order-note').textContent=order.state==='succeeded'?'Срок добавлен к подписке один раз. Профиль Happ сохраняется.':order.state==='pending'?'Данные карты вводятся только в форме провайдера. Подтверждение проверяет сервер.':'Если нужна помощь, откройте поддержку. Повторное нажатие проверки не добавляет срок.';
    if (order.state==='succeeded'||order.state==='refunded') await account();
    clearTimeout(timer);
    if (order.state==='pending') timer=setTimeout(refresh,8000);
  }
  async function refresh() {if (!current||busy)return;busy=true;try{await render(await api('orders/'+current.id+'/refresh',{}));}catch(e){error(e.message);}finally{busy=false;}}
  async function choose(plan,button) {
    if (!csrf) {el('auth-message').hidden=false;el('auth-message').scrollIntoView({block:'center'});return;}
    if(busy)return;busy=true;button.disabled=true;el('site-error').hidden=true;
    try{await render(await api('orders',{tariff_id:plan.id}));el('order-panel').scrollIntoView({block:'center'});}catch(e){error(e.message);}finally{busy=false;button.disabled=false;}
  }
  async function start() {
    try {
      const data=await api('plans');
      for (const plan of data.plans) {
        const card=document.createElement('article');card.className='plan';
        const note=document.createElement('p');note.className='plan-note';note.textContent=plan.months===1?'Начните с одного месяца':plan.months===3?'Ваш привычный ритм':'Доступ на целый год';
        const label=document.createElement('p');label.textContent=plan.months+(plan.months===1?' месяц':plan.months===3?' месяца':' месяцев');
        const price=document.createElement('strong');price.textContent=money(plan.amount_kopecks);
        const button=document.createElement('button');button.type='button';button.className='button '+(plan.months===1?'green':'blue');button.textContent='Выбрать';button.addEventListener('click',()=>choose(plan,button));
        card.append(note,label,price,button);el('plan-cards').append(card);
      }
      try {const session=await api('session');csrf=session.csrf;await account();const latest=await api('orders/latest');if(latest.order)await render(latest.order);}catch(e) {if(e.status===401){csrf='';el('auth-message').hidden=false;if(location.pathname==='/account')el('auth-message').scrollIntoView({block:'center'});}else{error(e.message);}}
    }catch(e){error(e.message);}
  }
  el('refresh-order').addEventListener('click',refresh);
  el('logout').addEventListener('click',async()=>{try{await api('logout',{});location.href='/';}catch(e){error(e.message);}});
  start();
})();

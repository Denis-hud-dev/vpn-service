"""Explicit plan review precedes any provider order; synthetic browser only."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which('node')
SCRIPT = Path(__file__).resolve().parents[1]/'bot/subscription_api/static/website.js'


@pytest.mark.skipif(NODE is None, reason='Node.js unavailable')
@pytest.mark.parametrize('scenario', ['success', 'confirmation_only', 'cancel', 'no_methods', 'no_auth', 'timeout', 'expired', 'double_click'])
def test_confirmation_and_automatic_success_with_happ(scenario):
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scenario=SCENARIO,nodes={},planButtons=[],posts=[],redirects=[];
let scheduled=null, refreshed=0;
function element(){return {hidden:true,textContent:'',value:'',children:[],firstChild:{textContent:''},
 addEventListener(name,fn){this[name]=fn;},removeAttribute(name){delete this[name];},
 replaceChildren(){this.children=[];},append(...items){this.children.push(...items);},scrollIntoView(){}};}
const el=id=>nodes[id]||(nodes[id]=element());
const document={getElementById:el,createElement(type){const e=element();if(type==='button')planButtons.push(e);return e;}};
const location={origin:'https://site.example',hostname:'site.example',pathname:'/account',assign(url){redirects.push(url);}};
const plan={id:'pilot_month_199',months:1,amount_kopecks:19900};
const order={id:'fixture',state:'pending',label:'1 месяц — 199 ₽',test:true,currency:'RUB',amount_kopecks:19900,months:1,payment_url:'https://yoomoney.ru/fixture'};
async function fetch(path,opts){
 let status=200,data;
 if(path==='/api/plans')data={plans:[plan],read_only:false,direct_checkout:scenario!=='confirmation_only',order_confirmation:true,methods:scenario==='no_methods'?[]:['bank_card','yoo_money']};
 else if(path==='/api/session'){data={csrf:'fixture'};if(scenario==='no_auth')status=401;}
 else if(path==='/api/account')data={status:refreshed?'active':'inactive',nodes:[],expires_at:refreshed?'2030-02-01T00:00:00Z':null};
 else if(path==='/api/payments')data={payments:[]};
 else if(path==='/api/orders/latest')data={order:null};
 else if(path==='/api/orders'){posts.push(JSON.parse(opts.body));data=order;if(scenario==='timeout')status=503;}
 else if(path.endsWith('/refresh')){refreshed++;data={...order,state:scenario==='expired'&&refreshed===1?'expired':'succeeded',subscription_expires_at:'2030-02-01T00:00:00Z',connect_url:location.origin+'/connect',access_pending:false};}
 else throw new Error('unexpected fetch');
 return {ok:status===200,status,json:async()=>data};
}
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,location,fetch,URL,Intl,Date,Error,
 setTimeout(fn){scheduled=fn;return 1;},clearTimeout(){scheduled=null;}});
const settle=()=>new Promise(setImmediate);
(async()=>{
 await settle();await planButtons[0].click();await settle();
 assert(posts.length===0 && redirects.length===0);
 if(scenario==='no_auth'){assert(el('auth-message').hidden===false);return;}
 assert(!el('confirm-panel').hidden && el('confirm-summary').textContent.includes('199'));
 assert(el('confirm-note').textContent.includes('СБП'));
 if(scenario==='cancel'){await el('cancel-confirm').click();assert(el('confirm-panel').hidden);return;}
 if(scenario==='no_methods'){assert(el('confirm-order').disabled);return;}
 assert(el('confirm-method').children[0].textContent==='Карта / ЮMoney');
 const first=el('confirm-order').click();
 if(scenario==='double_click')await el('confirm-order').click();
 await first;await settle();assert(posts.length===1 && posts[0].tariff_id===plan.id);
 if(scenario==='timeout'){assert(redirects.length===0 && !el('site-error').hidden);return;}
 assert(redirects.length===1 && redirects[0]===order.payment_url);
 assert(scheduled);await scheduled();await settle();
 if(scenario==='expired'){assert(scheduled);await scheduled();await settle();}
 assert(el('order-status').textContent==='Оплата подтверждена');
 assert(!el('order-connect').hidden && el('order-connect').href===location.origin+'/connect');
 assert(el('account-status').textContent==='Подписка активна');
 assert(el('account-expiry').textContent.includes('2030'));
 assert(posts.length===1 && redirects.length===1);
})().catch(e=>{console.error(e.message);process.exitCode=1;});
'''.replace('SCENARIO', json.dumps(scenario))
    result = subprocess.run([NODE, '-e', harness, str(SCRIPT)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

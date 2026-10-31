"""Run payment browser recovery with synthetic fetch/DOM; never hit a real shop."""
from pathlib import Path
import shutil
import subprocess

import pytest

NODE=shutil.which('node')
SCRIPT=Path(__file__).resolve().parents[1]/'bot/subscription_api/static/website.js'


@pytest.mark.skipif(NODE is None,reason='Node.js unavailable')
@pytest.mark.parametrize('failure',[503,401])
def test_payment_page_retry_and_auth_expiry(failure):
    harness=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let scheduled=null, refreshes=0, reads=0, logged=false;
const elements={};
function el(id){return elements[id]||(elements[id]={hidden:true,href:'',textContent:'',className:'',firstChild:{textContent:''},
  addEventListener(name,fn){this[name]=fn;},removeAttribute(name){delete this[name];},replaceChildren(){},append(){},scrollIntoView(){}});}
const document={getElementById:el,createElement:()=>({append(){}})};
const location={origin:'https://review.example',hostname:'review.example',pathname:'/account'};
const order={id:'fixture',state:'pending',label:'1 месяц — 199 ₽',payment_url:'https://yoomoney.ru/fixture'};
async function fetch(path,opts){
 let status=200,data;
 if(path==='/api/plans')data={plans:[],read_only:false};
 else if(path==='/api/session')data={csrf:'fixture'};
 else if(path==='/api/account'){reads++;data={status:'active',nodes:[],expires_at:null,tariff:'Stored plan'};}
 else if(path==='/api/payments')data={payments:[]};
 else if(path==='/api/orders/latest')data={order};
 else if(path.endsWith('/refresh')){refreshes++;status=refreshes===1?FAILURE:200;data={...order,state:'succeeded',access_pending:refreshes===2};}
 else throw new Error('Unexpected request');
 return {ok:status===200,status,json:async()=>data};
}
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,location,fetch,URL,Intl,Date,Error,
  setTimeout(fn,delay){scheduled={fn,delay};return 1;},clearTimeout(){scheduled=null;}});
async function settle(){await new Promise(setImmediate);}
(async()=>{
 await settle();assert(scheduled && scheduled.delay===8000);assert(el('pay-link').href===order.payment_url);
 let callback=scheduled.fn;scheduled=null;await callback();await settle();
 if(FAILURE===401){assert(scheduled===null);assert(el('site-error').textContent.includes('Войдите снова'));return;}
 assert(scheduled && scheduled.delay===16000);
 callback=scheduled.fn;scheduled=null;await callback();await settle();
 assert(scheduled && scheduled.delay===8000);assert(el('order-note').textContent.includes('повторная оплата не нужна'));
 callback=scheduled.fn;scheduled=null;await callback();await settle();
 assert(scheduled===null && reads===3);assert(el('pay-link').hidden);assert(!el('pay-link').href);
})().catch(e=>{console.error(e.message);process.exitCode=1;});
'''.replace('FAILURE',str(failure))
    result=subprocess.run([NODE,'-e',harness,str(SCRIPT)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr

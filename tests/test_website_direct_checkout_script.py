"""Only explicit website pay clicks may open a verified provider URL."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE=shutil.which('node')
SCRIPT=Path(__file__).resolve().parents[1]/'bot/subscription_api/static/website.js'


@pytest.mark.skipif(NODE is None,reason='Node.js unavailable')
@pytest.mark.parametrize('fault',['valid','disabled','reload','old_html','evil_host','http','userinfo','port','fragment',
                                  'amount','months','live','canceled','expired','succeeded','api_error'])
def test_explicit_pay_navigation_is_scoped_to_matching_order(fault):
    harness=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fault=FAULT, nodes={}, buttons=[], redirects=[], requests=[];
function element(){return {hidden:true,textContent:'',className:'',firstChild:{textContent:''},
 addEventListener(name,fn){this[name]=fn;},removeAttribute(name){delete this[name];},replaceChildren(){},append(){},scrollIntoView(){}};}
const el=id=>nodes[id]||(nodes[id]=element());
const document={getElementById(id){return fault==='old_html'&&id==='order-connect'?null:el(id);},createElement(type){let e=element();if(type==='button')buttons.push(e);return e;}};
const location={origin:'https://review.example',hostname:'review.example',pathname:'/account',assign(url){redirects.push(url);}};
const plan={id:'pilot_month_199',amount_kopecks:19900,months:1};
const order={id:'fixture',state:'pending',label:'1 месяц — 199 ₽',test:true,currency:'RUB',amount_kopecks:19900,months:1,payment_url:'https://yoomoney.ru/fixture'};
const badURLs={evil_host:'https://yoomoney.ru.evil.example/pay',http:'http://yoomoney.ru/pay',userinfo:'https://someone@yoomoney.ru/pay',port:'https://yoomoney.ru:8443/pay',fragment:'https://yoomoney.ru/pay#fragment'};
if(badURLs[fault])order.payment_url=badURLs[fault];
if(fault==='amount')order.amount_kopecks=1;
if(fault==='months')order.months=12;
if(fault==='live')order.test=false;
if(['canceled','expired','succeeded'].includes(fault))order.state=fault;
if(fault==='succeeded'){order.connect_url=location.origin+'/connect';order.access_pending=false;}
async function fetch(path,opts){
 requests.push([path,opts.method]);let status=200,data;
 if(path==='/api/plans')data={plans:[plan],direct_checkout:fault!=='disabled',read_only:false};
 else if(path==='/api/session')data={csrf:'fixture'};
 else if(path==='/api/account')data={status:'active',nodes:[],expires_at:null,tariff:'Stored plan'};
 else if(path==='/api/payments')data={payments:[]};
 else if(path==='/api/orders/latest')data={order:fault==='reload'?order:null};
 else if(path==='/api/orders'){data=order;if(fault==='api_error')status=503;}
 else throw new Error('Unexpected request');
 return {ok:status===200,status,json:async()=>data};
}
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,location,fetch,URL,Intl,Date,Error,setTimeout(){return 1;},clearTimeout(){}});
(async()=>{
 await new Promise(setImmediate);assert(redirects.length===0);
 assert(requests.filter(([p,m])=>m==='POST').length===0);
 if(fault==='reload')return;
 assert(buttons.length===1 && buttons[0].textContent===(fault==='disabled'?'Выбрать':'К тестовой оплате ↗'));
 await buttons[0].click();await new Promise(setImmediate);
 assert(requests.filter(([p,m])=>p==='/api/orders'&&m==='POST').length===1);
 assert(redirects.length===(['valid','old_html'].includes(fault)?1:0));
 if(fault==='valid')assert(redirects[0]===order.payment_url);
 if(fault==='succeeded')assert(!el('order-connect').hidden&&el('order-connect').href===location.origin+'/connect');
 if(['amount','months','live','api_error',...Object.keys(badURLs)].includes(fault)){
   assert(el('site-error').hidden===false);assert(el('pay-link').hidden);
 }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
'''.replace('FAULT',json.dumps(fault))
    result=subprocess.run([NODE,'-e',harness,str(SCRIPT)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr

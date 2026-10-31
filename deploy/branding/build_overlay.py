"""Build a visual-only overlay over pinned, already deployed TEST runtimes."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

APPROVED='ca0d10ece2f3026a69c08c36f5759ed3fa0d1220'
ROOT=Path(__file__).resolve().parents[2]

def blob(rev,name):
    return subprocess.check_output(['git','show',rev+':'+name],cwd=ROOT)

def sha(data):return hashlib.sha256(data).hexdigest()

def asset_overlay(previous,current,names):
    """Replace only static image handlers, leaving auth/payment/HTTP routes exact."""
    tree=ast.parse(previous);approved=ast.parse(current)
    lines=previous.splitlines(keepends=True)
    for name in names:
        old=[n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name]
        new=[n for n in ast.walk(approved) if isinstance(n,ast.AsyncFunctionDef) and n.name==name]
        if len(old)!=1 or len(new)!=1:raise ValueError('Ambiguous image handler')
        node=old[0];replacement=ast.get_source_segment(current,new[0])
        # Both nested handlers use four-space indentation in these pinned versions.
        replacement='    '+replacement+'\n'
        lines[node.lineno-1:node.end_lineno]=[replacement]
        previous=''.join(lines);tree=ast.parse(previous);lines=previous.splitlines(keepends=True)
    previous='from bot.branding import BRAND_IMAGES, brand_image\n'+previous
    check=ast.parse(previous)
    def scrub(tree):
        class StaticOnly(ast.NodeTransformer):
            def visit_AsyncFunctionDef(self,node):
                if node.name in names:node.body=[ast.Pass()]
                return self.generic_visit(node)
        tree=StaticOnly().visit(tree)
        tree.body=[n for n in tree.body if not(isinstance(n,ast.ImportFrom) and n.module=='bot.branding')]
        return ast.dump(tree,include_attributes=False)
    if scrub(check)!=scrub(ast.parse(blob('0d45896' if 'login_asset' in names else 'ecf526d',
        'bot/subscription_api/website.py' if 'login_asset' in names else 'bot/subscription_api/app.py').decode())):
        raise ValueError('Non-visual AST changed')
    return previous.encode()


def build(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    stages={
        'website':dict(unit='kudavpn-website-review.service',old='/opt/kudavpn-website-review/site-release-0d45896c9317',restart=True),
        'feed':dict(unit='kudavpn-telegram-pilot-feed.service',old='/opt/kudavpn-telegram-pilot/app-orbit-ecf526d-feed',restart=True),
        'checkout':dict(unit='kudavpn-test-checkout.service',old='/opt/kudavpn-telegram-pilot/app-checkout-08fbff6',restart=False),
        'bot':dict(unit='kudavpn-telegram-pilot-bot.service',old='/opt/kudavpn-telegram-pilot/app-purchase-menu-707e130cec22',restart=False),
    }
    def put(stage,name,data,base=None):
        if name.startswith('/') or '..' in Path(name).parts:raise ValueError('Unsafe path')
        p=output/stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        stages[stage].setdefault('files',{})[name]={'before':sha(blob(base,name)) if base else None,'after':sha(data)}
    for stage,base,module,handlers in [('website','0d45896','website.py',('asset','login_asset')),('feed','ecf526d','app.py',('asset',))]:
        name='bot/subscription_api/'+module
        put(stage,name,asset_overlay(blob(base,name).decode(),blob(APPROVED,name).decode(),handlers),base)
        put(stage,'bot/subscription_api/connect_page.py',blob(APPROVED,'bot/subscription_api/connect_page.py'),base)
        put(stage,'bot/branding.py',blob(APPROVED,'bot/branding.py'))
        for name in subprocess.check_output(['git','ls-tree','-r','--name-only',APPROVED,'bot/assets/brand'],cwd=ROOT,text=True).splitlines():
            put(stage,name,blob(APPROVED,name))
        for name in ('connect.css',):
            path='bot/subscription_api/static/'+name;put(stage,path,blob(APPROVED,path),base)
    prefix='bot/subscription_api/static/'
    for name in ('website.css','website-login.css','checkout.css'):
        put('website',prefix+name,blob(APPROVED,prefix+name),'0d45896')
    previous=blob('0d45896',prefix+'website.html').decode()
    latest=blob(APPROVED,prefix+'website.html').decode()
    # Keep old checkout DOM, texts, scripts and legal links; replace only brand markup.
    previous=previous.replace('<img src="/assets/logo.png" alt="">','<img class="brand-logo" src="/assets/logo-small.png" alt="" width="72" height="72">')
    start='<div class="orbit-art" aria-hidden="true">'
    previous=re.sub(re.escape(start)+r'.*?</div></section>',
        re.search(re.escape(start)+r'.*?</div></section>',latest).group(0),previous,count=1)
    previous=previous.replace('</head>','<link rel="icon" type="image/vnd.microsoft.icon" href="/assets/favicon.ico"></head>')
    put('website',prefix+'website.html',previous.encode(),'0d45896')
    put('website',prefix+'website-login.html',blob(APPROVED,prefix+'website-login.html'),'0d45896')
    for name in ('site-terms.html','site-privacy.html','site-support.html'):
        old=blob('0d45896',prefix+name).decode().replace('<a class="brand" href="/">','<a class="brand" href="/"><img class="brand-logo" src="/assets/logo-small.png" alt="" width="72" height="72">')
        old=old.replace('</head>','<link rel="icon" type="image/vnd.microsoft.icon" href="/assets/favicon.ico"></head>')
        put('website',prefix+name,old.encode(),'0d45896')
    # These existing handlers read the file per request: no bot/payment restart.
    put('bot','bot/assets/welcome-premium.png',blob(APPROVED,'bot/assets/brand/telegram-welcome.png'),'707e130')
    put('checkout',prefix+'kudavpn-orbit-emblem.png',blob(APPROVED,'bot/assets/brand/logo-192.png'),'08fbff6')
    html=blob('08fbff6',prefix+'checkout.html').decode().replace('</head>','<link rel="icon" type="image/png" href="/checkout/assets/logo.png"></head>')
    put('checkout',prefix+'checkout.html',html.encode(),'08fbff6')
    manifest={'approved':APPROVED,'stages':stages}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest

if __name__=='__main__':
    build(sys.argv[1]);print('visual_overlay=prepared')

#!/usr/bin/env python3
"""Extend Quiet Current, rendering NEW shapes only. Legacy WEBM are read-only."""
from __future__ import annotations
import argparse
import ast
import csv
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import emoji_legacy_renderer as legacy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/telegram-emoji-extension'
NAMES = ('info','settings','apple','windows','android','linux','ios','macos',
         'smart_tv','promo','referral','channel','community','notification','back','home')
REUSE = {'buy_vpn': 'premium', 'faq': 'support'}
LEGACY_COMMIT = 'b630c3b'
C,I,M,G,E = legacy.CYAN,legacy.ICE,legacy.MUTED,legacy.GRAPHITE,legacy.EDGE


def bezier(start, segments):
    points = [start]
    for a,b,end in segments:
        p = points[-1]
        for k in range(1,13):
            t=k/12;u=1-t
            points.append((u**3*p[0]+3*u*u*t*a[0]+3*u*t*t*b[0]+t**3*end[0],
                           u**3*p[1]+3*u*u*t*a[1]+3*u*t*t*b[1]+t**3*end[1]))
    return points


def apple(g, cx=50, cy=48, scale=1):
    body=bezier((50,37), [((42,31),(33,35),(31,44)),((28,55),(38,72),(44,72)),
        ((48,72),(49,68),(54,69)),((62,75),(67,65),(70,58)),
        ((62,54),(61,45),(69,40)),((65,33),(58,32),(50,37))])
    leaf=bezier((50,32), [((50,26),(56,23),(61,23)),((61,29),(56,34),(50,32))])
    transform=lambda ps:[(cx+(x-50)*scale,cy+(y-48)*scale) for x,y in ps]
    g.poly(transform(body),I,max(0.8,scale),I)
    g.poly(transform(leaf),C,max(0.8,scale),C)


def symbol(name):
    g=legacy.Graphic()
    if name=='info':
        g.circle(50,50,21,I,3.5);g.circle(50,39,2,C,2,C)
        g.line([(50,48),(50,62)],I,4);g.line([(45,62),(55,62)],C,3)
    elif name=='settings':
        points=[]
        for k in range(32):
            a=2*math.pi*(k-0.5)/32;r=23 if k%4 in (0,3) else 18
            points.append((50+r*math.cos(a),50+r*math.sin(a)))
        g.poly(points+[points[0]],I,3.2);g.circle(50,50,7.5,C,3.5)
    elif name=='apple':apple(g)
    elif name=='windows':
        for points,color in [([(29,34),(47,31),(47,48),(29,48),(29,34)],I),
            ([(52,30),(72,27),(72,48),(52,48),(52,30)],I),
            ([(29,53),(47,53),(47,69),(29,66),(29,53)],I),
            ([(52,53),(72,53),(72,74),(52,70),(52,53)],C)]:
            g.poly(points,color,1.1,color)
    elif name=='android':
        head=[(50+16*math.cos(math.pi+math.pi*k/24),43+14*math.sin(math.pi+math.pi*k/24)) for k in range(25)]
        g.poly(head+[head[0]],I,1.2,I)
        g.line([(39,32),(35,26)],C,2.5);g.line([(61,32),(65,26)],C,2.5)
        g.circle(43,37.5,1.4,G,1,G);g.circle(57,37.5,1.4,G,1,G)
        g.rect((34,47,66,64),3,I,2,I)
        g.line([(28,49),(28,61)],I,5);g.line([(72,49),(72,61)],I,5)
        g.line([(42,64),(42,72)],C,5);g.line([(58,64),(58,72)],C,5)
    elif name=='linux':
        g.poly([(39,43),(33,59),(37,67),(63,67),(67,59),(61,43),(39,43)],I,2.4,G)
        g.circle(50,39,12,I,2.5,G);g.circle(50,56,11,I,1,I)
        g.circle(46,37,2.6,I,1,I);g.circle(54,37,2.6,I,1,I)
        g.circle(46.5,37.5,1,G,1,G);g.circle(53.5,37.5,1,G,1,G)
        g.poly([(45,43),(50,47),(55,43),(50,41),(45,43)],C,1,C)
        g.poly([(40,64),(33,71),(47,71),(46,65),(40,64)],C,1,C)
        g.poly([(60,64),(67,71),(53,71),(54,65),(60,64)],C,1,C)
    elif name=='ios':
        g.rect((35,26,65,74),5,I,3)
        g.line([(44,31),(56,31)],M,2);g.line([(45,69),(55,69)],C,2.4)
        apple(g,50,49,0.44)
    elif name=='macos':
        g.rect((27,30,73,61),4,I,3)
        apple(g,50,44.5,0.43)
        g.line([(30,57),(70,57)],M,1.8)
        g.line([(50,62),(50,70)],C,3);g.line([(40,71),(60,71)],I,3)
    elif name=='smart_tv':
        g.rect((27,32,73,62),4,I,3)
        g.arc((36,39,64,62),215,325,C,2.8)
        g.arc((42,45,58,61),215,325,I,2.8)
        g.circle(50,55,1.7,C,1.5,C)
        g.line([(39,63),(35,70)],I,3);g.line([(61,63),(65,70)],I,3)
    elif name=='promo':
        g.rect((27,35,73,65),5,I,3)
        g.circle(27,50,4,G,2,G);g.circle(73,50,4,G,2,G)
        g.line([(45,58),(55,42)],C,3.5)
        g.circle(40,43,3,I,2.4);g.circle(60,57,3,I,2.4)
    elif name=='referral':
        g.circle(43,39,8,I,3)
        g.arc((28,50,58,77),190,350,I,3.5)
        g.line([(65,40),(65,56)],C,3.5);g.line([(57,48),(73,48)],C,3.5)
    elif name=='channel':
        g.poly([(27,47),(73,29),(61,72),(48,58),(39,65),(39,53),(27,47)],I,3)
        g.line([(40,53),(64,37),(48,58)],C,2.8)
    elif name=='community':
        g.circle(50,36,7,I,3);g.circle(33,43,5.5,C,2.8);g.circle(67,43,5.5,C,2.8)
        g.arc((37,46,63,72),190,350,I,3.5)
        g.arc((23,51,43,72),190,275,C,3);g.arc((57,51,77,72),265,350,C,3)
    elif name=='notification':
        bell=bezier((50,31),[((39,31),(36,39),(36,49))])
        bell += [(31,60),(69,60),(64,49)]
        bell += bezier((64,49),[((64,39),(61,31),(50,31))])[1:]
        g.poly(bell,I,3.4)
        g.arc((44,59,56,73),15,165,C,3.5);g.circle(50,27,1.8,C,1.5,C)
    elif name=='back':
        g.line([(58,30),(38,50),(58,70)],I,4)
        g.line([(39,50),(72,50)],C,3.5)
    elif name=='home':
        g.line([(28,47),(50,29),(72,47)],I,3.8)
        g.line([(35,44),(35,69),(65,69),(65,44)],I,3.5)
        g.line([(45,69),(45,54),(55,54),(55,69)],C,3)
    else:raise ValueError(name)
    return g


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def old_ids():
    tree=ast.parse((ROOT/'bot/premium_emoji.py').read_text())
    for n in tree.body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EMOJI' for t in n.targets):
            return {ast.literal_eval(k):ast.literal_eval(v.args[0]) for k,v in zip(n.value.keys,n.value.values) if ast.literal_eval(k) in old_hashes()}
    raise ValueError('Published IDs missing')


def old_hashes():
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',LEGACY_COMMIT,'assets/telegram-emoji/webm'],cwd=ROOT,text=True).splitlines()
    return {Path(name).stem:hashlib.sha256(subprocess.check_output(['git','show',LEGACY_COMMIT+':'+name],cwd=ROOT)).hexdigest() for name in names}


def render(name,ffmpeg):
    graphic=symbol(name)
    mark=legacy.Mark(name,name,'',name)
    legacy.write_svg(mark,graphic,OUT/'source'/f'{name}.svg')
    legacy.frame(graphic,0).save(OUT/'png'/f'{name}.png')
    assert legacy.frame(graphic,0).tobytes()==legacy.frame(graphic,legacy.FRAMES).tobytes()
    destination=OUT/'new-webm'/f'{name}.webm'
    assert not destination.exists(), 'Never overwrite an export implicitly'
    command=[ffmpeg,'-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','rgba',
        '-s','100x100','-r','24','-i','-','-an','-c:v','libvpx-vp9','-pix_fmt','yuva420p',
        '-auto-alt-ref','0','-b:v','0','-crf','36','-deadline','good','-cpu-used','3',str(destination)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    for tick in range(legacy.FRAMES):process.stdin.write(legacy.frame(graphic,tick).tobytes())
    process.stdin.close();error=process.stderr.read()
    if process.wait():raise RuntimeError(error.decode()[-300:])
    print(name, destination.stat().st_size, 'bytes', flush=True)


def preview(original):
    names=['buy_vpn',*NAMES[:1],*NAMES[1:],'faq']
    paths={n:OUT/'png'/f'{n}.png' for n in NAMES}
    for alias,old in REUSE.items():
        raw=subprocess.check_output(['git','show',LEGACY_COMMIT+':assets/telegram-emoji/png/'+old+'.png'],cwd=ROOT)
        p=OUT/'png'/f'{alias}.png';p.write_bytes(raw);paths[alias]=p
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',15)
    small=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',12)
    columns=6;cellw=180;cellh=174;head=58
    board=Image.new('RGB',(columns*cellw,head+3*cellh),'#070F19');draw=ImageDraw.Draw(board)
    draw.text((22,18),'KudaVPN / Quiet Current - extension',font=font,fill=I)
    draw.text((22,39),'16 new animations + 2 existing icons. Original palette / 2 s loops.',font=small,fill=C)
    for idx,name in enumerate(names):
        x=(idx%columns)*cellw;y=head+(idx//columns)*cellh
        im=Image.open(paths[name]).convert('RGBA');board.paste(im,(x+40,y+2),im)
        draw.text((x+15,y+104),name+(' [reuse]' if name in REUSE else ''),font=small,fill=I)
        for j,size in enumerate((20,24,32)):
            tiny=im.resize((size,size),Image.Resampling.LANCZOS);board.paste(tiny,(x+24+j*43,y+132),tiny)
    board.save(OUT/'preview/contact-sheet.png')
    # Animated preview only; upload source is always VP9 WEBM.
    frames=[]
    for tick in range(0,48,2):
        sheet=Image.new('RGB',(6*120,3*142),'#070F19');d=ImageDraw.Draw(sheet)
        for idx,name in enumerate(names):
            x=(idx%6)*120;y=(idx//6)*142
            graphic=symbol(name) if name not in REUSE else None
            if graphic is None:
                # Decode existing files once outside preview frame loop below.
                image=original[name][tick]
            else:image=legacy.frame(graphic,tick)
            sheet.paste(image,(x+10,y),image);d.text((x+8,y+112),name,font=small,fill=I)
        frames.append(sheet)
    frames[0].save(OUT/'preview/animated-grid.gif',save_all=True,append_images=frames[1:],duration=[83,83,84]*8,loop=0,optimize=True)
    items=''.join('<article><video autoplay loop muted playsinline src="../'+('reused-existing/' if n in REUSE else 'new-webm/')+n+'.webm"></video><p>'+n+(' / existing' if n in REUSE else '')+'</p></article>' for n in names)
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KudaVPN emoji extension</title><style>body{background:#070f19;color:#d9f7ff;font:16px system-ui;margin:28px}h1{color:#55d6ff}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px}article{border:1px solid #315165;border-radius:16px;padding:16px;text-align:center}video{width:100px;height:100px}button{background:#111a22;color:#55d6ff;border:1px solid #315165;border-radius:8px;padding:10px}body.light{background:#e6eef3;color:#111a22}@media(prefers-reduced-motion:reduce){video{display:none}article{background-image:var(--poster);background-repeat:no-repeat;background-position:top center;padding-top:110px}}</style><h1>KudaVPN / Quiet Current</h1><p>16 new VP9 animations + 2 unchanged reused files. Click any animation to pause/play.</p><button id="theme">Light / dark preview</button><main>'''+items+'''</main><script>document.querySelector('#theme').onclick=()=>document.body.classList.toggle('light');for(const v of document.querySelectorAll('video')){v.onclick=()=>v.paused?v.play():v.pause();v.parentElement.style.setProperty('--poster',`url(../png/${v.getAttribute('src').split('/').pop().replace('.webm','.png')})`);if(matchMedia('(prefers-reduced-motion:reduce)').matches)v.pause()}</script></html>'''
    (OUT/'preview/index.html').write_text(html)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--original-webm-dir',type=Path,required=True)
    args=parser.parse_args();original=args.original_webm_dir
    hashes=old_hashes();assert len(hashes)==14
    assert all(sha(original/(n+'.webm'))==h for n,h in hashes.items()), 'Original pack differs from committed originals'
    for name in ('source','new-webm','reused-existing','png','preview'):(OUT/name).mkdir(parents=True,exist_ok=True)
    ffmpeg=legacy.ffmpeg_path()
    for name in NAMES:render(name,ffmpeg)
    decoded={}
    for alias,name in REUSE.items():
        shutil.copy2(original/(name+'.webm'),OUT/'reused-existing'/f'{alias}.webm')
        raw=subprocess.check_output([ffmpeg,'-v','error','-c:v','libvpx-vp9','-i',str(original/(name+'.webm')),'-f','rawvideo','-pix_fmt','rgba','-'])
        decoded[alias]=[Image.frombytes('RGBA',(100,100),raw[k*40000:(k+1)*40000]) for k in range(48)]
    assert all(sha(original/(n+'.webm'))==h for n,h in hashes.items())
    spec=json.loads((OUT/'source/labels.json').read_text())
    ids=old_ids();entries=[]
    for name in spec:
        old=REUSE.get(name)
        entries.append({'filename':name+'.webm',**spec[name],'reuse_existing':old,
            'custom_emoji_id':ids[old] if old else None,'sha256':sha(OUT/('reused-existing' if old else 'new-webm')/(name+'.webm'))})
    manifest={'name':'KudaVPNPremium','integration_enabled':False,'original_commit':LEGACY_COMMIT,
        'duration_seconds':2,'fps':24,'dimensions':[100,100],'codec':'vp9','audio':False,
        'palette':{'graphite':G,'cyan':C,'ice':I,'edge':E,'muted':M},
        'original_sha256':hashes,'original_custom_emoji_ids':ids,'icons':entries}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    with (OUT/'mapping.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f,lineterminator='\n');writer.writerow(['filename','purpose','button','custom_emoji_id','reuse_existing'])
        for e in entries:writer.writerow([e['filename'],e['purpose'],e['button'],e['custom_emoji_id'] or '',e['reuse_existing'] or ''])
    preview(decoded)
    print('16 new WEBM; 2 byte-identical reused aliases; original14 unchanged',flush=True)


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Decode real VP9 alpha streams; validate constraints, motion and loop seam."""
import json
import statistics
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
from build_emoji_extension import OUT,NAMES,REUSE,old_ids,old_hashes,sha,symbol
import emoji_legacy_renderer as legacy


def main():
    ffmpeg=legacy.ffmpeg_path();manifest=json.loads((OUT/'manifest.json').read_text())
    assert manifest['integration_enabled'] is False
    assert manifest['original_custom_emoji_ids']==old_ids()
    assert manifest['original_sha256']==old_hashes()
    assert len(NAMES)==16 and len(REUSE)==2 and len(manifest['icons'])==18
    results=[]
    for entry in manifest['icons']:
        name=Path(entry['filename']).stem;old=REUSE.get(name)
        path=OUT/('reused-existing' if old else 'new-webm')/entry['filename']
        assert sha(path)==entry['sha256'] and path.stat().st_size<256000
        if old:
            assert sha(path)==manifest['original_sha256'][old]
            assert entry['custom_emoji_id']==old_ids()[old]
        else:
            assert entry['custom_emoji_id'] is None
            svg=ET.parse(OUT/'source'/f'{name}.svg').getroot()
            assert svg.attrib['viewBox']=='0 0 100 100'
            assert legacy.frame(symbol(name),0).tobytes()==legacy.frame(symbol(name),48).tobytes()
        with Image.open(OUT/'png'/f'{name}.png') as image:
            assert image.mode=='RGBA' and image.size==(100,100)
        probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(path)],capture_output=True,text=True).stderr
        assert all(text in probe for text in ('Video: vp9','100x100','24 fps','Duration: 00:00:02.00','alpha_mode      : 1'))
        assert 'Audio:' not in probe
        raw=subprocess.check_output([ffmpeg,'-hide_banner','-loglevel','error','-c:v','libvpx-vp9','-i',str(path),'-f','rawvideo','-pix_fmt','rgba','-'])
        assert len(raw)==48*40000
        frames=[raw[k*40000:(k+1)*40000] for k in range(48)]
        for frame in frames:
            assert frame[3]==0 and frame[39999]==0 and frame[(50*100+50)*4+3]==255
        assert len(set(frames))==48, 'Every frame must contain real motion'
        delta=lambda a,b:sum(abs(x-y) for x,y in zip(a,b))/40000
        inner=[delta(frames[k],frames[k+1]) for k in range(47)]
        seam=delta(frames[-1],frames[0]);ratio=seam/statistics.mean(inner)
        assert 0.3<ratio<3, (name,'loop discontinuity',ratio)
        results.append({'filename':entry['filename'],'bytes':path.stat().st_size,'codec':'vp9','size':[100,100],
            'fps':24,'duration_seconds':2,'frames':48,'audio':False,'alpha_verified':True,
            'moving_frames':48,'loop_seam_ratio':round(ratio,3),'reused':bool(old)})
        print(entry['filename'],path.stat().st_size,'bytes alpha/motion/loop OK',flush=True)
    report={'passed':len(results),'new':16,'reused':2,'results':results,
        'telegram_requirements':'https://core.telegram.org/stickers/webm-vp9-encoding'}
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print('18/18 passed (16 new + 2 unchanged aliases); 14 existing IDs unchanged')


if __name__=='__main__':main()

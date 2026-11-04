"""Measure native SVG text in an isolated headless Chromium renderer, never in EDA."""
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def prepare_svg(source):
    if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
        raise ValueError('DTD/entity SVG is unsupported')
    root = ET.fromstring(source)
    if root.tag.split('}')[-1] != 'svg':
        raise ValueError('not an SVG document')
    for element in root.iter():
        tag = element.tag.split('}')[-1].lower()
        if tag in ('script', 'foreignobject', 'iframe', 'animate', 'animatetransform', 'set'):
            raise ValueError('active/animated SVG is unsupported')
        for key, value in element.attrib.items():
            name = key.split('}')[-1].lower()
            embedded_image = tag == 'image' and value.startswith('data:image/')
            if name.startswith('on') or (name in ('href', 'src') and not value.startswith('#') and not embedded_image):
                raise ValueError('event handlers/external resources are unsupported')
        styles = element.attrib.get('style', '') + (element.text or '' if tag == 'style' else '')
        urls = re.findall(r'url\(([^)]*)\)', styles, re.I)
        if '@import' in styles.lower() or any(not url.strip().strip('\'"').startswith('#') for url in urls):
            raise ValueError('external CSS resources are unsupported')
    # Serialize parsed XML: do not embed an XML declaration or processing instructions in HTML.
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    return ET.tostring(root, encoding='unicode')


def find_browser():
    candidates = [
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
    ]
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'msedge'):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError('Chromium renderer unavailable; provide --browser, do not estimate glyph widths')


MEASURE_JS = r'''
(async () => {
  await document.fonts.ready;
  const root = document.querySelector('svg');
  const inverse = root.getScreenCTM().inverse();
  const texts = [];
  const invisible = el => {
    for (let n = el; n instanceof Element; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity) === 0) return true;
      if (n.tagName.toLowerCase() === 'defs') return true;
    }
    return false;
  };
  for (const [index, el] of [...root.querySelectorAll('text')].entries()) {
    if (!el.textContent.trim() || invisible(el)) continue;
    const b = el.getBBox(), c = el.getScreenCTM();
    if (!c || !b.width || !b.height) throw new Error('Unmeasurable visible text: ' + el.id);
    const matrix = inverse.multiply(c);
    const pts = [[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
      .map(([x,y]) => new DOMPoint(x,y).matrixTransform(matrix));
    const s = getComputedStyle(el);
    texts.push({id: el.id || ('svg-text-' + index), sourceId: el.id || null, text: el.textContent,
      svgBBox: [Math.min(...pts.map(p=>p.x)),Math.min(...pts.map(p=>p.y)),Math.max(...pts.map(p=>p.x)),Math.max(...pts.map(p=>p.y))],
      fontFamily:s.fontFamily,fontSize:s.fontSize,fontWeight:s.fontWeight,fontStyle:s.fontStyle});
  }
  if (!texts.length) throw new Error('No visible text elements; outlined glyph text is unsupported');
  const result = {texts, textCount:texts.length, svgViewBox:root.getAttribute('viewBox'),
    excludedImages:root.querySelectorAll('image').length, useElements:root.querySelectorAll('use').length};
  document.getElementById('measurement-result').textContent = JSON.stringify(result);
})().catch(e => document.getElementById('measurement-result').textContent = JSON.stringify({error:String(e)}));
'''


def measure(svg_path, transform, browser=None):
    if len(transform) != 6 or not all(math.isfinite(v) for v in transform):
        raise ValueError('provide finite affine SVG-to-EDA matrix a b c d e f')
    a, b, c, d, e, f = transform
    if abs(a * d - b * c) < 1e-12:
        raise ValueError('singular SVG-to-EDA transform')
    raw = Path(svg_path).read_bytes()
    source = prepare_svg(raw.decode('utf-8-sig'))
    browser = browser or find_browser()
    # Static local rendering only. Separate profile; no access to user's browser session.
    with tempfile.TemporaryDirectory(prefix='schematic-svg-', ignore_cleanup_errors=True) as temporary:
        work = Path(temporary)
        content = ('<!doctype html><meta charset="utf-8">'
                   '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'">'
                   '<body style="margin:0">' + source + '<pre id="measurement-result"></pre><script>' + MEASURE_JS + '</script>')
        page = work / 'measure.html'
        page.write_text(content, encoding='utf-8')
        process = subprocess.run([browser, '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                  '--disable-background-networking', '--dump-dom', '--virtual-time-budget=2500',
                                  '--user-data-dir=' + str(work / 'profile'), page.as_uri()],
                                 capture_output=True, timeout=45,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        dom = process.stdout.decode('utf-8', errors='replace')
        found = re.search(r'<pre id="measurement-result">(.*?)</pre>', dom, re.S)
        if process.returncode != 0 or not found or not found.group(1):
            raise ValueError('renderer did not produce measurements; preserve renderer diagnostics')
        result = json.loads(html.unescape(found.group(1)))
        if 'error' in result:
            raise ValueError(result['error'])
    ids = [t['id'] for t in result['texts']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate SVG text IDs; require an unambiguous source mapping')
    for text in result['texts']:
        x0, y0, x1, y1 = text['svgBBox']
        points = [(a*x+c*y+e, b*x+d*y+f) for x, y in ((x0,y0),(x0,y1),(x1,y0),(x1,y1))]
        text['bbox'] = [min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)]
    result.update({'schemaVersion':1, 'source':'native-svg-browser-getBBox', 'units':'raw',
                   'svgSha256':hashlib.sha256(raw).hexdigest(), 'svgToEdaTransform':transform,
                   'browser':str(browser), 'liveVerified':False,
                   'unverified':['SVG-to-EDA calibration supplied by caller', 'font equality with EDA renderer',
                                 'text encoded as paths/use or embedded images', 'stroke/filter effects', 'symbol/object ownership mapping']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('svg')
    parser.add_argument('--transform', required=True, nargs=6, type=float, metavar=('A','B','C','D','E','F'))
    parser.add_argument('--browser')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    output = Path(args.out)
    if output.exists():
        parser.error('output already exists; use a new revision filename')
    try:
        result = measure(args.svg, args.transform, args.browser)
    except (OSError, ValueError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        print(json.dumps({'status':'unsupported', 'error':str(exc)}, ensure_ascii=False))
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status':'measured', 'textCount':result['textCount'], 'out':str(output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only page-by-page release audit of a captured public site."""
import argparse
import collections
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

INTERNAL = {'lineupbeat.com', 'www.lineupbeat.com'}
COPY = re.compile(r'projection.boundary|identity.resolved|validated player|evidence categor|evidence agreement|reconciliation|out.of.sample|benchmark copying|reviewed baseline|v1\.[0-9]|exact current active|development preview|offline development|177.player|\bTODO\b|lorem ipsum', re.I)
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids=[];self.links=[];self.images=[];self.assets=[];self.controls=[];self.labels=[]
        self.title=[];self.canonical=[];self.description=[];self.robots=[];self.h1=0;self.main=0
        self.skip=0;self.label_depth=0;self.in_title=False;self.text=[];self.script=None;self.schemas=[];self.lang=None;self.viewport=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if a.get('id'):self.ids.append(a['id'])
        if tag=='html':self.lang=a.get('lang')
        if tag=='title':self.in_title=True
        if tag=='h1':self.h1+=1
        if tag=='main' or a.get('role')=='main':self.main+=1
        if tag in {'script','style','svg'}:self.skip+=1
        if tag=='script' and a.get('type')=='application/ld+json':self.script=[]
        if tag=='a':self.links.append(a)
        if tag=='img':self.images.append(a)
        if tag in {'script','link'} and (a.get('src') or a.get('rel')=='stylesheet'):self.assets.append(a.get('src') or a.get('href'))
        if tag in {'input','select','textarea'} and a.get('type') not in {'hidden','submit','button'}:self.controls.append(a)
        if tag=='label':
            self.label_depth+=1
            if a.get('for'):self.labels.append(a['for'])
        if tag in {'input','select','textarea'} and self.label_depth:a['wrapped_label']=True
        if tag=='link' and a.get('rel')=='canonical':self.canonical.append(a.get('href',''))
        if tag=='meta':
            if a.get('name')=='description':self.description.append(a.get('content',''))
            if a.get('name')=='robots':self.robots.append(a.get('content',''))
            if a.get('name')=='viewport':self.viewport=a.get('content')
    def handle_endtag(self,tag):
        if tag=='label' and self.label_depth:self.label_depth-=1
        if tag=='title':self.in_title=False
        if tag=='script' and self.script is not None:self.schemas.append(''.join(self.script));self.script=None
        if tag in {'script','style','svg'} and self.skip:self.skip-=1
    def handle_data(self,s):
        if self.in_title:self.title.append(s)
        if self.script is not None:self.script.append(s)
        if not self.skip:self.text.append(s)

def audit(root,crawl):
    report=[];parsed={};indexed={urlsplit(x.text).path for x in ET.parse(root/'sitemap.xml').iter() if x.tag.endswith('loc')}
    for capture in crawl:
        path=capture['path']
        if not path.endswith(('/', '.html')):continue
        file=root.parent/capture['file'];findings=[]
        def flag(level,rule,detail):findings.append({'severity':level,'rule':rule,'detail':detail})
        if capture['status']!='200':flag('high','http_status',f"HTTP {capture['status']} {capture.get('error','')}")
        if not file.is_file():flag('high','missing_capture','No page bytes captured');text=''
        else:text=file.read_text(errors='replace')
        p=Page();p.feed(text);parsed[path]=p
        visible=' '.join(' '.join(p.text).split())
        title=''.join(p.title)
        if p.h1!=1:flag('medium','h1',f'{p.h1} H1 headings')
        if p.main!=1:flag('medium','main_landmark',f'{p.main} main landmarks')
        if not p.lang:flag('medium','document_language','Missing document language')
        if not p.viewport:flag('high','viewport','Missing mobile viewport')
        if not title:flag('high','title','Missing title')
        if len(p.description)!=1 or not p.description[0]:flag('medium','description','Missing or duplicate description')
        if path in indexed and len(p.canonical)!=1:flag('medium','canonical','Missing or duplicate canonical')
        if path in indexed and any('noindex' in r.lower() for r in p.robots):flag('high','indexing','Sitemap page has noindex')
        duplicates=[x for x,n in collections.Counter(p.ids).items() if n>1]
        if duplicates:flag('medium','duplicate_ids',', '.join(duplicates[:15]))
        missing_alt=[a.get('src','') for a in p.images if 'alt' not in a]
        if missing_alt:flag('medium','image_alt',f'{len(missing_alt)} images missing alt attributes')
        unnamed=[a.get('id') or a.get('name') or a.get('type','control') for a in p.controls if not (a.get('wrapped_label') or a.get('aria-label') or a.get('aria-labelledby') or a.get('id') in p.labels or a.get('title'))]
        if unnamed:flag('medium','control_label',', '.join(unnamed[:15]))
        for schema in p.schemas:
            try:json.loads(schema)
            except Exception:flag('high','structured_data','Invalid JSON-LD')
        matches=list(dict.fromkeys(m.group() for m in COPY.finditer(visible)))
        if matches:flag('review','reader_copy',', '.join(matches))
        if re.search(r'lb-dev-banner|lineupbeat-dev\.pages\.dev',visible):flag('high','dev_leak','Development information in reader text')
        if len(text.encode())>500000:flag('review','html_weight',f'{len(text.encode()):,} uncompressed HTML bytes')
        report.append({**capture,'title':title,'canonical':p.canonical,'words':len(visible.split()),'h1':p.h1,'images':len(p.images),'links':len(p.links),'findings':findings})
    known={r['path'] for r in crawl if r['status']=='200'}
    unresolved=[];assets=set()
    for row in report:
        p=parsed[row['path']]
        for a in p.links:
            href=a.get('href','')
            if not href or '${' in href:continue
            u=urlsplit(urljoin(row['url'],href))
            if u.scheme not in {'http','https'} or u.hostname not in INTERNAL:continue
            target=u.path or '/'
            if target.startswith('/cdn-cgi/'):continue  # Cloudflare-managed email decoder
            if target in parsed and u.fragment and unquote(u.fragment) not in parsed[target].ids:
                row['findings'].append({'severity':'medium','rule':'broken_fragment','detail':href})
            if target not in known:unresolved.append({'from':row['path'],'target':target,'href':href})
        for a in p.images:
            if a.get('src') and not a['src'].startswith('data:'):assets.add(urljoin(row['url'],a['src']))
        for src in p.assets:
            if src:assets.add(urljoin(row['url'],src))
    return {'pages':report,'unresolved_links':unresolved,'assets':sorted(assets),'summary':dict(collections.Counter(f['rule'] for r in report for f in r['findings']))}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('capture',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    crawl=json.loads((args.capture/'crawl.json').read_text())
    result=audit(args.capture/'site',crawl);args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({'pages':len(result['pages']),'findings':result['summary'],'unresolved_targets':len({r['target'] for r in result['unresolved_links']}),'unique_assets':len(result['assets'])},indent=2))

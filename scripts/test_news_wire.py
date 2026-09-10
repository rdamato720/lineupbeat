#!/usr/bin/env python3
"""News links preserve source dates/identity and cannot expose commentary."""
import copy, hashlib, json, sys, tempfile, time, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from feedparser import FeedParserDict
sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_news_wire as n

class NewsWireTests(unittest.TestCase):
    def setUp(self):
        self.source=next(s for s in n.sources() if s.source_id=='pewter_report')
        self.now=datetime(2026,9,9,17,tzinfo=timezone.utc)
        self.players=n.identities()
        self.entry=FeedParserDict(link='https://www.pewterreport.com/baker-mayfield-update/',title='Baker Mayfield returns to practice',published_parsed=time.strptime('2026-09-09 16:00','%Y-%m-%d %H:%M'))
    def normalize(self,entry=None):return n.normalize_entry(self.source,entry or self.entry,self.players,self.now)
    def test_exact_identity_and_publisher_timestamp(self):
        row=self.normalize();self.assertEqual(row['players'][0]['name'],'Baker Mayfield');self.assertEqual(row['players'][0]['team'],'TB');self.assertEqual(row['published_at'],'2026-09-09T16:00:00+00:00')
    def test_unknown_wrong_team_bare_surname_and_missing_date(self):
        for title in ('Mayfield returns to practice','Patrick Mahomes returns to practice','Imaginary Player returns'):
            e=copy.deepcopy(self.entry);e["title"]=title;self.assertIsNone(self.normalize(e))
        e=copy.deepcopy(self.entry);del e['published_parsed'];self.assertIsNone(self.normalize(e))
    def test_old_future_and_foreign_links_rejected(self):
        for stamp in ('2026-08-01 16:00','2026-09-10 16:00'):
            e=copy.deepcopy(self.entry);e["published_parsed"]=time.strptime(stamp,'%Y-%m-%d %H:%M');self.assertIsNone(self.normalize(e))
        for url in ('javascript:alert(1)','https://example.com/baker-mayfield','http://www.pewterreport.com/test'):
            e=copy.deepcopy(self.entry);e["link"]=url;self.assertIsNone(self.normalize(e))
    def test_rendering_has_no_editorial_fields(self):
        row=self.normalize();row['commentary']='UNREVIEWED_COMMENTARY_SENTINEL';row['reporter_found']='PRIVATE_EVIDENCE_SENTINEL'
        data={'updated_at':self.now.isoformat(),'items':[row]}
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'news.json';snapshot.write_text(json.dumps(data));out=Path(d)/'index.html'
            with patch.object(n,'SNAPSHOT',snapshot):n.build(out=out)
            text=out.read_text();self.assertNotIn('SENTINEL',text);self.assertIn(row['headline'],text);self.assertIn('for="news-team"',text);self.assertIn('class="nw-photo"',text)
    def test_snapshot_identity_and_duplicate_validation(self):
        row=self.normalize();data={'updated_at':self.now.isoformat(),'items':[row,row]}
        with self.assertRaises(ValueError):n.validated(data)
        data['items']=[copy.deepcopy(row)];data['items'][0]['players'][0]['team']='KC'
        with self.assertRaises(ValueError):n.validated(data)
    def test_roundup_uses_description_without_rendering_it(self):
        e=copy.deepcopy(self.entry);e['title']='Wednesday practice report'
        e['summary']='Baker Mayfield returned. DESCRIPTION_SENTINEL'
        row=self.normalize(e);self.assertEqual(row['players'][0]['name'],'Baker Mayfield')
        data={'updated_at':self.now.isoformat(),'items':[row]};n.validated(data)
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'data.json';snapshot.write_text(json.dumps(data))
            with patch.object(n,'SNAPSHOT',snapshot):p=n.build(out=Path(d)/'index.html')
            self.assertNotIn('DESCRIPTION_SENTINEL',p.read_text())
    def test_team_report_does_not_invent_player_tags(self):
        e=copy.deepcopy(self.entry);e['title']='Wednesday injury report';e['summary']='Three players limited.'
        row=self.normalize(e);self.assertEqual(row['players'],[]);self.assertEqual(row['teams'],['TB'])
        data={'updated_at':self.now.isoformat(),'items':[row]};n.validated(data)
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'data.json';snapshot.write_text(json.dumps(data))
            with patch.object(n,'SNAPSHOT',snapshot):p=n.build(out=Path(d)/'index.html')
            self.assertIn('TB · Team report',p.read_text());self.assertIn('value="TB"',p.read_text())
        row['teams']=['KC']
        with self.assertRaises(ValueError):n.validated(data)
    def test_news_network_paths_are_exact_team_scopes(self):
        source=next(s for s in n.sources() if s.source_id=='news_si_nfl')
        e=copy.deepcopy(self.entry);e['link']='https://www.si.com/nfl/buccaneers/onsi/baker-mayfield-practice?utm_source=RSS'
        row=n.normalize_entry(source,e,self.players,self.now)
        self.assertEqual(row['teams'],['TB']);self.assertNotIn('utm_',row['url'])
        for path in ('/nfl/chiefs/onsi/test','/college/test','/nfl/buccaneers-other/onsi/test'):
            e['link']='https://www.si.com'+path
            self.assertIsNone(n.normalize_entry(source,e,self.players,self.now))
    def test_full_article_content_and_unrelated_news_do_not_match(self):
        e=copy.deepcopy(self.entry);e['title']='Team announces community event'
        e['content']=[{'value':'Baker Mayfield returned to practice.'}]
        self.assertIsNone(self.normalize(e))
        for title in ('Baker Mayfield fantasy football rankings','Baker Mayfield mural unveiled','Baker Mayfield Super Bowl prediction'):
            e['title']=title;self.assertIsNone(self.normalize(e))
        e['title']='Wednesday practice report';e['summary']='Patrick Mahomes returned.'
        self.assertEqual(self.normalize(e)['players'],[])
    def test_possessive_player_name_matches_without_fuzzy_names(self):
        e=copy.deepcopy(self.entry);e['title']="Baker Mayfield’s practice status updated"
        self.assertEqual(self.normalize(e)['players'][0]['name'],'Baker Mayfield')
    def test_sbn_mixed_categories_fail_closed(self):
        source=next(s for s in n.sources() if s.source_id=='news_sbn_tb')
        entry=copy.deepcopy(self.entry);entry['link']='https://www.bucsnation.com/baker-practice'
        for categories in ([], ['General'], ['Buccaneers News','Buccaneers Analysis'],
                           ['Buccaneers News','Open Threads'], ['Buccaneers News','Buccaneers Opinion'],
                           ['Buccaneers News','Buccaneers NFL picks and predictions']):
            entry['tags']=[{'term':c} for c in categories]
            self.assertIsNone(n.normalize_entry(source,entry,self.players,self.now))
        entry['tags']=[{'term':'Tampa Bay Buccaneers Injuries'}]
        for title in ('Should the Buccaneers extend Baker Mayfield?', 'Buccaneers preview: Baker Mayfield returns'):
            opinion=copy.deepcopy(entry);opinion['title']=title
            self.assertIsNone(n.normalize_entry(source,opinion,self.players,self.now))
        row=n.normalize_entry(source,entry,self.players,self.now)
        self.assertEqual(row['players'][0]['name'],'Baker Mayfield')
        data={'updated_at':self.now.isoformat(),'items':[row]};n.validated(data)
        row['categories'].append('Tampa Bay Buccaneers Analysis')
        with self.assertRaises(ValueError):n.validated(data)
    def test_sbn_has_32_unique_team_feeds(self):
        sources=[s for s in n.sources() if s.source_id.startswith('news_sbn_')]
        self.assertEqual(len(sources),32)
        self.assertEqual(len({s.teams[0] for s in sources}),32)
        self.assertTrue(all(s.news_categories_only and len(s.teams)==1 for s in sources))

    def test_robots_refusal_does_not_fetch_feed(self):
        response=type('Result',(),{'returncode':0,'stdout':b'User-agent: *\nDisallow: /'})()
        with tempfile.TemporaryDirectory() as d, patch.object(n,'sources',return_value=[self.source]), patch.object(n.subprocess,'run',return_value=response) as get:
            with self.assertRaises(RuntimeError):n.refresh(Path(d)/'data.json')
            self.assertEqual(get.call_count,1);self.assertTrue(get.call_args.args[0][-1].endswith('/robots.txt'))
    def test_failed_refresh_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'news.json';snapshot.write_text('{"items":[]}')
            before=snapshot.read_bytes()
            with patch.object(n.subprocess,'run',return_value=type('Result',(),{'returncode':1})()):
                with self.assertRaises(RuntimeError):n.refresh(snapshot)
            self.assertEqual(before,snapshot.read_bytes())
    def test_publications_unchanged_by_news_build(self):
        pubs=n.ROOT/'data/wire_publications.json';before=hashlib.sha256(pubs.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as d:n.build(out=Path(d)/'index.html')
        self.assertEqual(before,hashlib.sha256(pubs.read_bytes()).hexdigest())
    @patch.dict(n.os.environ, {}, clear=True)
    @patch("builtins.print")
    def test_health_distinguishes_quiet_feed_from_stale_or_failed_capture(self, _print):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'news.json'
            data={'updated_at':self.now.isoformat(),'items':[],
                  'sources':[{'source_id':'pewter_report','ok':True}]}
            path.write_text(json.dumps(data))
            self.assertTrue(n.check_health(path,self.now))
            data['updated_at']='2026-09-08T17:00:00+00:00'
            path.write_text(json.dumps(data))
            self.assertFalse(n.check_health(path,self.now))
            data['updated_at']=self.now.isoformat();data['sources'][0]['ok']=False
            path.write_text(json.dumps(data))
            self.assertFalse(n.check_health(path,self.now))
            path.write_text('invalid')
            self.assertFalse(n.check_health(path,self.now))
    def test_recurring_workflows_cannot_run_legacy_paid_news(self):
        import yaml
        workflows=n.ROOT/'.github/workflows'
        monitor=yaml.load((workflows/'wire-monitor.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertNotIn('schedule',monitor['on'])
        self.assertEqual(monitor['jobs']['monitor']['if'],'${{ false }}')
        refresh=yaml.load((workflows/'refresh.yml').read_text(),Loader=yaml.BaseLoader)
        steps=refresh['jobs']['refresh']['steps']
        commands='\n'.join(s.get('run','') for s in steps)
        self.assertNotIn('beatwire.cli run',commands)
        self.assertNotIn('wire_mobile_draft.py',commands)
        self.assertIn('build_news_wire.py --refresh-only',commands)
        self.assertIn('build_news_wire.py --check-health',commands)
        pipeline=next(s for s in steps if s.get('name')=='Run pipeline')
        self.assertNotIn('OPENAI_API_KEY',pipeline['env'])
        self.assertNotIn('TWITTERAPI_IO_KEY',pipeline['env'])
if __name__=='__main__':unittest.main()

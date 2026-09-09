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
if __name__=='__main__':unittest.main()

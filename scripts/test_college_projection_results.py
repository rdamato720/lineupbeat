import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import college_projection_results as r

class ResultsTests(unittest.TestCase):
    def test_scoring_uses_same_components_as_forecast(self):
        self.assertAlmostEqual(r.points({'passYds':250,'passTd':2,'int':1,'rushYds':30,'rushTd':1,'rec':2,'recYds':20,'recTd':1}),36)
    def test_absolute_errors_do_not_cancel(self):
        self.assertEqual(r.metrics([{'projected':10,'actual':5},{'projected':10,'actual':15}]),{'count':2,'mae':5})
    def test_frozen_forecast_and_sample_reconcile(self):
        d=r.evaluate();focus=[p for p in d['rows'] if p['rank']<=30]
        self.assertEqual(len(focus),120)
        self.assertEqual(d['summary']['count'],sum(p['actual'] is not None for p in focus))
        self.assertEqual(sum(x['count'] for x in d['positions'].values()),d['summary']['count'])
        self.assertEqual(d['teams'],64)
        self.assertTrue(any(p['actual'] is None for p in focus))
        self.assertAlmostEqual(d['summary']['mae'],7.624363636363636)
    def test_absent_stat_line_is_ungraded_not_zero(self):
        d=r.evaluate();missing=next(p for p in d['rows'] if p['name']=='Scotty Fox Jr.')
        self.assertIsNone(missing['actual'])
    def test_identity_cannot_fuzzy_match(self):
        self.assertNotEqual(r.identity('John Smith Jr.'),r.identity('John Smith'))
        self.assertEqual(r.identity('D’Andre'),r.identity("D'Andre"))
    def test_late_forecast_and_duplicate_identity_refused(self):
        original=json.loads(r.ACTUALS.read_text())
        for mutation in ('late','duplicate','hash'):
            data=copy.deepcopy(original)
            if mutation=='late':data['forecastCommittedAt']='2026-09-10T00:00:00Z'
            elif mutation=='hash':data['forecastSha256']='wrong'
            else:data['players'].append(data['players'][0])
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'actuals.json';p.write_text(json.dumps(data))
                with patch.object(r,'ACTUALS',p),self.assertRaises(ValueError):r.evaluate()
    def test_results_link_and_week_scope(self):
        for pos in ('','qb/','rb/','wr/','te/'):
            text=(r.ROOT/'site/college-fantasy-football/week-2'/pos/'index.html').read_text()
            self.assertIn('110 of 120 players graded',text)
            self.assertIn(r.RESULT_PATH,text)
        old=(r.ROOT/'site/college-fantasy-football/week-1/index.html').read_text()
        self.assertNotIn('class="pr-strip"',old)

if __name__=='__main__':unittest.main()

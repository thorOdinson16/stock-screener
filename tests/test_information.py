"""Publication/observation safety, earnings versions and peer-context causality."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.evaluation.information import earnings_at, attach_earnings, sector_features, announcement_features, timestamp, document_facts
from ml.evaluation.information_research import prospective_summary


class EarningsTests(unittest.TestCase):
    def row(self,period,published,revenue=100,profit=10):
        return {'symbol':'A','period_end':period,'published_at':published+'T18:00:00+05:30',
                'first_seen_at':'2026-11-09T10:00:00+05:30','available_date':str(pd.Timestamp(published).date()+pd.Timedelta(days=1)),
                'revenue':revenue,'profit':profit,'margin':profit/revenue,'scope':'Consolidated','currency':'INR',
                'document_sha256':period+published}

    def test_no_growth_without_prior_filing_and_no_same_day_availability(self):
        r=self.row('2024-12-31','2025-02-01')
        self.assertTrue(np.isnan(earnings_at([r],'2025-02-01')['info_margin']))
        v=earnings_at([r],'2025-02-02')
        self.assertAlmostEqual(v['info_margin'],.1)
        self.assertTrue(np.isnan(v['info_revenue_qoq']))

    def test_growth_uses_only_comparisons_already_published(self):
        rows=[self.row('2024-09-30','2024-11-01',100,10),self.row('2024-12-31','2025-02-01',120,18)]
        r=earnings_at(rows,'2025-02-02')
        self.assertAlmostEqual(r['info_revenue_qoq'],.2)
        self.assertAlmostEqual(r['info_profit_change_on_revenue'],.08)
        before=earnings_at(rows,'2025-01-01')
        self.assertAlmostEqual(before['info_margin'],.1)

    def test_amendment_of_old_quarter_does_not_replace_newer_quarter(self):
        rows=[self.row('2024-09-30','2025-02-10',500,200),self.row('2024-12-31','2025-02-01',120,18)]
        self.assertAlmostEqual(earnings_at(rows,'2025-02-11')['info_margin'],.15)

    def test_future_observation_and_stale_filing_are_unavailable_live(self):
        r=self.row('2024-12-31','2025-02-01')
        self.assertTrue(np.isnan(earnings_at([r],'2025-02-02','2025-02-02T12:00:00+05:30')['info_margin']))
        self.assertTrue(np.isnan(earnings_at([r],'2026-11-09')['info_margin']))

    def test_timestamp_never_infers_timezone_for_iso_input(self):
        with self.assertRaises(ValueError):timestamp('2025-02-01T10:00:00')


class PeerTests(unittest.TestCase):
    def data(self):
        sessions=pd.date_range('2020-01-01',periods=260).strftime('%Y-%m-%d').tolist()
        rows=[{'symbol':s,'trade_date':d,'industry':'sector'} for s in 'ABCDEF' for d in sessions]
        bars={(s,d):{'close':100+i*(j+1)/10,'volume':1000+i} for j,s in enumerate('ABCDEF') for i,d in enumerate(sessions)}
        return pd.DataFrame(rows),bars,sessions

    def test_peers_exclude_self_and_future_changes_do_not_rewrite_past(self):
        f,b,sessions=self.data();a=sector_features(f,b,sessions)
        changed={k:dict(v) for k,v in b.items()}
        for key in changed:
            if key[0]=='A':changed[key]['close']*=3
            if key[1]>sessions[240]:changed[key]['close']*=2
        other=sector_features(f,changed,sessions)
        columns=['peer_breadth_200d','peer_return_21d','peer_return_dispersion_63d']
        np.testing.assert_allclose(a.loc[(a.symbol=='A')&(a.trade_date<=sessions[240]),columns],other.loc[(other.symbol=='A')&(other.trade_date<=sessions[240]),columns],equal_nan=True)

    def test_fewer_than_five_other_peers_is_unknown(self):
        f,b,sessions=self.data();r=sector_features(f[f.symbol!='F'],b,sessions)
        self.assertTrue(r.peer_return_21d.isna().all())


class AnnouncementTests(unittest.TestCase):
    def test_archive_gaps_are_not_zero_and_future_observations_are_excluded(self):
        row={'symbol':'A','published_at':'2026-11-01T10:00:00+05:30','first_seen_at':'2026-11-09T10:00:00+05:30',
             'subject':'Financial Results','exchange_id':'1'}
        unknown=announcement_features([row],'A','2026-11-08T10:00:00+05:30',None)
        self.assertIsNone(unknown['announcement_count_7d'])
        known=announcement_features([row],'A','2026-11-08T10:00:00+05:30','2026-10-01T10:00:00+05:30')
        self.assertEqual(known['announcement_count_7d'],0)


class ProspectiveTests(unittest.TestCase):
    def test_empty_trial_has_no_supported_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            r=prospective_summary(Path(d),[])
        self.assertEqual(r['matured_cohorts'],0)
        self.assertFalse(r['production_ready'])
        self.assertEqual(len(r['comparisons']),6)
        self.assertFalse(any(c['supported'] for c in r['comparisons'].values()))


class DocumentTests(unittest.TestCase):
    def test_original_quarter_identity_units_scope_and_future_publication(self):
        xml = '''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:f="urn:financial">
        <x:context id="q"><x:period><x:startDate>2024-10-01</x:startDate><x:endDate>2024-12-31</x:endDate></x:period></x:context>
        <x:unit id="INR"><x:measure>iso4217:INR</x:measure></x:unit>
        <f:Symbol contextRef="q">A</f:Symbol><f:NatureOfReportStandaloneConsolidated contextRef="q">Consolidated</f:NatureOfReportStandaloneConsolidated>
        <f:DateOfEndOfReportingPeriod contextRef="q">2024-12-31</f:DateOfEndOfReportingPeriod>
        <f:RevenueFromOperations contextRef="q" unitRef="INR">100</f:RevenueFromOperations><f:ProfitLossForPeriod contextRef="q" unitRef="INR">10</f:ProfitLossForPeriod></x:xbrl>'''
        record={'symbol':'A.NS','source':'https://nsearchives.nseindia.com/corporate/test.xml',
                'index_source':'https://www.nseindia.com','published_times':['01-Feb-2025 18:00:00']}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.xml';path.write_text(xml)
            r=document_facts(record,path,'2025-02-02T10:00:00+05:30')
            self.assertEqual(r['available_date'],'2025-02-02')
            self.assertAlmostEqual(r['margin'],.1)
            with self.assertRaisesRegex(ValueError,'Future'):
                document_facts(record,path,'2025-02-01T10:00:00+05:30')
            path.write_text(xml.replace('Consolidated','Standalone'))
            with self.assertRaises(ValueError):document_facts(record,path,'2025-02-02T10:00:00+05:30')
            path.write_text(xml.replace('iso4217:INR','iso4217:USD'))
            with self.assertRaises(ValueError):document_facts(record,path,'2025-02-02T10:00:00+05:30')

    def test_observation_ledger_cannot_be_overwritten(self):
        from poller.research_information import exclusive
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'ledger.json'
            exclusive(file,{'first_seen_at':'first'})
            exclusive(file,{'first_seen_at':'later'})
            self.assertEqual(json.loads(file.read_text())['first_seen_at'],'first')

    def test_incomplete_first_cohort_is_not_replaced_by_overlapping_winner(self):
        sessions=pd.date_range('2020-01-02',periods=80).strftime('%Y-%m-%d').tolist()
        with tempfile.TemporaryDirectory() as d:
            paper=Path(d);(paper/'predictions').mkdir();(paper/'outcomes').mkdir()
            for i,day in enumerate(('2020-01-01','2020-01-02','2020-01-24')):
                (paper/'predictions'/f'{i}.json').write_text(json.dumps({'publication_date':day}))
                books={name:{'status':'complete','net_return':.02} for name in ('technical_control','momentum','current_model','sector_candidate','earnings_candidate')}
                if i==0:books['sector_candidate']={'status':'incomplete','net_return':None}
                (paper/'outcomes'/f'{i}.json').write_text(json.dumps(books))
            r=prospective_summary(paper,sessions)
            self.assertEqual(r['scheduled_independent_cohorts'],2)
            self.assertEqual(r['comparisons']['sector_candidate/momentum']['complete_independent_pairs'],1)
            self.assertFalse(r['comparisons']['sector_candidate/momentum']['supported'])


if __name__=='__main__':unittest.main()

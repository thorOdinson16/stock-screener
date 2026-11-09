"""Outcome-free stock selection, source schemas and hard data readiness gates."""
from pathlib import Path
import sys
import json
import tempfile
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.evaluation.pilot_coverage import choose_pilot,readiness,parse_pilot_record,require_ready
from poller.pilot_filings import normalize

THRESHOLDS={'minimum_signal_dates':2,'required_date_pass_fraction':.95,'minimum_quarter_pass_fraction':.9,
            'latest_date_must_pass':True,'required_quarter_cell_fraction':.95}


class PilotTests(unittest.TestCase):
    def test_selection_uses_only_prior_liquidity_and_industry_caps(self):
        days=pd.date_range('2023-01-01',periods=64).strftime('%Y-%m-%d').tolist()
        bars=pd.DataFrame([{'symbol':s,'trade_date':d,'close':100,'volume':v} for s,v in [('^NSEI',1),('A',30),('B',20),('C',10),('BANK',100)] for d in days])
        specs=[{'yf_symbol':s,'industry':industry,'isin':s} for s,industry in [('A','IT'),('B','IT'),('C','Consumer'),('BANK','Financial Services')]]
        result=choose_pilot(bars,specs,days[62],2,1)
        self.assertEqual([r['symbol'] for r in result],['A','C'])
        bars.loc[bars.trade_date==days[-1],'close']=1e9
        self.assertEqual(result,choose_pilot(bars,specs,days[62],2,1))

    def test_gate_requires_every_rule_and_no_missing_quarter_cells(self):
        daily=pd.DataFrame({'trade_date':['2024-01-01','2024-01-02'],'ready':[True,True]})
        self.assertTrue(readiness(daily,[{'accepted':True}],THRESHOLDS)['ready'])
        self.assertFalse(readiness(daily,[{'accepted':False}],THRESHOLDS)['ready'])
        daily.loc[1,'ready']=False
        self.assertFalse(readiness(daily,[{'accepted':True}],THRESHOLDS)['ready'])

    def test_bad_quarter_cannot_hide_behind_high_global_average(self):
        days=pd.date_range('2024-01-01',periods=200)
        daily=pd.DataFrame({'trade_date':days.strftime('%Y-%m-%d'),'ready':True})
        daily.loc[:5,'ready']=False
        # Overall >=95%, but these six dates are all of the first tiny quarter
        # in this custom slice. Use a first quarter with just one observed date.
        daily=pd.DataFrame({'trade_date':['2023-12-29']+days.strftime('%Y-%m-%d').tolist(),'ready':[False]+[True]*200})
        r=readiness(daily,[{'accepted':True}],THRESHOLDS)
        self.assertGreater(r['date_pass_fraction'],.95)
        self.assertFalse(r['ready'])

    def test_integrated_metadata_requires_dissemination_and_original_link(self):
        row={'type':'Integrated Filing- Financials','consolidated':'Consolidated','broadcast_Date':'01-Feb-2025 18:00:00',
             'creation_Date':'01-Feb-2025 18:00:02','qe_Date':'31-DEC-2024','symbol':'A',
             'xbrl':'https://nsearchives.nseindia.com/corporate/original.xml'}
        r=normalize(row,'https://www.nseindia.com/api/integrated-filing-results','A.NS',True)
        self.assertEqual(r['period_end'],'31-Dec-2024')
        self.assertEqual(len(r['published_times']),2)
        row.pop('creation_Date')
        with self.assertRaises(ValueError):normalize(row,'source','A.NS',True)

    def test_standalone_is_not_substituted_for_consolidated(self):
        self.assertIsNone(normalize({'consolidated':'Standalone'},'source','A.NS'))


    def test_alias_needs_exchange_bound_exact_isin(self):
        xml='''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:f="urn:test">
        <x:context id="q"><x:period><x:startDate>2024-10-01</x:startDate><x:endDate>2024-12-31</x:endDate></x:period></x:context>
        <x:unit id="INR"><x:measure>iso4217:INR</x:measure></x:unit>
        <f:Symbol contextRef="q">OLD</f:Symbol><f:NatureOfReportStandaloneConsolidated contextRef="q">Consolidated</f:NatureOfReportStandaloneConsolidated>
        <f:DateOfEndOfReportingPeriod contextRef="q">2024-12-31</f:DateOfEndOfReportingPeriod>
        <f:RevenueFromOperations contextRef="q" unitRef="INR">100</f:RevenueFromOperations><f:ProfitLossForPeriod contextRef="q" unitRef="INR">10</f:ProfitLossForPeriod></x:xbrl>'''
        row={'symbol':'NEW.NS','exchange_symbol':'NEW','index_isin':'ISIN1','first_seen_at':'2025-02-02T12:00:00+05:30',
             'published_times':['01-Feb-2025 18:00:00'],'source':'https://nsearchives.nseindia.com/corporate/a.xml','index_source':'https://www.nseindia.com/api/corporates-financial-results'}
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'source.xml';file.write_text(xml)
            result=parse_pilot_record(row,file,{'NEW.NS':{'isin':'ISIN1'}})
            self.assertEqual(result['symbol'],'NEW.NS')
            self.assertEqual(result['as_filed_symbol'],'OLD.NS')
            with self.assertRaises(ValueError):parse_pilot_record(row,file,{'NEW.NS':{'isin':'OTHER'}})
            row.pop('index_isin')
            with self.assertRaises(ValueError):parse_pilot_record(row,file,{'NEW.NS':{'isin':'ISIN1'}})

    def test_annual_index_does_not_automatically_make_a_quarter(self):
        row={'consolidated':'Consolidated','period':'Annual','cumulative':'Cumulative','broadCastDate':'01-May-2025 18:00:00',
             'exchdisstime':'01-May-2025 18:00:02','toDate':'31-Mar-2025','xbrl':'https://nsearchives.nseindia.com/corporate/a.xml'}
        result=normalize(row,'source','A.NS')
        self.assertEqual(result['period_end'],'31-Mar-2025')
        # It remains just index metadata. Acceptance still requires the strict
        # original document quarter parser exercised in the other tests.


    def test_training_gate_refuses_an_unready_audit(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'coverage_report.json').write_text(json.dumps({'readiness':{'ready':False,'reasons':['Missing quarter']}}))
            with self.assertRaisesRegex(RuntimeError,'Financial data not ready'):
                require_ready(Path(d))


if __name__=='__main__':unittest.main()

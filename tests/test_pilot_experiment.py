"""Fixed comparison schedule, pairing and rejection behavior."""
import unittest
from unittest.mock import patch
from ml.evaluation.pilot_experiment import cycle_schedule,paired,evaluate,selection_reasons,SCENARIOS
import pandas as pd


class PilotExperimentTests(unittest.TestCase):
    def test_schedule_has_no_overlapping_holdings_and_respects_delay_and_end(self):
        sessions=pd.bdate_range('2024-01-01',periods=90).strftime('%Y-%m-%d').tolist()
        result=cycle_schedule(sessions,sessions,sessions[0],sessions[-1],1)
        self.assertEqual(result[0],(sessions[0],2))
        self.assertTrue(all(b[1]>a[1]+21 for a,b in zip(result,result[1:])))
        self.assertTrue(all(i+21<len(sessions) for _,i in result))

    def test_unknown_pair_remains_in_coverage_denominator(self):
        a=[{'signal_date':'A','status':'complete','net_return':.1},{'signal_date':'B','status':'incomplete','net_return':None}]
        b=[{'signal_date':'A','status':'complete','net_return':0},{'signal_date':'B','status':'complete','net_return':.1}]
        result=paired(a,b)
        self.assertEqual(result['coverage'],.5)
        self.assertEqual(result['scheduled_pairs'],2)
        b.reverse()
        with self.assertRaisesRegex(ValueError,'Unpaired'):paired(a,b)

    def test_top_five_selects_different_stocks_without_filtering_future_failures(self):
        sessions=pd.bdate_range('2024-01-01',periods=25).strftime('%Y-%m-%d').tolist()
        rows=pd.DataFrame([{'symbol':f'S{i}','trade_date':sessions[0],'score':i} for i in range(8)])
        other=rows.copy();other['score']=-other.score
        stats={(r.symbol,sessions[0]):{'momentum':.1} for r in rows.itertuples()}
        def pool(predictions,statistics,day,policy,events):return predictions
        def outcome(symbol,*args):return {'status':'missing_price','net_return':None} if symbol=='S7' else {'status':'complete','net_return':.01}
        book={'status':'complete','blocked':None,'net_return':0,'max_drawdown':0,'trade_win_rate':0,'trades':[],'open_positions':{},'curve':[]}
        with patch('ml.evaluation.pilot_experiment.candidates',side_effect=pool),patch('ml.evaluation.pilot_experiment.outcome',side_effect=outcome),patch('ml.evaluation.pilot_experiment.simulate',return_value=book):
            result=evaluate({'a':rows,'b':other},stats,{},sessions,[],sessions[0],sessions[-1])
        a=result['strategies']['a']['scenarios']['base']['top5_cycles'][0]
        b=result['strategies']['b']['scenarios']['base']['top5_cycles'][0]
        self.assertEqual(a['selected_symbols'],['S7','S6','S5','S4','S3'])
        self.assertEqual(b['selected_symbols'],['S0','S1','S2','S3','S4'])
        self.assertEqual(a['status'],'incomplete')
        self.assertIsNone(a['net_return'])

    def test_positive_base_result_cannot_hide_double_cost_failure(self):
        controls=('sector','momentum','current_model')
        retail={'status':'complete','net_return':.05,'closed_trades':30,'max_drawdown':-.1}
        financial={'scenarios':{s:{'retail':dict(retail)} for s in SCENARIOS},
            'comparisons':{c:{s:{'coverage':1,'complete_pairs':10,'mean_difference':.01} for s in SCENARIOS} for c in controls}}
        result={'strategies':{'financial':financial,**{c:{'scenarios':{s:{'retail':{**retail,'net_return':.01}} for s in SCENARIOS}} for c in controls}}}
        self.assertEqual(selection_reasons(result,[result]*3),[])
        financial['scenarios']['double_costs']['retail']['net_return']=-.01
        self.assertTrue(any('double_costs' in r for r in selection_reasons(result,[result]*3)))


if __name__=='__main__':unittest.main()

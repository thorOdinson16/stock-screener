"""Publication-independent label timing, abstention and honest comparisons."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from ml.evaluation.decision_targets import sector_forward_returns,matured_training,execution_targets
from ml.evaluation.decision_evaluation import select_rows,basket,schedule,paired,cycle_summary,evaluate,qualify
from ml.evaluation.retail import Policy


class SectorTargetTests(unittest.TestCase):
    def fixture(self):
        sessions=pd.bdate_range('2024-01-01',periods=8).strftime('%Y-%m-%d').tolist()
        industries={f'S{i}':'Sector' for i in range(7)}
        bars=pd.DataFrame([{'symbol':s,'trade_date':d,'close':100*(1+(.1 if s=='S0' else .01))**i} for s in industries for i,d in enumerate(sessions)])
        return sessions,industries,bars

    def test_exact_next_close_interval_and_leave_one_out(self):
        days,industries,bars=self.fixture()
        x=sector_forward_returns(bars,industries,days,2,[],['S0'],min_history=1)
        self.assertAlmostEqual(x.iloc[0].sector_forward,1.01**2-1)
        altered=bars.copy();altered.loc[(altered.symbol=='S0')&(altered.trade_date==days[3]),'close']=99999
        y=sector_forward_returns(altered,industries,days,2,[],['S0'],min_history=1)
        self.assertAlmostEqual(x.iloc[0].sector_forward,y.iloc[0].sector_forward)
        self.assertTrue(pd.isna(x.iloc[-1].sector_forward))

    def test_missing_peer_is_unknown_and_denominator_not_shrunk(self):
        days,industries,bars=self.fixture()
        bars.loc[(bars.symbol=='S6')&(bars.trade_date==days[2]),'close']=np.nan
        x=sector_forward_returns(bars,industries,days,2,[],['S0'],min_history=1)
        self.assertEqual(x.iloc[0].sector_expected_peers,6)
        self.assertEqual(x.iloc[0].sector_known_peers,5)
        self.assertTrue(pd.isna(x.iloc[0].sector_forward))

    def test_restructuring_peer_is_not_used_as_a_price_return(self):
        days,industries,bars=self.fixture()
        event={'symbols':['S6'],'impact_start':days[2],'impact_end':days[2]}
        x=sector_forward_returns(bars,industries,days,2,[event],['S0'],min_history=1)
        self.assertTrue(pd.isna(x.iloc[0].sector_forward))

    def test_peer_eligibility_uses_past_not_future_listing_data(self):
        days,industries,bars=self.fixture()
        industries['IPO']='Sector'
        extra=pd.DataFrame([{'symbol':'IPO','trade_date':d,'close':100} for d in days[4:]])
        with_ipo=pd.concat([bars,extra],ignore_index=True)
        x=sector_forward_returns(with_ipo,industries,days,2,[],['S0'],min_history=1)
        self.assertEqual(x.iloc[0].sector_expected_peers,6)
        self.assertAlmostEqual(x.iloc[0].sector_forward,1.01**2-1)

    def test_signal_missing_from_benchmark_calendar_stays_unknown(self):
        frame=pd.DataFrame([{'symbol':'S0','trade_date':'2024-01-02'}])
        values=execution_targets(frame,{},['2024-01-01'],[],Policy())
        self.assertEqual(values.iloc[0].target_status,'missing_calendar')
        self.assertIsNone(values.iloc[0].execution_net)

    def test_maturity_purge_applies_even_to_early_stopped_trades(self):
        frame=pd.DataFrame([{'symbol':'S0','trade_date':'2024-01-01','execution_net':-.08,'sector_forward':.01,'target_maturity':d} for d in ['2024-02-01','2024-02-02']])
        x=matured_training(frame,['2024-01-01'],'2024-02-02')
        self.assertEqual(len(x),1)
        self.assertAlmostEqual(x.iloc[0].sector_relative_net,-.09)


class DecisionEvaluationTests(unittest.TestCase):
    def test_fixed_net_forecast_cutoff_is_not_a_rank_or_probability(self):
        pool=[{'symbol':'A','absolute_score':.01,'relative_score':.1}, {'symbol':'B','absolute_score':.01001,'relative_score':-.1}, {'symbol':'C','absolute_score':.02,'relative_score':.01}]
        self.assertEqual([r['symbol'] for r in select_rows(pool,'absolute_selective')],['C','B'])
        self.assertEqual([r['symbol'] for r in select_rows(pool,'relative_selective')],['C'])
        self.assertEqual(len(select_rows(pool,'relative_always')),3)

    def test_unfilled_basket_slots_remain_cash(self):
        with patch('ml.evaluation.decision_evaluation.outcome',return_value={'status':'complete','net_return':.1}):
            result=basket([{'symbol':'A'}],0,{},[],[],Policy())
        self.assertAlmostEqual(result['net_return'],.02)
        self.assertAlmostEqual(result['exposure'],.2)
        self.assertEqual(basket([],0,{},[],[],Policy())['net_return'],0)

    def test_unknown_selected_outcome_is_not_removed(self):
        with patch('ml.evaluation.decision_evaluation.outcome',return_value={'status':'missing_price'}):result=basket([{'symbol':'A'}],0,{},[],[],Policy())
        self.assertEqual(result['status'],'incomplete')
        self.assertIsNone(result['net_return'])

    def test_cash_cycles_do_not_count_as_active_evidence(self):
        cycles=[{'signal_date':str(i),'status':'complete','selected_count':0,'exposure':0,'net_return':0} for i in range(10)]
        self.assertEqual(cycle_summary(cycles)['active_cycles'],0)
        self.assertEqual(paired(cycles,cycles)['active_complete_pairs'],0)
        cycles[0]['status']='incomplete';cycles[0]['net_return']=None
        self.assertAlmostEqual(paired(cycles,cycles)['coverage'],.9)
        self.assertEqual(cycle_summary(cycles)['status'],'incomplete')

    def test_eight_active_cycles_required_even_when_all_returns_look_good(self):
        controls=['absolute_always','momentum']
        retail={'status':'complete','net_return':.2,'closed_trades':30,'max_drawdown':-.1}
        row={'basket_summary':{'status':'complete','active_cycles':8,'net_return':.1},'retail':retail,
             'comparisons':{c:{'coverage':1,'mean_difference':.01} for c in controls},
             'exposure_matched_comparisons':{c:{'coverage':1,'mean_difference':.01} for c in controls}}
        strategy={scenario:dict(row) for scenario in ['base','double_costs','entry_delayed']}
        result={'strategies':{'relative_selective':strategy,**{c:{s:{'retail':{**retail,'net_return':.1}} for s in strategy} for c in controls}}}
        self.assertEqual(qualify(result,[result]*3,'relative_selective'),[])
        row['basket_summary']['active_cycles']=4
        reasons=qualify(result,[result]*3,'relative_selective')
        self.assertTrue(any('eight' in reason for reason in reasons))

    def test_schedule_uses_horizon_and_delay_without_overlap(self):
        days=pd.bdate_range('2024-01-01',periods=140).strftime('%Y-%m-%d').tolist()
        cycles=schedule(days,days,days[0],days[-1],63,1)
        self.assertEqual(cycles[0],(days[0],2))
        self.assertEqual(cycles[1][1],66)

    def test_selective_control_has_identical_exposure_and_cycle_dates(self):
        days=pd.bdate_range('2024-01-01',periods=25).strftime('%Y-%m-%d').tolist()
        p=pd.DataFrame([{'symbol':f'S{i}','trade_date':days[0],'absolute_score':.02 if i==0 else -.01,'relative_score':i,'momentum':i} for i in range(7)])
        book={'status':'complete','blocked':None,'net_return':0,'max_drawdown':0,'trade_win_rate':0,'trades':[],'open_positions':{},'curve':[]}
        with patch('ml.evaluation.decision_evaluation.candidates',side_effect=lambda rows,*args:rows),patch('ml.evaluation.decision_evaluation.outcome',return_value={'status':'complete','net_return':.01}),patch('ml.evaluation.decision_evaluation.simulate',return_value=book):
            r=evaluate(p,{}, {},days,[],days[0],days[-1],21)
        row=r['strategies']['absolute_selective']['base']
        self.assertEqual(row['cycles'][0]['selected_count'],1)
        matched=row['matched_control_cycles']['momentum'][0]
        self.assertEqual(matched['selected_count'],1)
        self.assertEqual(matched['signal_date'],row['cycles'][0]['signal_date'])
        self.assertEqual(matched['exposure'],row['cycles'][0]['exposure'])


if __name__=='__main__':unittest.main()

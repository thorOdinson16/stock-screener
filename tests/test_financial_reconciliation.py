"""Original table reviews must retain units, publication and source integrity."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ml.evaluation.financial_reconciliation import load_reviews
from ml.evaluation.model_tournament import sha
from ml.evaluation.reconcile_pilot import match_table


class ReconciliationTests(unittest.TestCase):
    def fixture(self,root):
        config=root/'config.json';config.write_text(json.dumps({'symbols':[{'symbol':'A.NS','isin':'ID'}]}))
        pdf=root/'a.pdf';pdf.write_bytes(b'%PDF-1.4\noriginal financial table\n%%EOF')
        text=root/'a.txt';text.write_text('A Limited\nConsolidated quarter ended31 March2022\nINR million\nRevenue100\nProfit10')
        source='https://nsearchives.nseindia.com/corporate/a.pdf'
        url='https://www.nseindia.com/api/corporate-announcements?symbol=A'
        index=root/'index.json';index.write_text(json.dumps({'source':url,'records':[{'attchmntFile':source,'symbol':'A','sm_isin':'ID','an_dt':'01-May-2022 18:00:00','exchdisstime':'01-May-2022 18:00:10'}]}))
        e={'symbol':'A.NS','period_end':'2022-03-31','isin':'ID','exchange_symbol':'A','source':source,'index_source':url,'published_at':'2022-05-01T18:00:10+05:30','first_seen_at':'2026-11-09T20:00:00+05:30'}
        for name,file in [('pdf',pdf),('text',text),('index',index)]:e[name+'_file']=str(file);e[name+'_sha256']=sha(file)
        review={'symbol':'A.NS','period_end':'2022-03-31','source':source,'pdf_sha256':sha(pdf),'page':1,'unit_factor':1e6,'reported_revenue':100,'reported_profit':10,'status':'reviewed_original_table','scope':'Consolidated','currency':'INR','evidence':e}
        ledger={'pilot_config_sha256':sha(config),'reviews':[review]};file=root/'reviews.json';file.write_text(json.dumps(ledger))
        return config,file,ledger,pdf

    def test_million_is_not_crore_and_availability_is_after_dissemination(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg,review,_,_=self.fixture(root)
            facts,hashes,_=load_reviews(root,cfg,{},review)
            self.assertEqual(facts[0]['revenue'],100000000)
            self.assertEqual(facts[0]['profit'],10000000)
            self.assertEqual(facts[0]['available_date'],'2022-05-02')
            self.assertIn(str(review),hashes)

    def test_changed_original_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg,review,_,pdf=self.fixture(root);pdf.write_bytes(b'%PDF changed\n%%EOF')
            with self.assertRaisesRegex(ValueError,'evidence changed'):load_reviews(root,cfg,{},review)

    def test_backdated_publication_and_unreviewed_table_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg,review,ledger,_=self.fixture(root)
            ledger['reviews'][0]['evidence']['published_at']='2022-05-01T18:00:00+05:30';review.write_text(json.dumps(ledger))
            with self.assertRaisesRegex(ValueError,'Publication is not bound'):load_reviews(root,cfg,{},review)
            ledger['reviews'][0]['status']='pending';review.write_text(json.dumps(ledger))
            with self.assertRaisesRegex(ValueError,'Unreviewed'):load_reviews(root,cfg,{},review)

    def test_same_number_in_wrong_profit_column_is_not_corroboration(self):
        text='Consolidated INR crore quarter ended31 March2022\nRevenue from operations         100       200\nProfit for the period           20        10'
        self.assertIsNone(match_table(text,100e7,10e7,'2022-03-31'))


if __name__=='__main__':unittest.main()

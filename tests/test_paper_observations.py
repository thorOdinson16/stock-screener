"""Prospective observations preserve first prices and frozen evaluation rules."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from poller.paper_observations import completed, merge_observations, held_window_revisions, unique_revisions, verify_frozen
from ml.evaluation.forward_tracking import IST


class ObservationTests(unittest.TestCase):
    def test_intraday_and_future_quotes_are_excluded(self):
        now=datetime(2026,11,9,17,59,tzinfo=IST)
        self.assertTrue(completed('2026-11-06',now))
        self.assertFalse(completed('2026-11-09',now))
        self.assertFalse(completed('2026-11-10',now))
        self.assertTrue(completed('2026-11-09',now.replace(hour=18,minute=0)))

    def test_revised_prices_cannot_resize_or_rewrite_first_observation(self):
        first={'symbol':'A','trade_date':'2026-11-09','open':100,'low':95,'close':102}
        old={};bars,revisions=merge_observations(old,[first])
        updated,revisions=merge_observations(bars,[{**first,'close':51}])
        self.assertEqual(updated['A|2026-11-09']['close'],102)
        self.assertEqual(len(revisions),1)
        self.assertEqual(old,{})
        self.assertEqual(len(unique_revisions(revisions+revisions)),1)

    def test_revision_after_exit_cannot_invalidate_a_completed_cohort(self):
        record={'publication_date':'2026-11-09','policy':{'horizon':2}}
        days=['2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13']
        revisions=[{'symbol':s,'trade_date':d,'reason':'changed'} for s,d in [('A','2026-11-09'),('A','2026-11-10'),('A','2026-11-12'),('A','2026-11-13'),('B','2026-11-11')]]
        found=held_window_revisions(record,[{'symbol':'A'}],days,revisions)
        self.assertEqual([r['trade_date'] for r in found],['2026-11-10','2026-11-12'])
        self.assertEqual(held_window_revisions(record,[{'symbol':'A'}],days[:1],revisions),[])

    def test_changed_execution_source_blocks_valuation(self):
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);evaluation=root/'ml/evaluation';evaluation.mkdir(parents=True)
            source=evaluation/'rules.py';source.write_text('fixed rules')
            model=root/'model';model.write_bytes(b'frozen weights')
            protocol=root/'protocol.json';protocol.write_text(json.dumps({'source_hashes':{'rules.py':digest(source)}}))
            (root/'paper_models.json').write_text(json.dumps({'protocol_sha256':digest(protocol),'models':{'candidate':{'file':'model','sha256':digest(model)}}}))
            with patch('poller.paper_observations.ROOT',root):
                verify_frozen(root)
                source.write_text('changed rules')
                with self.assertRaisesRegex(RuntimeError,'Frozen trial source changed'):verify_frozen(root)


if __name__=='__main__':unittest.main()

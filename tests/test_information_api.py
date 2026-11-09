"""Information trial API stays readable without training libraries."""
from pathlib import Path
import sys
import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.evaluation.information_report import read_report
from api.planner import information_research


class ApiTests(unittest.TestCase):
    def test_missing_trial_never_claims_evidence(self):
        with tempfile.TemporaryDirectory() as d:r=read_report(d)
        self.assertFalse(r['evidence_current'])
        self.assertFalse(r['production_changed'])
        self.assertEqual(r['status'],'not_available')

    def test_endpoint_does_not_query_markets_or_train(self):
        with patch('ml.evaluation.information_report.read_report',return_value={'status':'historical_research'}) as reader, patch('api.planner.query') as query:
            self.assertEqual(information_research(),{'status':'historical_research'})
        reader.assert_called_once_with()
        query.assert_not_called()


    def test_changed_frozen_weights_mark_evidence_stale(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            protocol={'source_hashes':{},'filing_files':{},'base_protocol':'unavailable','input_manifest':'unavailable'}
            (root/'protocol.json').write_text(json.dumps(protocol))
            report={'protocol':protocol,'results':{},'selected_on_validation':None,'filing_coverage':{},'feature_coverage':{},'limits':[]}
            (root/'report.json').write_text(json.dumps(report))
            (root/'model.joblib').write_bytes(b'changed')
            receipt={'protocol_sha256':hashlib.sha256((root/'protocol.json').read_bytes()).hexdigest(),
                     'models':{'candidate':{'file':'model.joblib','sha256':'old-digest'}}}
            (root/'paper_models.json').write_text(json.dumps(receipt))
            result=read_report(root)
            self.assertFalse(result['evidence_current'])
            self.assertEqual(result['status'],'stale_evidence')
            self.assertIn('Frozen paper weights changed',result['stale_reasons'])


if __name__=='__main__':unittest.main()

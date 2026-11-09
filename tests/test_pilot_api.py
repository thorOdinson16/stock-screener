"""Data readiness is separate from model quality and needs no ML API imports."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.evaluation.pilot_report import read_report
from api.planner import financial_pilot


class PilotApiTests(unittest.TestCase):
    def test_missing_audit_cannot_be_ready(self):
        with tempfile.TemporaryDirectory() as d:r=read_report(d)
        self.assertFalse(r['ready'])
        self.assertFalse(r['evidence_current'])
        self.assertFalse(r['production_changed'])

    def test_api_does_not_fetch_prices_or_start_training(self):
        with patch('ml.evaluation.pilot_report.read_report',return_value={'ready':False}) as reader, patch('api.planner.query') as query:
            self.assertEqual(financial_pilot(),{'ready':False})
        reader.assert_called_once_with();query.assert_not_called()


if __name__=='__main__':unittest.main()

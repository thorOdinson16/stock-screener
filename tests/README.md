# Tests

```bash
pip install -r requirements-dev.txt
make test            # = python -m pytest -q
make ci              # static checks + tests + frontend typecheck/build
```

* Most files are `unittest` style and need nothing but the packages in
  `requirements-dev.txt`. Six older files (`test_features`, `test_indicators`,
  ...) also run as plain scripts: `python tests/test_indicators.py`.
* Tests that need heavy optional libraries (torch, xgboost, lightgbm, catboost)
  skip themselves when those are missing (`tests/_optional.py`).
* **Opt-in** tests that exercise real infrastructure are skipped by default:
  * Spark/Iceberg: `spark-submit --jars <iceberg-runtime.jar> tests/<file>.py --spark`
    (`test_incremental`, `test_forward_integration`,
    `test_training_quality_integration`, `test_tournament_live_integration`)
  * Full stack: `RUN_E2E=1 python tests/test_pipeline_e2e.py --limit 10`
* Tests never write into `ml/evaluation/results/` (the frozen research
  evidence). If you add one that shells out to a pipeline script, override every
  results path it reads (see `test_tournament_forward.py`).
* `import poller.x` does not work in tests: `tests/conftest.py` puts `poller/`
  on `sys.path`, so import its scripts flat (`import backfill`).
* Use pandas 2.x (see `requirements-dev.txt`); `ml/feature_engineering/asof.py`
  does not work under pandas 3.

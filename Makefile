# Developer shortcuts. `make test` needs: pip install -r requirements-dev.txt
PYTHON ?= python3

.PHONY: test check typecheck frontend-build ci

test:            ## unit tests (Spark integration tests are opt-in and skip themselves)
	$(PYTHON) -m pytest -q

check:           ## cheap static checks: every Python file compiles, every shell script parses
	$(PYTHON) -m compileall -q -x '(node_modules|\.venv)' api airflow benchmarks docker druid ml monitoring poller spark tests
	@for f in $$(git ls-files '*.sh'); do bash -n $$f || exit 1; done
	@echo "static checks ok"

typecheck:       ## frontend type check
	cd frontend && npm run typecheck

frontend-build:  ## frontend production build
	cd frontend && npm run build

ci: check test typecheck frontend-build

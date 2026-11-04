PY ?= python
TESTS = normalize adapters clock places store migrate server

.PHONY: install test oracle run report serve

install:
	$(PY) -m pip install -r requirements.txt

test:            ## run the unit tests (SQLite; set TEST_MYSQL_URL to also test MySQL)
	@for t in $(TESTS); do $(PY) -m tests.test_$$t || exit 1; done

oracle:          ## run the whole pipeline without API calls
	$(PY) -m newsai.run --oracle --note oracle

run:             ## live run against the LLM (needs DEEPSEEK_API_KEY)
	$(PY) -m newsai.run --note "$(NOTE)"

report:          ## score the latest run against the labelled fixtures
	$(PY) -m newsai.report

serve:           ## HTTP control server: GET /status, POST /runs
	$(PY) -m newsai.server

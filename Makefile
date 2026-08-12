PY ?= python
export PYTHONPATH := .

.PHONY: help install seed run eval eval-online test clean docker

help:
	@echo "Targets:"
	@echo "  make install     - pip install -e .[dev]"
	@echo "  make seed        - create demo/demo.db with synthetic data"
	@echo "  make run         - start the FastAPI app on :8000"
	@echo "  make eval        - run the offline eval (no LLM key needed)"
	@echo "  make eval-online - run the eval against the real LLM"
	@echo "  make test        - run pytest"
	@echo "  make clean       - remove demo.db and caches"
	@echo "  make docker      - start the docker-compose stack"

install:
	$(PY) -m pip install -e .[dev]

seed:
	$(PY) -m demo.seed

run:
	$(PY) -m uvicorn api.main:app --reload --port 8000

eval:
	$(PY) eval/run_eval.py

eval-online:
	$(PY) eval/run_eval.py --online

test:
	$(PY) -m pytest tests/ -v

clean:
	rm -f demo/demo.db
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__

docker:
	docker compose up --build

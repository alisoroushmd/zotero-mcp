.PHONY: install test lint format check

install:
	pip install -e ".[dev,graph,fulltext]"

test:
	python -m pytest tests/ -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

check: lint test

.PHONY: dev up down scan enrich test-unit smoke-test

dev:
	uvicorn src.main:app --reload --port 8000

up:
	docker-compose up -d

down:
	docker-compose down

scan:
	curl -s -X POST http://localhost:8000/admin/scan | python -m json.tool

enrich:
	curl -s -X POST http://localhost:8000/admin/enrich | python -m json.tool

test-unit:
	python -m pytest tests/unit -v

smoke-test:
	@echo "=== Health ===" && curl -s http://localhost:8000/health | python -m json.tool
	@echo "=== Tables ===" && curl -s http://localhost:8000/catalog/tables | python -m json.tool
	@echo "=== Metrics ===" && curl -s http://localhost:8000/metrics | python -m json.tool
	@echo "=== NL Query ===" && curl -s -X POST http://localhost:8000/query/natural-language \
		-H "Content-Type: application/json" \
		-d '{"question": "What is the total revenue?"}' | python -m json.tool

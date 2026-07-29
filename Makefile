.PHONY: dev up up-aura down scan enrich test-unit smoke-test

dev:
	uvicorn src.main:app --reload --port 8000

# CE: start the local Neo4j container alongside the app
up:
	docker-compose --profile ce up -d

# Aura: start the app only; connect to Aura via NEO4J_URI in .env
up-aura:
	docker-compose up -d

down:
	docker-compose --profile ce down

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

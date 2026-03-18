# RoseAmor — Makefile
# Usage: make <target>

.PHONY: install pipeline api test clean

## Install all dependencies
install:
	pip install -r requirements.txt

## Run ETL pipeline (raw → staging → marts)
pipeline:
	python3 etl/pipeline.py

## Start the FastAPI web server 
api:
	uvicorn app.main:app --reload --port 8000

## Run pipeline + start API
all: pipeline api

## Remove generated database (reset)
clean:
	rm -f data/roseamor.db
	@echo "Database removed. Run 'make pipeline' to rebuild."

## Quick smoke-test via curl (requires running API)
test:
	curl -s http://localhost:8000/health | python3 -m json.tool
	@echo ""
	@echo "Registering test order..."
	curl -s -X POST http://localhost:8000/orders \
	  -H "Content-Type: application/json" \
	  -d '{"order_id":"TEST001","customer_id":"C0001","sku":"SKU0001","quantity":2,"unit_price":25.00,"order_date":"2025-01-15","channel":"ecommerce"}' \
	  | python3 -m json.tool
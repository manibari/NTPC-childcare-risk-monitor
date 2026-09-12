PY ?= python3
PORT ?= 8765
DB = data/watchdog.sqlite
DEMO = data/demo/watchdog-demo.sqlite

.PHONY: bootstrap deps db update serve test demo-db clean-db

## clone → running app on real data in ≤ 5 min, no raw PDFs, no OCR, no API key needed
bootstrap: deps db update serve

deps:
	$(PY) -m pip install -q -e ".[dev]"

## use the committed demo snapshot when there is no local DB and no kiang JSON
db:
	@if [ ! -f $(DB) ] && [ ! -f data/kiang_preschools.json ]; then \
	  echo "no local data → copying $(DEMO)"; mkdir -p data; cp $(DEMO) $(DB); fi

## rebuild everything that can be rebuilt offline (linker → score → schedule); add FETCH=1 to pull kiang, TRAIN=1 to retrain
update:
	@if [ -f data/kiang_preschools.json ]; then FROM=build; else FROM=linker; fi; \
	$(PY) scripts/update.py --from $$FROM $(if $(FETCH),,--no-fetch) $(if $(TRAIN),,--no-train)

serve:
	@echo "→ http://localhost:$(PORT)/"; $(PY) -m uvicorn app.main:app --port $(PORT)

test:
	$(PY) -m pytest

demo-db:
	$(PY) scripts/make_demo_db.py

clean-db:
	rm -f $(DB) $(DB)-wal $(DB)-shm

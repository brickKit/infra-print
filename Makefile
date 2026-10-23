.PHONY: test test-integration lint

test:  ## unit tests: the manifest, the OpenAPI document and the routes, merge safety, rendering and its limits
	uv run pytest -q

test-integration:  ## L2 against PostgreSQL and the bus (BE_SDK_TEST_PG_ADMIN, BE_SDK_TEST_NATS_URL)
	uv run pytest -q -m integration

lint:
	brickkit lint

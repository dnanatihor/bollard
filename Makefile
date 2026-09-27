.PHONY: backend frontend dev db up down

backend:
	poetry -C backend run gateway

frontend:
	npm --prefix frontend start

db:
	docker compose up -d postgres

dev: db
	@bash -c 'set -m; trap "kill 0" INT TERM EXIT; \
		POSTGRES_HOST=127.0.0.1 poetry -C backend run gateway & \
		npm --prefix frontend start & \
		wait'

up:
	docker compose up

down:
	docker compose down

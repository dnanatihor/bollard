# Run Bollard

Use one of the two boots below. Both start from the repository root.

| How you want to run | Command | Open |
| --- | --- | --- |
| Docker | `make up` | http://127.0.0.1:8080 |
| On this machine, with reload | `make dev` | http://127.0.0.1:5173 |

Docker serves the API and the console from the Bollard container. A machine run serves the console with Vite and proxies API calls to Bollard on port 8080.

## Docker

```bash
make up
```

| Service | Address | What it does |
| --- | --- | --- |
| bollard | http://127.0.0.1:8080 | API and console |
| postgres | `127.0.0.1:5432` | Database, password from `.env` |
| redis | `127.0.0.1:6379` | Shared rate limit, password from `.env` |

Compose binds all three ports to `127.0.0.1`. Other machines on the network cannot open the console, PostgreSQL, or Redis. Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `GATEWAY_KEY_PEPPER`, and `GATEWAY_MASTER_KEY` before the first boot. Compose refuses to start when any of those is empty.

`GET /health` returns `{"status":"ok"}` when the gateway can reach PostgreSQL.

Stop the stack:

```bash
make down
```

After a code change, rebuild the image and start again:

```bash
docker compose up --build
```

The database user defaults to `gateway`. The password is `POSTGRES_PASSWORD` in `.env`. `GATEWAY_DATABASE_URL`, when set, is the full SQLAlchemy URL and replaces the `POSTGRES_*` fields. Changing `POSTGRES_PASSWORD` after the volume exists also requires `ALTER USER` inside PostgreSQL; the image applies that variable only on the first initialization.

PostgreSQL data is the `postgres-data` volume. Redis writes an append-only file to the `redis-data` volume, so the shared rate-limit window survives a restart. `make down` keeps these volumes. Delete them only when you intend to wipe the database and the rate-limit log:

```bash
docker compose down -v
```

## On this machine

You need Python 3.11 or newer, Node.js 22 or newer, and Docker for PostgreSQL.

Install once:

```bash
poetry -C backend install
npm --prefix frontend install
```

Start PostgreSQL, the API, and the console:

```bash
make dev
```

| Process | Address |
| --- | --- |
| Console | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8080 |
| PostgreSQL | `127.0.0.1:5432`, password from `.env` |

Ctrl+C stops the API and the console. PostgreSQL keeps running. Stop it with:

```bash
docker compose stop postgres
```

Start one process on its own:

```bash
make backend     # API only. Uses SQLite at data/gateway.db unless POSTGRES_HOST is set
make frontend    # console only, at http://127.0.0.1:5173
```

`make backend` runs `poetry -C backend run gateway`. The virtualenv is `backend/.venv`. The schema in `backend/alembic` is created on first startup, and the process reloads when files in `backend/gateway` or `backend/alembic` change.

Build the console into `frontend/dist` when you want the Docker image to pick up UI changes:

```bash
npm --prefix frontend run build
```

## Create the admin key

Set `GATEWAY_KEY_PEPPER` and `GATEWAY_MASTER_KEY` in `.env` before this step. Open the console address from the table above, type the application name, and click **Create admin access**. Copy the `aigw_live_...` key. It is shown once and belongs to that application. Later visits use **Sign in** with that key. `POST /api/bootstrap` stays open until that key exists, so create it before anyone else can reach the console. Send `{"applicationName":"..."}` with that request.

That key is an admin. Open **Users**, create a person, and click **Issue key**. Choose the date the key is allowed until. The plaintext is shown once. **API keys** can rotate or revoke a key. Disabling a user stops every key that belongs to them.

An inference user can call `/v1` and cannot open audit. An auditor can read audit, traces, and guardrail decisions and cannot change configuration.

## Providers

Put vendor keys in `.env` before the first admin setup if you want them imported automatically:

```text
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_BASE_URL=
BEDROCK_API_KEY=
```

The first admin setup imports each provider whose vendor key is set. Startup then adds two catalog models and two routes for every provider that exists, and creates the guardrail rules if they are missing. You can add or disable providers later in the console. A secret saved there is encrypted and is not returned by the API.

Azure needs a base URL. Bedrock uses a bearer API key and the region field, default `us-east-1`.

## Call a model

Calls go to the API on port 8080 in both boots.

```bash
curl -sS http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer $GATEWAY_API_KEY"

curl -sS http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"general-fast","messages":[{"role":"user","content":"Reply with one short sentence."}]}'
```

The `model` value is a route id or a catalog model id. A missing model returns 404. A missing key returns 401.

Input guardrails run before the provider. Output guardrails run before the client receives the text. A block returns HTTP 400 with `AI_GW_INPUT_BLOCKED` or `AI_GW_OUTPUT_BLOCKED`. The same call is an audit event and a trace. Open **Traces** and use the `x-trace-id` response header.

## Where state lives

| Path or volume | Contents |
| --- | --- |
| `data/gateway.db` | SQLite file used by `make backend` when `POSTGRES_HOST` is unset |
| `data/master.key` | Encryption key for provider secrets when `GATEWAY_MASTER_KEY` is unset |
| `postgres-data` | PostgreSQL data for Docker and for `make dev` |
| `redis-data` | Redis append-only file for the shared rate limit and prompt cache |
| `gateway-data` | Mounted at `/app/data` in the Bollard container. Unused while `GATEWAY_MASTER_KEY` is set |
| `.env` | Pepper, master key, and the PostgreSQL and Redis passwords |

`data/` and `.env` are gitignored. An existing database is kept. On startup the schema is created or migrated to the single Alembic revision. If the database already has tables and an unknown revision id, that row is moved to the current head and the data is left in place. A SQLite file is not copied into PostgreSQL. Deleting `data/` returns a SQLite run to the first-run screen.

`GATEWAY_KEY_PEPPER` is mixed into gateway key hashes. `GATEWAY_MASTER_KEY` is a Fernet key, generated with the command in `.env.example`. A password you choose is rejected, and provider import during admin setup then fails. Set both before creating keys or saving provider secrets, and keep them. A later change makes existing keys and stored secrets unreadable. Docker Compose requires both. A machine run without `GATEWAY_MASTER_KEY` falls back to `data/master.key`. `GATEWAY_DB_POOL_SIZE` (default 5) and `GATEWAY_DB_MAX_OVERFLOW` (default 10) apply to PostgreSQL.

## Tests

```bash
poetry -C backend run pytest -q
```

Tests use a temporary database and do not read vendor keys from `.env`. To run them against PostgreSQL, use a database you can wipe. The tests drop and recreate the schema. Do not point `GATEWAY_TEST_DATABASE_URL` at the database the running gateway is using.

```bash
docker compose up -d postgres
set -a && source .env && set +a
docker compose exec postgres createdb -U gateway gateway_test
GATEWAY_TEST_DATABASE_URL="postgresql+psycopg://gateway:${POSTGRES_PASSWORD}@127.0.0.1:5432/gateway_test" \
  poetry -C backend run pytest -q
```

## License

Bollard is [PolyForm Noncommercial 1.0.0](LICENSE). Use it for personal and noncommercial work. Commercial use needs a separate license from the copyright holder.

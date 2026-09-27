# Architecture

Bollard is one Python process. It owns the control plane and the data plane. The React console is a static build served by that process.

```text
browser  --->  FastAPI
client   --->  POST /v1/chat/completions
                 authenticate key
                 input guardrails
                 resolve route
                 rate limit and budget
                 provider HTTP call
                 output guardrails
                 audit event and trace
```

PostgreSQL is the system of record when `POSTGRES_HOST` or `GATEWAY_DATABASE_URL` is set. Redis, when `REDIS_URL` is set, holds the shared per-minute rate limit, the exact prompt cache, and the circuit breaker. In Compose that data is the `redis-data` volume, with append-only persistence, and Redis requires `REDIS_PASSWORD`. Compose publishes the gateway, PostgreSQL, and Redis on `127.0.0.1` only. A host process with neither database variable uses SQLite, and without Redis the rate limit stays in memory.

Tenants, applications, and users live in the database. An admin creates a user and issues a gateway key for that user. Keys can be rotated and revoked. A disabled user cannot call the gateway. Gateway key hashes include `GATEWAY_KEY_PEPPER`. Provider secrets are encrypted with `GATEWAY_MASTER_KEY`. When that variable is unset, a machine run uses `data/master.key`. Both values have to stay stable for the life of the keys and the stored secrets.

Each call appends a usage row and a hash-chained audit event. The event stores the prompt, the payload after guardrails, and the model reply. Each person has a monthly cap. The workspace also has its own ceiling for the month; zero means there is no workspace ceiling, and personal caps do not have to add up to it. A call stops when the person hits their cap or when workspace spend reaches the ceiling. Spend is checked before the provider is called. `GET /metrics` exposes counters. A route may name a fallback catalog model. After five provider failures the circuit stays open for 30 seconds. Guardrail rules apply globally, or only to the route named in `route_id`.

Roles:

| Role | Can call `/v1` | Can read audit and traces | Can change configuration |
| --- | --- | --- | --- |
| inference | yes | no | no |
| auditor | no | yes | no |
| admin | yes | yes | yes |

Provider calls use `httpx`. Supported types are `openai`, `openai-compatible`, `anthropic`, `gemini`, `azure-openai`, and `bedrock`. Chat requests may include tools. OpenAI-compatible providers can stream tokens when no output guardrail applies to that route. Bedrock accepts a bearer token, or JSON credentials with `accessKeyId` and `secretAccessKey`, which the gateway signs with SigV4.

`POST /v1/embeddings` and `POST /v1/rerank` forward to the resolved provider. `POST /v1/responses` runs the same chat pipeline and returns an OpenAI Responses object. The WebSocket `/v1/realtime` proxies an OpenAI-compatible realtime model. The query string carries `access_token` and `model`.

A route strategy is `direct`, `weighted`, `round_robin`, `least_cost`, `least_latency`, `conditional`, `canary`, or `region`. Conditional routes match a request header. Region routes match `x-region` or the `gateway_region` setting and try the other region targets when the first provider call fails. Direct and least-cost choices can be memoized for `config_cache_seconds`. Exact prompt cache is a tenant-scoped Redis entry, or an in-process entry when Redis is unset, controlled by `cache_ttl_seconds`. Semantic cache is off until `semantic_threshold` and `semantic_model` are set; it embeds the guarded prompt and reuses a reply whose cosine similarity meets the threshold. The circuit breaker is stored in Redis when `REDIS_URL` is set, so replicas share it.

MCP servers are registered in the console. Upstream auth is none, a bearer secret, OAuth, or up to five custom headers. The console lists the tools the server returns. A caller posts JSON-RPC to `/mcp/{server id}`. The gateway checks that person’s tool grant, adds the stored credential, writes an `mcp.tool` span, and audits `tools/call`. An empty grant list allows every tool. OAuth access tokens are refreshed from the stored refresh token. `POST /api/agents/run` lists those tools and loops a chat model for at most eight steps. A configured issuer, audience, and JWKS URL lets a JWT stand in for a gateway key when its email or `sub` matches a user. Hosted sign-in is an authorization-code redirect to `/auth/login` that sets an `aigw_session` cookie. Admins can require `x-client-verify: SUCCESS` when a TLS terminator checks client certificates. `x-workspace` lets an admin operate in another tenant. Chat accepts `promptId` to prepend a saved prompt version. `GET /api/config` exports models, routes, guardrails, and MCP server URLs without secrets.

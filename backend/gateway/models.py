from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[str] = mapped_column(String(40))


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[str] = mapped_column(String(40))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    application_id: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    external_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))


class UserBudget(Base):
    __tablename__ = "user_budgets"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    monthly_usd: Mapped[float] = mapped_column(Float, default=25)
    spent_usd: Mapped[float] = mapped_column(Float, default=0)
    period: Mapped[str] = mapped_column(String(7), default="")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(32))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    application_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class CatalogModel(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    provider_model: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256))
    input_price_per_million: Mapped[float] = mapped_column(Float, default=0)
    output_price_per_million: Mapped[float] = mapped_column(Float, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="chat")


class VirtualModel(Base):
    __tablename__ = "virtual_models"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"))
    fallback_model_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    strategy: Mapped[str] = mapped_column(String(32), default="direct")
    targets_json: Mapped[str] = mapped_column(Text, default="[]")


class GuardrailRule(Base):
    __tablename__ = "guardrail_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    route_id: Mapped[str | None] = mapped_column(String(256), nullable=True)


class Budget(Base):
    __tablename__ = "budgets"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    monthly_usd: Mapped[float] = mapped_column(Float, default=25)
    overall_usd: Mapped[float] = mapped_column(Float, default=0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(128))
    application_id: Mapped[str] = mapped_column(String(128), default="")
    user_id: Mapped[str] = mapped_column(String(256), default="")
    key_id: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(256), default="")
    provider: Mapped[str] = mapped_column(String(128), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="chat")
    created_at: Mapped[str] = mapped_column(String(40))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(128))
    application_id: Mapped[str] = mapped_column(String(128), default="")
    user_id: Mapped[str] = mapped_column(String(256), default="")
    requested_model: Mapped[str] = mapped_column(String(256), default="")
    provider: Mapped[str] = mapped_column(String(128), default="")
    provider_model: Mapped[str] = mapped_column(String(256), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    timestamp: Mapped[str] = mapped_column(String(40))
    prev_hash: Mapped[str] = mapped_column(String(128))
    hash: Mapped[str] = mapped_column(String(128))
    prompt_text: Mapped[str] = mapped_column(Text, default="")
    sent_text: Mapped[str] = mapped_column(Text, default="")
    response_text: Mapped[str] = mapped_column(Text, default="")


class Trace(Base):
    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(128))
    application_id: Mapped[str] = mapped_column(String(128), default="")
    user_id: Mapped[str] = mapped_column(String(256), default="")
    model: Mapped[str] = mapped_column(String(256), default="")
    provider: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[str] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Span(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.trace_id"))
    name: Mapped[str] = mapped_column(String(128))
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")


class GuardrailHit(Base):
    __tablename__ = "guardrail_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(16))
    rule: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40))


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(512))
    auth_type: Mapped[str] = mapped_column(String(32), default="none")
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorize_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_client_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    oauth_client_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_scopes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    refresh_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40))


class McpGrant(Base):
    __tablename__ = "mcp_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_servers.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tools_json: Mapped[str] = mapped_column(Text, default="[]")


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40))


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40))


class SemanticEntry(Base):
    __tablename__ = "semantic_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(256))
    vector_json: Mapped[str] = mapped_column(Text, default="[]")
    response_text: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40))

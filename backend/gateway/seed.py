import json
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gateway.budgets import ensure_user_budget
from gateway.crypto import encrypt_secret
from gateway.directory import ADMIN_USER, DEFAULT_TENANT, ensure_application, ensure_directory
from gateway.models import (
    ApiKey,
    Budget,
    CatalogModel,
    GuardrailRule,
    Prompt,
    Provider,
    Setting,
    Tenant,
    User,
    VirtualModel,
)
from gateway.security import generate_key, hash_key

RULES = (
    ("prompt-injection", "Prompt injection", "prompt-injection", "input", {}),
    ("toxicity", "Toxicity", "toxicity", "input", {"terms": ["kill yourself"]}),
    ("pii", "PII redaction", "pii", "both", {}),
    ("secret", "Secret redaction", "secret", "both", {}),
    ("max-input", "Maximum input length", "max-input", "input", {"maxChars": 100000}),
    ("blocked-words", "Blocked words", "blocked-words", "input", {"words": []}),
)

# id, provider, provider model, display name, input $ / million, output $ / million
CATALOG = (
    ("openai/gpt-4o-mini", "openai", "gpt-4o-mini", "GPT-4o mini", 0.15, 0.60),
    ("openai/gpt-4.1", "openai", "gpt-4.1", "GPT-4.1", 2.00, 8.00),
    ("anthropic/claude-haiku", "anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5", 1.00, 5.00),
    ("anthropic/claude-sonnet", "anthropic", "claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", 3.00, 15.00),
    ("gemini/flash", "gemini", "gemini-2.5-flash", "Gemini 2.5 Flash", 0.30, 2.50),
    ("gemini/pro", "gemini", "gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.00),
    ("bedrock/claude-haiku", "bedrock", "anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5 on Bedrock", 1.00, 5.00),
    ("bedrock/claude-sonnet", "bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0", "Claude Sonnet 4.5 on Bedrock", 3.00, 15.00),
)

EMBEDDINGS = (
    ("openai/text-embedding-3-small", "openai", "text-embedding-3-small", "Embedding small", 0.02, 0.0),
    ("openai/text-embedding-3-large", "openai", "text-embedding-3-large", "Embedding large", 0.13, 0.0),
    ("gemini/text-embedding-004", "gemini", "text-embedding-004", "Gemini embedding", 0.15, 0.0),
)

# route id, display name, provider, catalog model id
ROUTES = (
    ("general-fast", "General fast", "openai", "openai/gpt-4o-mini"),
    ("openai-chat", "OpenAI chat", "openai", "openai/gpt-4.1"),
    ("anthropic-fast", "Anthropic fast", "anthropic", "anthropic/claude-haiku"),
    ("anthropic-chat", "Anthropic chat", "anthropic", "anthropic/claude-sonnet"),
    ("gemini-fast", "Gemini fast", "gemini", "gemini/flash"),
    ("gemini-chat", "Gemini chat", "gemini", "gemini/pro"),
    ("bedrock-fast", "Bedrock fast", "bedrock", "bedrock/claude-haiku"),
    ("bedrock-chat", "Bedrock chat", "bedrock", "bedrock/claude-sonnet"),
    ("embed-small", "Embedding small", "openai", "openai/text-embedding-3-small"),
    ("embed-large", "Embedding large", "openai", "openai/text-embedding-3-large"),
    ("gemini-embed", "Gemini embedding", "gemini", "gemini/text-embedding-004"),
)

SAMPLES = (
    (
        "support-tone",
        "You are a support agent. Answer in plain sentences. If you do not know, say so. Do not invent account data.",
    ),
    (
        "strict-json",
        "Reply with one JSON object and no markdown. Use only the keys the user asked for.",
    ),
    (
        "brief-summary",
        "Summarize the user message in three short bullets. Keep names, numbers, and dates.",
    ),
)

PROVIDER_ENV = (
    ("openai", "openai", "OPENAI_API_KEY", None, None),
    ("anthropic", "anthropic", "ANTHROPIC_API_KEY", None, None),
    ("gemini", "gemini", "GEMINI_API_KEY", None, None),
    ("azure", "azure-openai", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", None),
    ("bedrock", "bedrock", "BEDROCK_API_KEY", None, "us-east-1"),
)


def seed(db: Session) -> None:
    ensure_directory(db)
    existing = {rule.id for rule in db.scalars(select(GuardrailRule)).all()}
    for rule_id, name, kind, phase, config in RULES:
        if rule_id not in existing:
            db.add(
                GuardrailRule(
                    id=rule_id,
                    name=name,
                    kind=kind,
                    phase=phase,
                    enabled=1,
                    config_json=json.dumps(config),
                )
            )
    if db.get(Setting, "requests_per_minute") is None:
        db.add(Setting(key="requests_per_minute", value="60"))
    _seed_catalog(db)
    for tenant in db.scalars(select(Tenant)).all():
        ensure_workspace_samples(db, tenant.id)
    db.commit()


def needs_setup(db: Session) -> bool:
    count = db.scalar(select(func.count()).select_from(ApiKey))
    return count == 0


def create_admin(db: Session, application_name: str) -> str:
    name = application_name.strip()
    if not name:
        raise ValueError("Name the application.")
    plaintext = generate_key()
    now = datetime.now(UTC).isoformat()
    ensure_directory(db)
    ensure_application(db, DEFAULT_TENANT, name, name)
    if db.get(User, ADMIN_USER) is None:
        db.add(
            User(
                id=ADMIN_USER,
                tenant_id=DEFAULT_TENANT,
                application_id=name,
                email="admin@localhost",
                display_name="Local admin",
                role="admin",
                status="active",
                created_at=now,
            )
        )
    ensure_user_budget(db, ADMIN_USER, DEFAULT_TENANT)
    db.add(
        ApiKey(
            id=f"key_{uuid.uuid4().hex[:12]}",
            prefix=plaintext[:16],
            key_hash=hash_key(plaintext),
            tenant_id=DEFAULT_TENANT,
            application_id=name,
            user_id=ADMIN_USER,
            role="admin",
            status="active",
            created_at=now,
        )
    )
    if db.get(Budget, DEFAULT_TENANT) is None:
        db.add(Budget(tenant_id=DEFAULT_TENANT, monthly_usd=25, spent_usd=0))
    _import_providers(db)
    db.commit()
    return plaintext


def _import_providers(db: Session) -> None:
    imported = False
    for provider_id, kind, env_name, url_env, region in PROVIDER_ENV:
        secret = os.environ.get(env_name, "").strip()
        if not secret or db.get(Provider, provider_id) is not None:
            continue
        base_url = os.environ.get(url_env, "").strip() if url_env else None
        db.add(
            Provider(
                id=provider_id,
                type=kind,
                base_url=base_url or None,
                region=region,
                api_version="2024-10-21" if kind == "azure-openai" else None,
                secret_ciphertext=encrypt_secret(secret),
                enabled=1,
            )
        )
        imported = True
    if imported:
        db.flush()
    _seed_catalog(db)


def _seed_catalog(db: Session) -> None:
    providers = {row.id for row in db.scalars(select(Provider)).all()}
    for model_id, provider_id, provider_model, display_name, input_price, output_price in CATALOG:
        _add_model(db, providers, model_id, provider_id, provider_model, display_name, input_price, output_price, "chat")
    for model_id, provider_id, provider_model, display_name, input_price, output_price in EMBEDDINGS:
        _add_model(db, providers, model_id, provider_id, provider_model, display_name, input_price, output_price, "embedding")
    db.flush()
    for route_id, display_name, provider_id, model_id in ROUTES:
        if provider_id not in providers or db.get(CatalogModel, model_id) is None:
            continue
        if db.get(VirtualModel, route_id) is not None:
            continue
        db.add(
            VirtualModel(
                id=route_id,
                display_name=display_name,
                provider_id=provider_id,
                model_id=model_id,
            )
        )


def _add_model(
    db: Session,
    providers: set[str],
    model_id: str,
    provider_id: str,
    provider_model: str,
    display_name: str,
    input_price: float,
    output_price: float,
    kind: str,
) -> None:
    if provider_id not in providers or db.get(CatalogModel, model_id) is not None:
        return
    db.add(
        CatalogModel(
            id=model_id,
            provider_id=provider_id,
            provider_model=provider_model,
            display_name=display_name,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            kind=kind,
        )
    )


def ensure_workspace_samples(db: Session, tenant_id: str) -> None:
    _ensure_prompts(db, tenant_id)


def _ensure_prompts(db: Session, tenant_id: str) -> None:
    created_at = datetime.now(UTC).isoformat()
    for name, body in SAMPLES:
        found = db.scalar(select(Prompt.id).where(Prompt.tenant_id == tenant_id, Prompt.name == name).limit(1))
        if found is not None:
            continue
        db.add(
            Prompt(
                id=f"prompt_{uuid.uuid4().hex[:12]}",
                tenant_id=tenant_id,
                name=name,
                version=1,
                body=body,
                created_at=created_at,
            )
        )

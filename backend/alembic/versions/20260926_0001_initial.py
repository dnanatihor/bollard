"""Gateway schema for a first-time database.

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("api_version", sa.String(length=64), nullable=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(length=256), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("provider_model", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("input_price_per_million", sa.Float(), nullable=False),
        sa.Column("output_price_per_million", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "virtual_models",
        sa.Column("id", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=256), nullable=False),
        sa.Column("fallback_model_id", sa.String(length=256), nullable=True),
        sa.Column("strategy", sa.String(length=32), nullable=False, server_default="direct"),
        sa.Column("targets_json", sa.Text(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "guardrail_rules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("route_id", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "budgets",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("monthly_usd", sa.Float(), nullable=False),
        sa.Column("overall_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_subject", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_table(
        "user_budgets",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("monthly_usd", sa.Float(), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=True),
        sa.Column("last_used_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("requested_model", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("provider_model", sa.String(length=256), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("timestamp", sa.String(length=40), nullable=False),
        sa.Column("prev_hash", sa.String(length=128), nullable=False),
        sa.Column("hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("sent_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("response_text", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_table(
        "spans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "guardrail_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("rule", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("headers_ciphertext", sa.Text(), nullable=True),
        sa.Column("authorize_url", sa.String(length=512), nullable=True),
        sa.Column("token_url", sa.String(length=512), nullable=True),
        sa.Column("oauth_client_id", sa.String(length=256), nullable=True),
        sa.Column("oauth_client_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("oauth_scopes", sa.String(length=512), nullable=True),
        sa.Column("refresh_ciphertext", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.String(length=40), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mcp_grants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("server_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tools_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "user_id", name="uq_mcp_grants_server_user"),
    )
    op.create_table(
        "prompts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "semantic_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("vector_json", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_tenant_email", "users", ["tenant_id", "email"])
    op.create_index("ix_keys_tenant_created", "api_keys", ["tenant_id", "created_at"])
    op.create_index("ix_traces_tenant_started", "traces", ["tenant_id", "started_at"])
    op.create_index("ix_audit_tenant_id", "audit_events", ["tenant_id", "id"])
    op.create_index("ix_usage_tenant_created", "usage_events", ["tenant_id", "created_at"])
    op.create_index("ix_usage_user_created", "usage_events", ["user_id", "created_at"])
    op.create_index("ix_prompts_tenant_name", "prompts", ["tenant_id", "name", "version"])


def downgrade() -> None:
    op.drop_index("ix_prompts_tenant_name", table_name="prompts")
    op.drop_index("ix_usage_user_created", table_name="usage_events")
    op.drop_index("ix_usage_tenant_created", table_name="usage_events")
    op.drop_index("ix_audit_tenant_id", table_name="audit_events")
    op.drop_index("ix_traces_tenant_started", table_name="traces")
    op.drop_index("ix_keys_tenant_created", table_name="api_keys")
    op.drop_index("ix_users_tenant_email", table_name="users")
    op.drop_table("semantic_entries")
    op.drop_table("login_sessions")
    op.drop_table("prompts")
    op.drop_table("mcp_grants")
    op.drop_table("mcp_servers")
    op.drop_table("guardrail_hits")
    op.drop_table("spans")
    op.drop_table("traces")
    op.drop_table("audit_events")
    op.drop_table("usage_events")
    op.drop_table("api_keys")
    op.drop_table("user_budgets")
    op.drop_table("users")
    op.drop_table("settings")
    op.drop_table("budgets")
    op.drop_table("guardrail_rules")
    op.drop_table("virtual_models")
    op.drop_table("models")
    op.drop_table("providers")
    op.drop_table("applications")
    op.drop_table("tenants")

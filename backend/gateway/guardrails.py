import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from gateway.models import GuardrailRule

SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9]{8,})|(aigw_live_[A-Za-z0-9_-]{8,})|(AKIA[0-9A-Z]{16})|"
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)|(gh[pousr]_[A-Za-z0-9]{20,})"
)
PII_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\b\d{3}-\d{2}-\d{4}\b", re.IGNORECASE)
INJECTION_PATTERN = re.compile(r"ignore (all|any|previous) instructions", re.IGNORECASE)


@dataclass
class Decision:
    rule: str
    action: str
    reason: str
    content: str


class GuardrailDenied(Exception):
    def __init__(self, rule: str, reason: str, phase: str) -> None:
        super().__init__(reason)
        self.rule = rule
        self.reason = reason
        self.phase = phase


def evaluate(db: Session, phase: str, content: str, route_id: str | None = None) -> tuple[str, list[Decision]]:
    rules = db.scalars(select_rules(phase)).all()
    decisions: list[Decision] = []
    current = content
    for rule in rules:
        if rule.enabled != 1:
            continue
        scope = rule.route_id or ""
        if scope and scope != (route_id or ""):
            continue
        decision = _apply(rule, phase, current)
        if decision is None:
            continue
        decisions.append(decision)
        if decision.action == "deny":
            raise GuardrailDenied(rule.name, decision.reason, phase)
        if decision.action == "redact":
            current = decision.content
    return current, decisions


def output_rules_active(db: Session, route_id: str | None) -> bool:
    rules = db.scalars(select_rules("output")).all()
    for rule in rules:
        scope = rule.route_id or ""
        if scope and scope != (route_id or ""):
            continue
        return True
    return False


def select_rules(phase: str):
    from sqlalchemy import or_, select

    return select(GuardrailRule).where(
        GuardrailRule.enabled == 1,
        or_(GuardrailRule.phase == phase, GuardrailRule.phase == "both"),
    )


def _apply(rule: GuardrailRule, phase: str, content: str) -> Decision | None:
    config = json.loads(rule.config_json or "{}")
    if rule.kind == "prompt-injection" and phase == "input":
        if INJECTION_PATTERN.search(content):
            return Decision(rule.name, "deny", "Prompt injection pattern detected.", content)
    elif rule.kind == "toxicity" and phase == "input":
        terms = [term.lower() for term in config.get("terms", ["kill yourself"])]
        lower = content.lower()
        if any(term and term in lower for term in terms):
            return Decision(rule.name, "deny", "Toxic content was blocked.", content)
    elif rule.kind == "pii":
        redacted = PII_PATTERN.sub("[pii]", content)
        if redacted != content:
            return Decision(rule.name, "redact", "PII redacted.", redacted)
    elif rule.kind == "secret":
        redacted = SECRET_PATTERN.sub("[secret]", content)
        if redacted != content:
            return Decision(rule.name, "redact", "Secret material was redacted.", redacted)
    elif rule.kind == "max-input" and phase == "input":
        limit = int(config.get("maxChars", 100_000))
        if len(content) > limit:
            return Decision(rule.name, "deny", f"Input exceeds {limit} characters.", content)
    elif rule.kind == "blocked-words" and phase == "input":
        words = [word for word in config.get("words", []) if isinstance(word, str) and word.strip()]
        lower = content.lower()
        hit = next((word for word in words if word.lower() in lower), None)
        if hit is not None:
            return Decision(rule.name, "deny", f'Blocked term "{hit}" was detected.', content)
    return None

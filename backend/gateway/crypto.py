import os

from cryptography.fernet import Fernet

from gateway.settings import DATA_DIR


def _fernet() -> Fernet:
    configured = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("utf-8"))
        except ValueError as exc:
            raise RuntimeError("GATEWAY_MASTER_KEY must be a Fernet key, not a password.") from exc
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "master.key"
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return Fernet(path.read_bytes())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")

import os
import tempfile

_test_database = os.environ.get("GATEWAY_TEST_DATABASE_URL", "").strip()
if _test_database:
    os.environ["GATEWAY_DATABASE_URL"] = _test_database
else:
    _fd, _name = tempfile.mkstemp(prefix="gateway-test-", suffix=".db")
    os.close(_fd)
    os.environ["GATEWAY_DATABASE_URL"] = f"sqlite:///{_name}"
os.environ["REDIS_URL"] = ""
for _name in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "BEDROCK_API_KEY",
):
    os.environ[_name] = ""

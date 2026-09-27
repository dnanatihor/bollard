import asyncio
import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from gateway.models import Provider


class ProviderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    tool_calls: list[dict[str, object]] | None = None
    finish_reason: str = "stop"


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def message_text(messages: list[dict[str, object]]) -> str:
    return "\n".join(_text(message.get("content")) for message in messages)


async def complete(
    provider: Provider,
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_choice: object | None = None,
) -> Completion:
    kind = provider.type
    if kind in {"openai", "openai-compatible"}:
        return await _openai(provider, secret, provider_model, messages, temperature, max_tokens, tools, tool_choice)
    if kind == "azure-openai":
        return await _azure(provider, secret, provider_model, messages, temperature, max_tokens, tools, tool_choice)
    if kind == "anthropic":
        return await _anthropic(secret, provider_model, messages, max_tokens, tools)
    if kind == "gemini":
        return await _gemini(secret, provider_model, messages, tools)
    if kind == "bedrock":
        return await _bedrock(provider, secret, provider_model, messages, max_tokens, tools)
    raise ProviderError(400, f"Provider type {kind} is not supported.")


async def _post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    content: bytes | None = None,
) -> dict[str, object]:
    last_error: ProviderError | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                if content is None:
                    response = await client.post(url, headers=headers, json=payload)
                else:
                    response = await client.post(url, headers=headers, content=content)
        except httpx.TimeoutException as exc:
            last_error = ProviderError(504, "Provider timed out.")
            if attempt < 2:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise last_error from exc
        except httpx.HTTPError as exc:
            last_error = ProviderError(502, "Provider connection failed.")
            if attempt < 2:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise last_error from exc
        if response.status_code in {429, 502, 503, 504} and attempt < 2:
            await asyncio.sleep(0.2 * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise ProviderError(response.status_code, f"Provider returned HTTP {response.status_code}.")
        body = response.json()
        if not isinstance(body, dict):
            raise ProviderError(502, "Provider returned an unexpected payload.")
        return body
    raise last_error or ProviderError(502, "Provider request failed.")


async def _openai(
    provider: Provider,
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_choice: object | None = None,
) -> Completion:
    base = provider.base_url or "https://api.openai.com/v1"
    payload = _openai_payload(provider_model, messages, temperature, max_tokens, tools, tool_choice)
    body = await _post(
        f"{base.rstrip('/')}/chat/completions",
        {"authorization": f"Bearer {secret}", "content-type": "application/json"},
        payload,
    )
    return _completion_from_openai(body)


async def _azure(
    provider: Provider,
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_choice: object | None = None,
) -> Completion:
    if not provider.base_url:
        raise ProviderError(400, "Azure provider requires a base URL.")
    version = provider.api_version or "2024-10-21"
    payload = _openai_payload(provider_model, messages, temperature, max_tokens, tools, tool_choice)
    payload.pop("model", None)
    url = (
        f"{provider.base_url.rstrip('/')}/openai/deployments/{quote(provider_model, safe='')}"
        f"/chat/completions?api-version={version}"
    )
    body = await _post(url, {"api-key": secret, "content-type": "application/json"}, payload)
    return _completion_from_openai(body)


async def _anthropic(
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
) -> Completion:
    system, converted = _anthropic_messages(messages)
    payload: dict[str, object] = {
        "model": provider_model,
        "max_tokens": max_tokens or 1024,
        "messages": converted,
    }
    if system:
        payload["system"] = "\n".join(system)
    anthropic_tools = _anthropic_tools(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
    body = await _post(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": secret,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload,
    )
    texts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    content = body.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                texts.append(item["text"])
            if item.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": str(item.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(item.get("name") or ""),
                            "arguments": json.dumps(item.get("input") or {}),
                        },
                    }
                )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return Completion(
        text="\n".join(texts),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        tool_calls=tool_calls or None,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


async def _gemini(
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None = None,
) -> Completion:
    system: list[str] = []
    contents: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        text = _text(message.get("content"))
        if role == "system":
            system.append(text)
        else:
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
            )
    payload: dict[str, object] = {"contents": contents}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
    declarations = _function_declarations(tools)
    if declarations:
        payload["tools"] = [{"functionDeclarations": declarations}]
    body = await _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(provider_model, safe='')}:generateContent",
        {"x-goog-api-key": secret, "content-type": "application/json"},
        payload,
    )
    texts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            for index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                call = part.get("functionCall")
                if isinstance(call, dict):
                    tool_calls.append(
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": str(call.get("name") or ""),
                                "arguments": json.dumps(call.get("args") or {}),
                            },
                        }
                    )
    usage = body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {}
    return Completion(
        text="\n".join(texts),
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        tool_calls=tool_calls or None,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


async def _bedrock(
    provider: Provider,
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
) -> Completion:
    region = provider.region or "us-east-1"
    system: list[dict[str, str]] = []
    converted: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        text = _text(message.get("content"))
        if role == "system":
            system.append({"text": text})
        else:
            converted.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": [{"text": text}],
                }
            )
    payload: dict[str, object] = {
        "messages": converted,
        "inferenceConfig": {"maxTokens": max_tokens or 1024},
    }
    if system:
        payload["system"] = system
    bedrock_tools = _bedrock_tools(tools)
    if bedrock_tools:
        payload["toolConfig"] = {"tools": bedrock_tools}
    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(provider_model, safe='')}/converse"
    signed = _bedrock_headers(url, payload, region, secret)
    raw = json.dumps(payload).encode()
    body = await _post(url, signed, payload, content=raw if signed.get("authorization", "").startswith("AWS4") else None)
    texts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    output = body.get("output")
    if isinstance(output, dict) and isinstance(output.get("message"), dict):
        content = output["message"].get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    texts.append(item["text"])
                use = item.get("toolUse")
                if isinstance(use, dict):
                    tool_calls.append(
                        {
                            "id": str(use.get("toolUseId") or ""),
                            "type": "function",
                            "function": {
                                "name": str(use.get("name") or ""),
                                "arguments": json.dumps(use.get("input") or {}),
                            },
                        }
                    )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return Completion(
        text="\n".join(texts),
        input_tokens=int(usage.get("inputTokens") or 0),
        output_tokens=int(usage.get("outputTokens") or 0),
        tool_calls=tool_calls or None,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def safe_provider_error(error: ProviderError) -> str:
    return json.dumps({"status": error.status})


def _openai_payload(
    provider_model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None,
    tool_choice: object | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"model": provider_model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return payload


def _completion_from_openai(body: dict[str, object]) -> Completion:
    text = ""
    tool_calls: list[dict[str, object]] = []
    finish = "stop"
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        if isinstance(choice.get("finish_reason"), str):
            finish = choice["finish_reason"]
        message = choice.get("message")
        if isinstance(message, dict):
            text = _text(message.get("content"))
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                tool_calls = [call for call in calls if isinstance(call, dict)]
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return Completion(
        text=text,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        tool_calls=tool_calls or None,
        finish_reason="tool_calls" if tool_calls else finish,
    )


def _function_declarations(tools: list[dict[str, object]] | None) -> list[dict[str, object]]:
    declarations: list[dict[str, object]] = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if not isinstance(function, dict) or not function.get("name"):
            continue
        declarations.append(
            {
                "name": function.get("name"),
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return declarations


def _anthropic_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for item in _function_declarations(tools):
        converted.append(
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["parameters"],
            }
        )
    return converted


def _anthropic_messages(messages: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]]]:
    system: list[str] = []
    converted: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            system.append(_text(message.get("content")))
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": _text(message.get("content")),
                        }
                    ],
                }
            )
            continue
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, list):
            blocks: list[dict[str, object]] = []
            text = _text(message.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                arguments = function.get("arguments") or "{}"
                if isinstance(arguments, str):
                    try:
                        parsed = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed = {}
                else:
                    parsed = arguments
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "input": parsed if isinstance(parsed, dict) else {},
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": "assistant" if role == "assistant" else "user", "content": _text(message.get("content"))})
    return system, converted


def _bedrock_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for item in _function_declarations(tools):
        specs.append(
            {
                "toolSpec": {
                    "name": item["name"],
                    "description": item["description"],
                    "inputSchema": {"json": item["parameters"]},
                }
            }
        )
    return specs


def _bedrock_headers(url: str, payload: dict[str, object], region: str, secret: str) -> dict[str, str]:
    stripped = secret.strip()
    if not stripped.startswith("{"):
        return {"authorization": f"Bearer {secret}", "content-type": "application/json"}
    try:
        creds = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProviderError(400, "Bedrock credentials must be a bearer token or a JSON access key.") from exc
    access = str(creds.get("accessKeyId") or "")
    key = str(creds.get("secretAccessKey") or "")
    if not access or not key:
        raise ProviderError(400, "Bedrock JSON credentials need accessKeyId and secretAccessKey.")
    from gateway.aws_sig import sign

    token = creds.get("sessionToken")
    return sign(
        method="POST",
        url=url,
        body=json.dumps(payload).encode(),
        region=region,
        access_key=access,
        secret_key=key,
        session_token=str(token) if token else None,
    )


async def embed(
    provider: Provider,
    secret: str,
    provider_model: str,
    inputs: list[str],
) -> tuple[list[list[float]], int]:
    kind = provider.type
    if kind in {"openai", "openai-compatible"}:
        base = provider.base_url or "https://api.openai.com/v1"
        body = await _post(
            f"{base.rstrip('/')}/embeddings",
            {"authorization": f"Bearer {secret}", "content-type": "application/json"},
            {"model": provider_model, "input": inputs},
        )
        data = body.get("data")
        vectors: list[list[float]] = []
        if isinstance(data, list):
            ordered = sorted(
                [item for item in data if isinstance(item, dict)],
                key=lambda item: int(item.get("index") or 0),
            )
            for item in ordered:
                embedding = item.get("embedding")
                if isinstance(embedding, list):
                    vectors.append([float(value) for value in embedding])
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return vectors, int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
    if kind == "azure-openai":
        if not provider.base_url:
            raise ProviderError(400, "Azure provider requires a base URL.")
        version = provider.api_version or "2024-10-21"
        body = await _post(
            f"{provider.base_url.rstrip('/')}/openai/deployments/{quote(provider_model, safe='')}/embeddings?api-version={version}",
            {"api-key": secret, "content-type": "application/json"},
            {"input": inputs},
        )
        data = body.get("data")
        vectors = []
        if isinstance(data, list):
            ordered = sorted(
                [item for item in data if isinstance(item, dict)],
                key=lambda item: int(item.get("index") or 0),
            )
            for item in ordered:
                embedding = item.get("embedding")
                if isinstance(embedding, list):
                    vectors.append([float(value) for value in embedding])
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return vectors, int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
    if kind == "gemini":
        vectors = []
        tokens = 0
        for text in inputs:
            body = await _post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote(provider_model, safe='')}:embedContent",
                {"x-goog-api-key": secret, "content-type": "application/json"},
                {"content": {"parts": [{"text": text}]}},
            )
            embedding = body.get("embedding")
            values = embedding.get("values") if isinstance(embedding, dict) else None
            if isinstance(values, list):
                vectors.append([float(value) for value in values])
        return vectors, tokens
    raise ProviderError(400, "This provider does not serve embeddings.")


async def rerank(
    provider: Provider,
    secret: str,
    provider_model: str,
    query: str,
    documents: list[str],
) -> dict[str, object]:
    if provider.type not in {"openai", "openai-compatible"}:
        raise ProviderError(400, "Rerank uses an OpenAI-compatible base URL, such as a Cohere-compatible endpoint.")
    base = (provider.base_url or "https://api.openai.com/v1").rstrip("/")
    body = await _post(
        f"{base}/rerank",
        {"authorization": f"Bearer {secret}", "content-type": "application/json"},
        {"model": provider_model, "query": query, "documents": documents},
    )
    return body


def lexical_rerank(query: str, documents: list[str]) -> dict[str, object]:
    query_tokens = set(query.lower().split())
    ranked: list[dict[str, object]] = []
    for index, document in enumerate(documents):
        tokens = set(document.lower().split())
        score = len(query_tokens & tokens) / max(len(query_tokens), 1)
        ranked.append({"index": index, "relevance_score": round(score, 4)})
    ranked.sort(key=lambda item: float(item["relevance_score"]), reverse=True)
    return {"object": "list", "results": ranked}


async def iter_openai_chunks(
    provider: Provider,
    secret: str,
    provider_model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_choice: object | None = None,
):
    if provider.type not in {"openai", "openai-compatible", "azure-openai"}:
        raise ProviderError(400, "Live token streaming is available for OpenAI-compatible providers.")
    payload = _openai_payload(provider_model, messages, temperature, max_tokens, tools, tool_choice)
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}
    if provider.type == "azure-openai":
        if not provider.base_url:
            raise ProviderError(400, "Azure provider requires a base URL.")
        version = provider.api_version or "2024-10-21"
        payload.pop("model", None)
        url = (
            f"{provider.base_url.rstrip('/')}/openai/deployments/{quote(provider_model, safe='')}"
            f"/chat/completions?api-version={version}"
        )
        headers = {"api-key": secret, "content-type": "application/json"}
    else:
        base = provider.base_url or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/chat/completions"
        headers = {"authorization": f"Bearer {secret}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                raise ProviderError(response.status_code, f"Provider returned HTTP {response.status_code}.")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict):
                    yield chunk


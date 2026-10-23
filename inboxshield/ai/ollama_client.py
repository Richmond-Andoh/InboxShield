"""Minimal client for the local Ollama HTTP API.

Only ``http://localhost`` style endpoints are used by default.  The client never
falls back to a cloud provider and does not accept API keys.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from inboxshield.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from inboxshield.config import Settings
from inboxshield.schemas import EXTRACTION_JSON_SCHEMA
from inboxshield.utils.logging_config import sanitize_error

logger = logging.getLogger(__name__)

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class OllamaError(RuntimeError):
    """Base error for local inference failures."""

    retryable = False

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class OllamaUnavailableError(OllamaError):
    retryable = True


class OllamaModelMissingError(OllamaError):
    retryable = False


class OllamaResponseError(OllamaError):
    retryable = True


class OllamaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.model
        self.timeout = timeout or settings.ollama_timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            transport=self._transport,
            trust_env=False,
            headers={"User-Agent": "InboxShield-AI/1.0"},
        )

    # ------------------------------------------------------------------ health
    def is_available(self, timeout: float = 3.0) -> bool:
        try:
            with self._client() as client:
                response = client.get("/api/tags", timeout=timeout)
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self, timeout: float = 5.0) -> list[str]:
        try:
            with self._client() as client:
                response = client.get("/api/tags", timeout=timeout)
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is 'ollama serve' running? "
                f"({sanitize_error(exc, 150)})"
            ) from exc
        if response.status_code != 200:
            raise OllamaUnavailableError(
                f"Ollama returned HTTP {response.status_code} for /api/tags"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OllamaResponseError("Ollama /api/tags returned invalid JSON") from exc
        return [str(model.get("name", "")) for model in payload.get("models", [])]

    def model_status(self) -> dict[str, Any]:
        """Return availability/capability details without loading model weights."""
        try:
            models = self.list_models()
        except OllamaError as exc:
            return {
                "available": False,
                "models": [],
                "model_present": False,
                "capabilities": [],
                "error": str(exc),
            }
        if ":" in self.model:
            present = self.model in models
        else:
            present = any(
                name.split(":")[0] == self.model for name in models
            )
        capabilities: list[str] = []
        if present:
            try:
                with self._client() as client:
                    response = client.post("/api/show", json={"model": self.model})
                if response.status_code == 200:
                    capabilities = list(response.json().get("capabilities", []))
            except (httpx.HTTPError, ValueError):
                pass
        return {
            "available": True,
            "models": models,
            "model_present": present,
            "capabilities": capabilities,
            "error": None,
        }

    # --------------------------------------------------------------- inference
    def extract_json(self, image_bytes: bytes, page_number: int, total_pages: int) -> dict[str, Any]:
        if not image_bytes:
            raise OllamaResponseError("No image data was supplied for inference")
        payload = {
            "model": self.model,
            "stream": False,
            "format": EXTRACTION_JSON_SCHEMA,
            "options": {"temperature": 0.0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(page_number, total_pages),
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                },
            ],
        }
        try:
            with self._client() as client:
                response = client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Start it with 'ollama serve'."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaResponseError(
                f"Ollama inference timed out after {self.timeout:.0f}s. "
                "The model may be loading or the document may be too large.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(
                f"Ollama request failed: {sanitize_error(exc, 200)}"
            ) from exc

        if response.status_code == 404:
            raise OllamaModelMissingError(
                f"Model '{self.model}' is not available in Ollama. "
                f"Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise OllamaResponseError(
                f"Ollama returned HTTP {response.status_code}: "
                f"{sanitize_error(response.text, 200)}",
                retryable=response.status_code >= 500,
            )

        try:
            data = response.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaResponseError(
                "Ollama returned a malformed chat response"
            ) from exc

        return _parse_json_content(str(content))

    def unload_model(self) -> None:
        """Best-effort unload to release memory (used by scripts/tests)."""
        try:
            with self._client() as client:
                client.post(
                    "/api/chat",
                    json={"model": self.model, "messages": [], "keep_alive": 0},
                    timeout=10.0,
                )
        except httpx.HTTPError:
            pass


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    text = _CODE_FENCE.sub("", text).strip()
    if not text:
        raise OllamaResponseError("Ollama returned an empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise OllamaResponseError(
                f"Ollama did not return JSON: {sanitize_error(text, 200)}"
            ) from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(
                f"Ollama returned invalid JSON: {sanitize_error(str(exc), 200)}"
            ) from exc
    if not isinstance(parsed, dict):
        raise OllamaResponseError("Ollama JSON response was not an object")
    return parsed

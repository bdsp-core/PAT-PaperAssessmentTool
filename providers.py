"""
Model-agnostic LLM provider abstraction for PAT.

Two concrete providers are supported:

* :class:`OllamaProvider` runs local inference via the Ollama daemon.
* :class:`AnthropicProvider` talks to the Anthropic Claude API and supports
  prompt caching so the paper text can be reused cheaply across agents.

Any Ollama model works, including multimodal families (``qwen3.5``, ``llava``,
``llama3.2-vision``, ...).  The figure-analysis agents gracefully skip when
:meth:`LLMProvider.supports_vision` returns ``False``.
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from pathlib import Path


# ---------------------------------------------------------------------------
# Defaults and shared configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOKENS = 2048

# Character-to-token ratio used for Ollama context-window sizing.
_CHARS_PER_TOKEN = 4

# Headroom above the input to reserve for reasoning + reply tokens.
_OUTPUT_TOKEN_HEADROOM = 4096

# Minimum Ollama context window used for short prompts.
_MIN_OLLAMA_CTX = 8192

# Ollama model families known to accept image inputs.
_VISION_FAMILIES = ("clip", "llava", "vision", "mllama")


# ---------------------------------------------------------------------------
# Emoji sanitation (local models occasionally hallucinate dingbats)
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001ffff"   # Misc Symbols, Emoticons, Dingbats
    "\U00002600-\U000027bf"   # Misc Symbols
    "\U00002b50-\U00002b55"   # Stars
    "\U0000fe00-\U0000fe0f"   # Variation selectors
    "\U0000200d"              # Zero-width joiner
    "\U000023e9-\U000023fa"   # Transport / map symbols
    "\U00002702-\U000027b0"   # Dingbats
    "\U0000e000-\U0000f8ff"   # Private-use area
    "]+",
)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_emojis(text: str) -> str:
    """Remove emoji / dingbat characters from LLM output."""
    return _EMOJI_RE.sub("", text)


def _strip_think(text: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks from the final text."""
    return _THINK_BLOCK_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base class for every LLM backend."""

    _cacheable_context: str | None = None

    def set_cacheable_context(self, text: str) -> None:
        """Mark ``text`` as shared across calls so providers can cache it.

        Providers with prompt-caching support (currently Anthropic) reuse this
        block across agents, cutting cost on multi-agent runs.
        """
        self._cacheable_context = text

    @abstractmethod
    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        """Single call. Returns the full reply.

        When ``on_chunk`` is provided, the provider streams internally and
        invokes ``on_chunk(text_chunk)`` for every piece before returning the
        full concatenated text.
        """

    @abstractmethod
    def call_stream(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        """Streaming call. Yields successive text chunks."""

    @abstractmethod
    def call_with_images(
        self,
        system: str,
        user: str,
        images: list[str],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        """Call with attached image inputs for multimodal models."""

    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this provider / model combination accepts images."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the underlying model identifier."""


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Local inference via the Ollama daemon."""

    def __init__(
        self,
        model: str = "qwen3.5:27b-bf16",
        host: str | None = None,
    ) -> None:
        import ollama
        self._model = model
        self._client = ollama.Client(host=host) if host else ollama

    @staticmethod
    def _estimate_ctx(system: str, user: str) -> int:
        """Round the needed context window up to the next power of two.

        Ollama keeps the model in VRAM sized for ``num_ctx``; overshooting
        wastes memory while undershooting silently truncates the prompt.
        Rounding up to a power of two balances both.
        """
        input_tokens = (len(system) + len(user)) // _CHARS_PER_TOKEN
        needed = input_tokens + _OUTPUT_TOKEN_HEADROOM
        power = _MIN_OLLAMA_CTX
        while power < needed:
            power *= 2
        return power

    def _options(self, system: str, user: str) -> dict:
        """Build per-call Ollama options.

        ``num_predict`` is intentionally omitted so reasoning-capable models
        can use the headroom for ``<think>`` tokens without starving the
        final answer.
        """
        return {
            "think": True,
            "num_ctx": self._estimate_ctx(system, user),
        }

    # --- Plain text --------------------------------------------------------

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        if on_chunk:
            chunks: list[str] = []
            for chunk in self.call_stream(system, user, max_tokens):
                chunks.append(chunk)
                on_chunk(chunk)
            return _strip_emojis(_strip_think("".join(chunks)))

        resp = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options=self._options(system, user),
        )
        return _strip_emojis(_strip_think(resp["message"]["content"]))

    def call_stream(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        in_think = False
        for chunk in self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options=self._options(system, user),
            stream=True,
        ):
            msg = chunk["message"]
            think_text = getattr(msg, "thinking", "") or ""
            content_text = getattr(msg, "content", "") or ""

            if think_text:
                if not in_think:
                    yield "<think>"
                    in_think = True
                yield _strip_emojis(think_text)

            if content_text:
                if in_think:
                    yield "</think>"
                    in_think = False
                yield _strip_emojis(content_text)
        if in_think:
            yield "</think>"

    # --- Multimodal --------------------------------------------------------

    def call_with_images(
        self,
        system: str,
        user: str,
        images: list[str],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": images},
        ]

        if on_chunk:
            chunks: list[str] = []
            in_think = False
            for chunk in self._client.chat(
                model=self._model, messages=messages,
                options=self._options(system, user), stream=True,
            ):
                msg = chunk["message"]
                think_text = getattr(msg, "thinking", "") or ""
                content_text = getattr(msg, "content", "") or ""
                if think_text:
                    if not in_think:
                        on_chunk("<think>")
                        in_think = True
                    on_chunk(_strip_emojis(think_text))
                    chunks.append(think_text)
                if content_text:
                    if in_think:
                        on_chunk("</think>")
                        in_think = False
                    on_chunk(_strip_emojis(content_text))
                    chunks.append(content_text)
            if in_think:
                on_chunk("</think>")
            return _strip_emojis(_strip_think("".join(chunks)))

        resp = self._client.chat(
            model=self._model, messages=messages,
            options=self._options(system, user),
        )
        return _strip_emojis(_strip_think(resp["message"]["content"]))

    # --- Capabilities ------------------------------------------------------

    def supports_vision(self) -> bool:
        # qwen3.5 is natively multimodal; short-circuit to avoid an extra RPC.
        if "qwen3.5" in self._model:
            return True
        try:
            info = self._client.show(self._model)
        except Exception:
            # Broad catch: vision capability is advisory - a failure here
            # should not crash the review, only disable figure vision.
            return False

        caps = getattr(info, "capabilities", None) or []
        if "vision" in caps:
            return True

        # Legacy Ollama clients expose capabilities only via model "families".
        if hasattr(info, "details"):
            details = info.details
            families = getattr(details, "families", None) or []
        else:
            details = info.get("details", {})
            families = details.get("families", []) if isinstance(details, dict) else []
        return any(f in families for f in _VISION_FAMILIES)

    @property
    def model_name(self) -> str:
        return self._model

    def unload(self) -> None:
        """Ask Ollama to release the model from VRAM so another can load."""
        try:
            self._client.generate(model=self._model, prompt="", keep_alive=0)
        except Exception:
            # Unload is best-effort; a failure simply leaves the model loaded.
            pass

    @staticmethod
    def list_models(host: str | None = None) -> list[str]:
        """Return the list of model names the local Ollama daemon knows about."""
        import ollama
        client = ollama.Client(host=host) if host else ollama
        result = client.list()
        # The Ollama client returns either a list-backed object or a dict.
        models = result.models if hasattr(result, "models") else result.get("models", [])
        return [
            getattr(m, "model", None) or m.get("name", str(m))
            for m in models
        ]

    @staticmethod
    def check_connection(host: str | None = None) -> None:
        """Raise if the Ollama daemon is not reachable."""
        import ollama
        client = ollama.Client(host=host) if host else ollama
        client.list()


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    """Cloud inference via the Anthropic Claude API.

    Supports prompt caching: call :meth:`set_cacheable_context` once with the
    paper text and that block is reused across every agent call, reducing
    cost on multi-agent reviews by roughly 50%.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
    ) -> None:
        import anthropic
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def _build_system(self, system: str) -> str | list[dict]:
        """Return the ``system=`` argument, splitting out the cacheable block.

        When :attr:`_cacheable_context` is set the system prompt becomes a
        two-block list so Anthropic can cache the shared paper text
        independently of the per-agent system prompt.
        """
        if not self._cacheable_context:
            return system
        return [
            {
                "type": "text",
                "text": f"PAPER TEXT (shared context):\n\n{self._cacheable_context}",
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": system,
            },
        ]

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        if on_chunk:
            chunks: list[str] = []
            for chunk in self.call_stream(system, user, max_tokens):
                chunks.append(chunk)
                on_chunk(chunk)
            return "".join(chunks)

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=self._build_system(system),
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text

    def call_stream(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=self._build_system(system),
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    def call_with_images(
        self,
        system: str,
        user: str,
        images: list[str],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        on_chunk=None,
    ) -> str:
        content: list[dict] = []
        for img_path in images:
            data = base64.standard_b64encode(
                Path(img_path).read_bytes()
            ).decode()
            media_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(Path(img_path).suffix.lower(), "image/png")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
        content.append({"type": "text", "text": user})

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=self._build_system(system),
            messages=[{"role": "user", "content": content}],
        )
        return resp.content[0].text

    def supports_vision(self) -> bool:
        # Every Claude 3+ model accepts images.
        return True

    @property
    def model_name(self) -> str:
        return self._model


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROVIDER_DEFAULTS: dict[str, str] = {
    "ollama": "qwen3.5:27b-bf16",
    "anthropic": "claude-sonnet-4-20250514",
}


def create_provider(
    provider: str,
    model: str | None = None,
    **kwargs,
) -> LLMProvider:
    """Create an :class:`LLMProvider` by name.

    Args:
        provider: Either ``"ollama"`` or ``"anthropic"``.
        model: Model identifier. Defaults to the entry in ``PROVIDER_DEFAULTS``.
        **kwargs: Forwarded to the provider constructor (``host=``, ``api_key=``).

    Returns:
        An initialised provider instance ready for :meth:`LLMProvider.call`.
    """
    default_model = PROVIDER_DEFAULTS.get(provider)
    if provider == "ollama":
        return OllamaProvider(model=model or default_model, **kwargs)
    if provider == "anthropic":
        return AnthropicProvider(model=model or default_model, **kwargs)
    raise ValueError(
        f"Unknown provider: {provider!r}. Choose 'ollama' or 'anthropic'."
    )

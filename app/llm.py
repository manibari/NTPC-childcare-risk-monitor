"""Environment configuration and a shared OpenAI-compatible client for local/production."""
from dataclasses import dataclass, field
import os
from typing import Mapping
from urllib.parse import urlsplit

from openai import OpenAI

PROVIDER_LABELS = {"anthropic": "Claude", "bedrock_openai": "AWS Bedrock"}

BEDROCK_BASE_URL = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    region: str = "us-west-2"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        provider = env.get("LLM_PROVIDER", "").strip()
        if provider not in {"anthropic", "bedrock_openai"}:
            raise ValueError("LLM_PROVIDER 必須設定為 anthropic 或 bedrock_openai")
        model = (env.get("LLM_MODEL") or env.get("WATCHDOG_AGENT_MODEL") or "").strip()
        if not model:
            raise ValueError("未設定 LLM_MODEL")
        region = env.get("AWS_DEFAULT_REGION") or "us-west-2"
        if region != "us-west-2":
            raise ValueError("AWS_DEFAULT_REGION 必須為 us-west-2")
        base_url = env.get("OPENAI_BASE_URL", "").strip().rstrip("/")
        if provider == "bedrock_openai":
            base_url = base_url or BEDROCK_BASE_URL
            # Legacy repository secret may already contain a Bedrock API key.
            key = env.get("OPENAI_API_KEY") or env.get("ANTHROPIC_API_KEY")
            allowed_hosts = {"bedrock-mantle.us-west-2.api.aws", "bedrock-runtime.us-west-2.amazonaws.com"}
            if urlsplit(base_url).hostname not in allowed_hosts:
                raise ValueError("Bedrock endpoint 必須位於 us-west-2")
        else:
            key = env.get("ANTHROPIC_API_KEY")
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("OPENAI_BASE_URL 必須是有效的 HTTPS API base URL")
        if not key or not key.strip():
            raise ValueError("未設定所選 LLM provider 的 API key")
        return cls(provider, model, base_url, key, region)


def create_client(config: LLMConfig, **kwargs) -> OpenAI:
    """Both providers use the same SDK, request schema, timeout and retry policy."""
    return OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=60.0, max_retries=2, **kwargs)

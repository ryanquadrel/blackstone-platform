"""
App Settings
============

Shared runtime objects for the platform.
"""

from os import getenv

from agno.models.vllm import VLLM


def default_model() -> VLLM:
    """Fresh model instance per agent — avoids shared-state footguns.

    Defaults target the Blackstone Law vLLM cluster (Qwen3.5-122B-A10B-FP8
    on spark-1). Override via VLLM_BASE_URL / VLLM_MODEL_ID for a different
    deployment. Agents that explicitly need Claude or another provider
    should construct that provider directly rather than calling this.
    """
    return VLLM(
        id=getenv("VLLM_MODEL_ID", "Qwen/Qwen3.5-122B-A10B-FP8"),
        base_url=getenv("VLLM_BASE_URL", "http://spark-1:8000/v1"),
        api_key=getenv("VLLM_API_KEY", "not-required"),
        max_tokens=int(getenv("VLLM_MAX_TOKENS", "4096")),
    )

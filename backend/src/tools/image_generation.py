# =============================================================================
# PH Agent Hub — Image Generation Tool Factory
# =============================================================================
# DALL·E / Stable Diffusion / Flux via API. Prompt → image URL.
# Generated images stored in MinIO/S3.
#
# Dependencies: httpx (already installed)
# =============================================================================

import base64
import io
import logging
import uuid
from typing import Any

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 60.0
DEFAULT_SIZE: str = "1024x1024"
DEFAULT_QUALITY: str = "standard"
DEFAULT_PRESIGNED_TTL: int = 3600

VALID_SIZES: set[str] = {"1024x1024", "1792x1024", "1024x1792", "512x512", "256x256"}

# Provider endpoints
PROVIDER_CONFIGS: dict[str, dict] = {
    "openai": {
        "url": "https://api.openai.com/v1/images/generations",
        "default_model": "dall-e-3",
        "supports_size": True,
        "supports_quality": True,
    },
    "stability": {
        "url": "https://api.stability.ai/v2beta/stable-image/generate/core",
        "default_model": "stable-diffusion-xl-1024-v1-0",
        "supports_size": False,
        "supports_quality": False,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_bucket_prefix() -> str:
    from ..core.config import settings
    return settings.MINIO_BUCKET_PREFIX


def _get_bucket(tenant_id: str) -> str:
    return f"{_get_bucket_prefix()}-{tenant_id}"


async def _upload_and_get_url(
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
    expires_in: int = DEFAULT_PRESIGNED_TTL,
) -> str:
    from ..storage.s3 import generate_presigned_url, upload_object, ensure_bucket_exists
    await ensure_bucket_exists(bucket)
    await upload_object(bucket, key, data, content_type)
    return await generate_presigned_url(bucket, key, expires_in=expires_in)


def _resolve_api_key(tool_config: dict) -> str:
    """Resolve and decrypt the API key from config."""
    from ..core.encryption import decrypt

    api_key = tool_config.get("api_key", "")
    if not api_key:
        return ""

    try:
        return decrypt(api_key)
    except Exception:
        return api_key


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_image_generation_tools(
    tool_config: dict | None = None,
    tenant_id: str = "",
) -> list:
    """Return a list of MAF @tool-decorated async functions for image generation.

    Args:
        tool_config: ``Tool.config`` JSON dict.  May include:
            - ``provider`` (str): "openai" (default), "stability"
            - ``api_key`` (str): API key for the provider (encrypted or plaintext)
            - ``model`` (str): model name (default based on provider)
            - ``default_size`` (str): default image size (default "1024x1024")
            - ``default_quality`` (str): "standard" or "hd"
            - ``base_url`` (str): custom API base URL (for proxies/self-hosted)
        tenant_id: The tenant ID for MinIO bucket resolution.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    provider: str = config.get("provider", "openai").lower()
    api_key: str = _resolve_api_key(config)
    model: str = config.get("model", "")
    default_size: str = config.get("default_size", DEFAULT_SIZE)
    default_quality: str = config.get("default_quality", DEFAULT_QUALITY)
    base_url: str = config.get("base_url", "")

    # Resolve provider defaults
    provider_info = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS["openai"])
    if not model:
        model = provider_info["default_model"]
    if not base_url:
        base_url = provider_info["url"]

    @tool
    async def generate_image(
        prompt: str,
        size: str | None = None,
        quality: str | None = None,
        style: str = "natural",
    ) -> dict:
        """Generate an image from a text prompt using AI.

        Supports DALL·E 3 (via OpenAI) and Stable Diffusion (via Stability AI).
        Generated images are stored and a download URL is returned.

        Args:
            prompt: A detailed description of the image to generate.
                    Be specific about subject, style, colors, lighting, and composition.
            size: Image size. For DALL·E: "1024x1024" (default), "1792x1024", "1024x1792".
                  For Stable Diffusion: size is fixed by the model.
            quality: Image quality. "standard" (default) or "hd" (DALL·E only).
            style: Style hint — "natural" (default) or "vivid" (DALL·E only).

        Returns:
            A dict with:
            - ``url``: presigned download URL for the generated image
            - ``width``: image width in pixels
            - ``height``: image height in pixels
            - ``prompt``: the prompt used
            - ``model``: the model used
            - ``error``: error message if generation failed
        """
        if not prompt or not prompt.strip():
            return {"error": "No prompt provided for image generation"}

        if not api_key:
            return {
                "error": (
                    "Image generation is not configured. Please set an API key "
                    f"for the '{provider}' provider in the tool config."
                ),
            }

        image_size = size or default_size
        if provider_info["supports_size"] and image_size not in VALID_SIZES:
            image_size = DEFAULT_SIZE

        image_quality = quality or default_quality

        # ------------------------------------------------------------------
        # OpenAI / DALL·E path
        # ------------------------------------------------------------------
        if provider == "openai":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload: dict[str, Any] = {
                "model": model,
                "prompt": prompt.strip(),
                "n": 1,
                "size": image_size,
            }

            # DALL·E 3 supports quality and style
            if model.startswith("dall-e-3"):
                payload["quality"] = image_quality
                payload["style"] = style if style in ("natural", "vivid") else "natural"

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.post(
                        base_url,
                        json=payload,
                        headers=headers,
                    )

                    if response.status_code == 401:
                        return {"error": "Authentication failed. Check your API key."}
                    elif response.status_code == 429:
                        return {"error": "Rate limit exceeded. Please wait before generating more images."}
                    elif response.status_code == 400:
                        error_data = response.json()
                        return {"error": f"Invalid request: {error_data.get('error', {}).get('message', response.text[:300])}"}

                    response.raise_for_status()
                    data = response.json()

            except httpx.TimeoutException:
                return {"error": "Image generation timed out (60s). Try a simpler prompt."}
            except Exception as exc:
                logger.error("Image generation failed: %s", exc)
                return {"error": f"Image generation failed: {str(exc)}"}

            image_data = data.get("data", [{}])
            if not image_data:
                return {"error": "No image data in response"}

            image_info = image_data[0]
            image_url = image_info.get("url", "")
            revised_prompt = image_info.get("revised_prompt", "")

            if not image_url:
                # Check for b64_json
                b64 = image_info.get("b64_json", "")
                if b64:
                    image_bytes = base64.b64decode(b64)
                else:
                    return {"error": "No image URL or data in response"}
            else:
                # Download the image from OpenAI's URL
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        img_response = await client.get(image_url)
                        img_response.raise_for_status()
                        image_bytes = img_response.content
                except Exception as exc:
                    logger.error("Failed to download image from OpenAI: %s", exc)
                    return {"error": f"Failed to download generated image: {str(exc)}"}

            # Parse dimensions from size string
            width, height = 1024, 1024
            if "x" in image_size:
                parts = image_size.split("x")
                width = int(parts[0])
                height = int(parts[1])

        # ------------------------------------------------------------------
        # Stability AI path
        # ------------------------------------------------------------------
        elif provider == "stability":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "image/*",
            }

            files = {
                "prompt": (None, prompt.strip()),
                "output_format": (None, "png"),
            }

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.post(
                        base_url,
                        headers=headers,
                        files=files,
                    )

                    if response.status_code == 401:
                        return {"error": "Authentication failed. Check your API key."}
                    elif response.status_code == 402:
                        return {"error": "Insufficient credits for Stability AI."}
                    elif response.status_code == 403:
                        return {"error": "Access denied. Your account may be restricted."}

                    response.raise_for_status()
                    image_bytes = response.content

            except httpx.TimeoutException:
                return {"error": "Image generation timed out (60s)."}
            except Exception as exc:
                logger.error("Stability AI generation failed: %s", exc)
                return {"error": f"Image generation failed: {str(exc)}"}

            width, height = 1024, 1024  # SDXL default
            revised_prompt = ""

        else:
            return {"error": f"Unsupported image generation provider: {provider}"}

        # ------------------------------------------------------------------
        # Upload to MinIO/S3
        # ------------------------------------------------------------------
        if not tenant_id:
            return {
                "error": "Tenant ID not available for file storage",
                "prompt": prompt,
            }

        try:
            bucket = _get_bucket(tenant_id)
            file_id = str(uuid.uuid4())
            key = f"generated/images/{file_id}.png"
            download_url = await _upload_and_get_url(
                bucket, key, image_bytes, "image/png"
            )

            logger.info(
                "Generated image: %s (%dx%d, %d bytes)",
                key, width, height, len(image_bytes),
            )

            result = {
                "url": download_url,
                "width": width,
                "height": height,
                "prompt": prompt,
                "model": model,
                "size_bytes": len(image_bytes),
            }

            if revised_prompt:
                result["revised_prompt"] = revised_prompt

            return result

        except Exception as exc:
            logger.error("Failed to upload generated image: %s", exc)
            return {"error": f"Failed to store generated image: {str(exc)}"}

    return [generate_image]

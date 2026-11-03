"""
AI captions — writes per-platform captions and hashtags from the post's picture.

Providers are tried in order: Groq, then Gemini, then OpenRouter. Any error, or
a reply that is not usable JSON with every requested platform, moves on to the
next one, so a rate limit or a retired model never stops the feature.
"""
import json
import logging
import re

import httpx

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

GROQ_MODELS       = ["qwen/qwen3.8-27b"]
GEMINI_MODELS     = ["gemini-flash-latest", "gemini-flash-lite-latest"]
OPENROUTER_MODELS = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"]

PLATFORM_STYLE = {
    "instagram": "Warm and engaging, 2-4 short lines, a few fitting emojis, then 8-12 hashtags on their own line.",
    "facebook":  "Conversational, 2-3 sentences, ends with a question that invites comments, at most 3 hashtags.",
    "linkedin":  "Professional, 3-5 sentences with a useful takeaway, no slang, at most 1 emoji, 3-5 hashtags at the end.",
    "youtube":   "A video description: 2-3 informative sentences, then 5-8 hashtags on their own line.",
    "x":         "Punchy single thought, at most 240 characters in total including 1-2 hashtags.",
}


class ProviderError(Exception):
    pass


def _build_prompt(master_caption: str, platforms: list[str], has_image: bool) -> str:
    styles = "\n".join(f'- {p}: {PLATFORM_STYLE[p]}' for p in platforms)
    keys   = ", ".join(f'"{p}": "..."' for p in platforms)
    source = (
        "Study the attached picture carefully: the subject, setting, mood, colours and any visible text. "
        "Every caption must clearly be about what is actually in this picture, not generic filler."
        if has_image else "Write from the author's note below."
    )
    note = (
        f'The author\'s note: "{master_caption}". Use it for intent and facts, and write in the same language.'
        if master_caption else "The author gave no note, so write in English."
    )
    return f"""You are a senior social media copywriter. {source}
{note}

Rules:
- Write each caption natively for its platform, not the same text reworded:
{styles}
- Hashtags must be specific to this content and mix popular with niche tags. Never use generic tags like #postly.
- Do not invent names, places, prices or events that are neither visible nor in the note.
- Never identify a person from their face. Name someone, in captions or hashtags, only if the note or visible text names them.
- Avoid cliches such as "Unlock", "Dive in", "Elevate" and "Game changer".

Reply with only this JSON, no markdown:
{{"captions": {{{keys}}}, "youtube_title": "catchy title under 70 characters"}}"""


def _parse_reply(text: str, platforms: list[str]) -> dict:
    # Some models prepend reasoning or wrap the JSON in a code fence.
    text  = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("reply had no JSON")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as e:
        raise ProviderError(f"reply was not valid JSON: {e}")

    captions = data.get("captions") or {}
    missing  = [p for p in platforms if not str(captions.get(p) or "").strip()]
    if missing:
        raise ProviderError(f"reply had no caption for {', '.join(missing)}")

    result = {p: str(captions[p]).strip() for p in platforms}
    if "x" in result and len(result["x"]) > 280:
        result["x"] = result["x"][:280].rsplit(" ", 1)[0]
    return {"captions": result, "youtube_title": str(data.get("youtube_title") or "").strip()[:100]}


async def _openai_compatible(client, url: str, api_key: str, model: str, prompt: str, image_b64: str | None) -> str:
    content: list[dict] = [{"type": "text", "text": prompt}]
    if image_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})
    r = await client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0.7},
    )
    if r.status_code != 200:
        raise ProviderError(f"{model}: HTTP {r.status_code} {r.text[:200]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"{model}: empty reply")


async def _groq(client, model, prompt, image_b64):
    return await _openai_compatible(client, "https://api.groq.com/openai/v1/chat/completions",
                                    settings.GROQ_API_KEY, model, prompt, image_b64)


async def _openrouter(client, model, prompt, image_b64):
    return await _openai_compatible(client, "https://openrouter.ai/api/v1/chat/completions",
                                    settings.OPENROUTER_API_KEY, model, prompt, image_b64)


async def _gemini(client, model, prompt, image_b64):
    parts: list[dict] = [{"text": prompt}]
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    r = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        json={"contents": [{"parts": parts}],
              "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"}},
    )
    if r.status_code != 200:
        raise ProviderError(f"{model}: HTTP {r.status_code} {r.text[:200]}")
    try:
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"{model}: empty reply")


def _providers() -> list[tuple[str, object, list[str], str]]:
    return [
        ("Groq",       _groq,       GROQ_MODELS,       settings.GROQ_API_KEY),
        ("Gemini",     _gemini,     GEMINI_MODELS,     settings.GEMINI_API_KEY),
        ("OpenRouter", _openrouter, OPENROUTER_MODELS, settings.OPENROUTER_API_KEY),
    ]


async def generate_captions(master_caption: str, platforms: list[str], image_b64: str | None, only: str | None = None) -> dict:
    """Returns {captions, youtube_title, provider, model}. `only` limits to one provider name (for testing)."""
    prompt = _build_prompt(master_caption, platforms, bool(image_b64))
    errors = []

    async with httpx.AsyncClient(timeout=45.0) as client:
        for name, call, models, api_key in _providers():
            if not api_key or (only and only != name):
                continue
            for model in models:
                try:
                    parsed = _parse_reply(await call(client, model, prompt, image_b64), platforms)
                    return {**parsed, "provider": name, "model": model}
                except (ProviderError, httpx.HTTPError) as e:
                    logger.warning("[ai] %s %s failed: %s", name, model, e)
                    errors.append(f"{name}: {e}")

    raise ProviderError("; ".join(errors) or "No AI provider is configured.")

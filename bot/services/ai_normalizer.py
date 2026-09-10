from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from bot.config import settings

AI_BASE_URL = settings.ai_base_url
AI_MODEL = settings.ai_model
AI_TOKEN = settings.ai_token


SYSTEM_PROMPT = r'''
You are a strict product-model normalizer for a Telegram phone/accessories catalog.
Your task is to canonicalize phone model names found in messy Persian/English retail posts.

Rules:
1. Output ONLY JSON: {"models":["..."]}.
2. Preserve compatibility groups written with slash as ONE item. Example: "iphone 7/8" -> "iPhone 7/8"; "iphone 7+/8+" -> "iPhone 7 Plus/8 Plus".
3. Never turn an accessory list (AirPods, cases, watches, etc.) into phone models.
4. iPhone: canonical prefix is exactly "iPhone". Normalize spacing/case: 17promax -> iPhone 17 Pro Max; 14Pro -> iPhone 14 Pro; Xsmax -> iPhone XS Max.
5. Samsung: REMOVE the word "Samsung" from output. Keep model identity exactly, including the lowercase s in A21s. A21 s -> A21s, NOT A21. Keep FE, Ultra, Plus, and network suffixes such as 4G/5G when present.
6. Xiaomi: REMOVE only the generic brand word "Xiaomi" from output, but DO NOT remove "Redmi" when it is part of the product name. In a Xiaomi/Redmi Note section, canonical Note names use "Redmi Note ...". Example: Note12 4G -> Redmi Note 12 4G; Note 12S -> Redmi Note 12 S; Note 13 pro plus -> Redmi Note 13 Pro Plus.
7. Do not invent a 4G/5G suffix when the source does not contain enough evidence. Prefer exact evidence over guessing.
8. Deduplicate identical canonical models.
9. Sort naturally by brand order iPhone, Samsung-family, Xiaomi-family; within each family sort by model number ascending, then variants in a sensible order.
10. Do not output brand names "Samsung" or "Xiaomi" as prefixes. "Redmi" is allowed and required for Redmi Note models.
11. Return an empty array when no phone models can be extracted.
'''


def _endpoint() -> str:
    base = (AI_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, flags=re.S)
    if not match:
        raise ValueError("AI returned non-JSON output")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI JSON root is not an object")
    return data


def _clean_model_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("AI response does not contain a models array")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = re.sub(r"\s+", " ", value.strip(" |,;\n\t"))
        if not value:
            continue
        # Hard safety rules after AI.
        value = re.sub(r"(?i)^Samsung\s+", "", value).strip()
        value = re.sub(r"(?i)^Xiaomi\s+(?=Redmi\b)", "", value).strip()
        if re.fullmatch(r"(?i)(?:case|airpods?|apple watch|watch)\b.*", value):
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _deterministic_is_safe(candidate: str) -> bool:
    if not candidate:
        return False
    if re.search(r"(?i)\bSamsung\s+", candidate):
        return False
    if re.search(r"(?i)\bXiaomi\s+(?!Redmi\b)", candidate):
        return False
    if re.search(r"(?i)\bA\d{1,3}\s+s\b", candidate):
        return False
    return True


async def ai_normalize(raw_text: str, deterministic: str, job_log) -> str:
    # AI is optional but strongly recommended for messy/ambiguous posts.
    if not AI_BASE_URL or not AI_TOKEN or not AI_MODEL:
        job_log.add(logging.WARNING, "AI is not configured; using deterministic parser only.")
        if _deterministic_is_safe(deterministic):
            return deterministic
        return deterministic

    payload = {
        "model": AI_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "RAW TELEGRAM TEXT:\n"
                    + raw_text
                    + "\n\nDETERMINISTIC CANDIDATE (may be imperfect):\n"
                    + (deterministic or "<empty>")
                    + "\n\nCanonicalize and return only the JSON object."
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    endpoint = _endpoint()
    headers = {"Authorization": f"Bearer {AI_TOKEN}", "Content-Type": "application/json"}
    job_log.add(logging.INFO, "Calling AI model=%s endpoint=%s", AI_MODEL, endpoint)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        data = _extract_json(content)
        models = _clean_model_list(data.get("models"))
        result = " | ".join(models)
        job_log.add(logging.INFO, "AI returned %d models.", len(models))
        return result
    except Exception as exc:
        job_log.add(logging.ERROR, "AI normalization failed: %s", exc)
        # Fail closed to deterministic output instead of failing the entire bot job.
        return deterministic

import asyncio
import json
import re
from time import monotonic
import httpx
from pydantic import ValidationError
from app.config import CONFIG, ROOT
from app.errors import AppError
from app.models.dto import ReadingResult, MonthlyResult
from app.services.safety import valid_output


class LLMAdapter:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(CONFIG["llm"]["read_timeout"], connect=CONFIG["llm"]["connect_timeout"]))
        self.semaphore = asyncio.Semaphore(CONFIG["llm"]["concurrency"])

    async def generate(self, kind, context, budget=30):
        if budget <= 0:
            raise AppError("LLM_TIMEOUT")
        started = monotonic()
        prompt = (ROOT / "prompts" / f"{kind}_v1.txt").read_text(encoding="utf-8")
        body = {"model": self.settings.llm_model, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}], "temperature": 0.7 if kind == "reading" else 0.4, "max_tokens": 1200 if kind == "reading" else 1800, "stream": False}
        if CONFIG["llm"]["json_mode"]:
            body["response_format"] = {"type": "json_object"}
        try:
            async with asyncio.timeout(budget):
                async with self.semaphore:
                    for attempt in range(2):
                        try:
                            response = await self.client.post(self.settings.llm_base_url.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + self.settings.llm_api_key.get_secret_value()}, json=body)
                        except httpx.TimeoutException:
                            raise AppError("LLM_TIMEOUT") from None
                        except httpx.NetworkError:
                            if attempt == 0 and budget - (monotonic() - started) >= 15:
                                continue
                            raise AppError("LLM_TIMEOUT") from None
                        if response.status_code == 429 or response.status_code >= 500:
                            try:
                                delay = min(30, max(0, float(response.headers.get("retry-after", "0"))))
                            except ValueError:
                                delay = 0
                            if attempt == 0 and budget - (monotonic() - started) >= 15 + delay:
                                await asyncio.sleep(delay)
                                continue
                        # A gateway lacking JSON mode is configured with json_mode=false.
                        if response.status_code != 200:
                            raise AppError("INVALID_LLM_RESPONSE")
                        content = response.json()["choices"][0]["message"]["content"]
                        result = (ReadingResult if kind == "reading" else MonthlyResult).model_validate_json(content)
                        allowed = {x["id"] for x in context.get("memory", context.get("notes", []))}
                        if kind == "reading" and not set(result.memory_refs) <= allowed:
                            raise AppError("INVALID_LLM_RESPONSE")
                        if kind == "monthly":
                            refs = set(re.findall(r"(?:diary|reading):[0-9a-fA-F-]{36}", content))
                            if not refs <= allowed:
                                raise AppError("INVALID_LLM_RESPONSE")
                        if not valid_output(content):
                            raise AppError("SAFETY_RESTRICTED")
                        return result.model_dump()
        except TimeoutError:
            raise AppError("LLM_TIMEOUT") from None
        except (ValueError, KeyError, IndexError, TypeError, ValidationError, httpx.HTTPError):
            raise AppError("INVALID_LLM_RESPONSE") from None
        raise AppError("INVALID_LLM_RESPONSE")

    async def close(self):
        await self.client.aclose()


def fallback_reading(cards):
    return {"interpretation": "Базовая трактовка без персонализации. Персональная трактовка сейчас недоступна.\n\n" + "\n\n".join(f"{c['label']} — {c['name']}: {c['meaning']}" for c in cards), "reflection_question": "Какая из этих мыслей помогает тебе лучше понять свои чувства?", "memory_refs": []}

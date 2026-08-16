"""The client. Five things, and only five.

1. Forces a schema and validates before returning, failing closed.
2. Caches on a content hash on disk, with the prompt text in the key.
3. Enforces a hard cost ceiling, raising before the call that would breach it.
4. Handles prompt caching of the shared corpus.
5. Retries on rate limits and 5xx only.

Built on urllib rather than the Anthropic SDK because there is no working pip on
this machine. That is a constraint, not a preference, but it has one real
benefit: the final run has no install step that can fail. Only Anthropic models
are called and no third-party framework is involved either way.

Structured output goes through output_config.format rather than a forced tool
call. Forced tool_choice and extended thinking do not combine on every model;
output_config.format is documented to work with thinking, and the judge is the
one call where losing thinking would cost the most.

Schema errors fail fast. Retrying malformed structured output mostly reproduces
the same malformed output and bills you twice.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ..events import EventSink
from ..ledger import CallRecord, CostLedger, call_cost_usd
from .cache import ResponseCache
from .schema import SchemaError, validate
from .tiers import Tier, TierRouter

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

RETRYABLE_STATUS = (408, 409, 429, 500, 502, 503, 504, 529)
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2, 6, 15)

# Rough characters-per-token, used only to estimate a call's cost before making
# it so the ceiling can refuse in advance. Deliberately pessimistic: a ceiling
# that under-estimates is not a ceiling.
CHARS_PER_TOKEN = 3.2


class ModelCallFailed(Exception):
    """The call could not be completed after retries, or came back unusable."""


class DeepCallBudgetExceeded(Exception):
    """More than one deep-tier call in a single run."""


@dataclass
class AgentResponse:
    value: dict[str, Any]
    call: CallRecord
    cached: bool


@dataclass
class Client:
    router: TierRouter
    ledger: CostLedger
    cache: ResponseCache
    events: EventSink
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    timeout_s: int = 900
    max_deep_calls: int = 1
    offline: bool = False

    def __post_init__(self) -> None:
        if not self.api_key and not self.offline:
            raise ModelCallFailed(
                "ANTHROPIC_API_KEY is not set. Run with the fixture generator if you want a "
                "no-network run; a missing key must not silently become a degraded forecast."
            )

    # -- the one public method ------------------------------------------

    def call(
        self,
        *,
        stage: str,
        node: str,
        agent: str,
        tier_name: str,
        system_blocks: list[dict[str, Any]],
        user_text: str,
        response_schema: dict[str, Any],
        max_tokens: int = 8000,
        effort: str | None = None,
        prompt_version: str = "unversioned",
    ) -> AgentResponse:
        tier = self.router[tier_name]
        body = self._build_body(
            tier=tier,
            system_blocks=system_blocks,
            user_text=user_text,
            response_schema=response_schema,
            max_tokens=max_tokens,
            effort=effort,
        )

        key = self.cache.key({"body": body, "prompt_version": prompt_version, "schema": response_schema})
        cached = self.cache.get(key)
        if cached is not None:
            value = validate(cached["value"], response_schema)
            record = CallRecord(**cached["call"])
            self.ledger.record(record)
            self.events.emit(
                stage,
                "agent_cached",
                node=node,
                agent=agent,
                tier=tier.name,
                model=tier.model,
                cache_key=key[:12],
                prompt_version=prompt_version,
            )
            return AgentResponse(value=value, call=record, cached=True)

        if tier.name == "deep" and self.ledger.deep_calls >= self.max_deep_calls:
            raise DeepCallBudgetExceeded(
                f"{agent} would be deep-tier call number {self.ledger.deep_calls + 1}; "
                f"exactly {self.max_deep_calls} is allowed per run"
            )

        estimate = self._estimate_usd(tier, body, max_tokens)
        self.ledger.check_before_call(estimate)

        self.events.emit(
            stage,
            "agent_started",
            node=node,
            agent=agent,
            tier=tier.name,
            model=tier.model,
            prompt_version=prompt_version,
            estimated_usd=round(estimate, 4),
        )

        started = time.monotonic()
        payload = self._post(body)
        duration = time.monotonic() - started

        usage = payload.get("usage", {})
        record = CallRecord(
            stage=stage,
            agent=agent,
            tier=tier.name,
            model=tier.model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0)),
            usd=0.0,
            cached=False,
            duration_s=round(duration, 2),
        )
        record.usd = call_cost_usd(
            tier.model,
            record.input_tokens,
            record.output_tokens,
            record.cache_write_tokens,
            record.cache_read_tokens,
        )
        self.ledger.record(record)

        # Both cache-write and cache-read on the same event. A large write with a
        # read of zero looks identical, from the read alone, to caching never
        # having been enabled at all.
        self.events.emit(
            stage,
            "agent_finished",
            node=node,
            agent=agent,
            tier=tier.name,
            model=tier.model,
            duration_s=record.duration_s,
            cost_usd=round(record.usd, 4),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_write_tokens=record.cache_write_tokens,
            cache_read_tokens=record.cache_read_tokens,
            stop_reason=payload.get("stop_reason"),
        )

        value = self._extract(payload, agent, stage)
        validate(value, response_schema)

        self.cache.put(key, {"value": value, "call": record.__dict__})
        return AgentResponse(value=value, call=record, cached=False)

    # -- internals -------------------------------------------------------

    def _build_body(
        self,
        *,
        tier: Tier,
        system_blocks: list[dict[str, Any]],
        user_text: str,
        response_schema: dict[str, Any],
        max_tokens: int,
        effort: str | None,
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": response_schema}
        }
        # Effort is a hard 400 on the cheap tier, which is where every
        # extraction prompt runs. Gate on the prefix, never on an exact string.
        if effort and tier.supports_effort:
            output_config["effort"] = effort

        body: dict[str, Any] = {
            "model": tier.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user_text}],
            "output_config": output_config,
        }
        if tier.supports_thinking:
            body["thinking"] = {"type": "adaptive"}
        return body

    def _estimate_usd(self, tier: Tier, body: dict[str, Any], max_tokens: int) -> float:
        characters = len(json.dumps(body, default=str))
        input_tokens = int(characters / CHARS_PER_TOKEN)
        return call_cost_usd(tier.model, input_tokens, max_tokens)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                if error.code not in RETRYABLE_STATUS:
                    # A 400 is a malformed request. Retrying reproduces it and
                    # bills twice.
                    raise ModelCallFailed(f"HTTP {error.code}: {detail}") from error
                last_error = ModelCallFailed(f"HTTP {error.code}: {detail}")
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = ModelCallFailed(f"transport failure: {error}")

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS[attempt])

        raise ModelCallFailed(f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")

    def _extract(self, payload: dict[str, Any], agent: str, stage: str) -> dict[str, Any]:
        stop_reason = payload.get("stop_reason")
        if stop_reason == "max_tokens":
            # A lens that truncates mid-string surfaces as a JSON parse error
            # rather than as truncation, and the symptom points nowhere near the
            # cause. Name it here instead.
            raise ModelCallFailed(
                f"{agent}: response hit max_tokens and is truncated; raise max_tokens rather "
                "than parsing the fragment"
            )
        if stop_reason == "refusal":
            raise ModelCallFailed(f"{agent}: the model declined the request ({payload.get('stop_details')})")

        for block in payload.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError as error:
                    raise ModelCallFailed(f"{agent}: response was not valid JSON: {error}") from error

        raise ModelCallFailed(f"{agent}: response carried no text block (stop_reason={stop_reason})")


def cacheable(text: str, tier: Tier) -> dict[str, Any]:
    """A system block marked for prompt caching, if it is long enough to cache.

    Below the model's minimum cacheable prefix nothing caches and no error is
    raised -- cache_creation_input_tokens simply comes back zero. Marking a block
    that cannot cache costs nothing but makes the Cost sheet look like caching
    failed, so the marker goes on only when it can do something.
    """
    block: dict[str, Any] = {"type": "text", "text": text}
    if len(text) / CHARS_PER_TOKEN >= tier.cache_minimum_tokens:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def plain(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}

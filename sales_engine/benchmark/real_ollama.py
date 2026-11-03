"""
sales_engine/benchmark/real_ollama.py
Authoritative Real Ollama Benchmark Runner for SalesAI (Production Readiness Step 6).
Executes actual local Ollama inference calls and captures real nanosecond durations
and token counts from the official Ollama API.

Strict Safety and Integrity Guarantees:
1. Calls localhost/127.0.0.1 ONLY. Never calls external endpoints or public tunnels.
2. Never automatically pulls, deletes, or modifies models.
3. If model is missing, returns status NOT_INSTALLED without failing the whole benchmark.
4. Separates cold run from warm repetitions.
5. Captures real response fields; unavailable measurements remain None (null).
6. NEVER injects synthetic latency, fabricated throughput, or offline estimates.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OllamaMetrics:
    """Real inference metrics captured from Ollama API response."""

    # Real Ollama nanoseconds converted to seconds
    total_duration_s: Optional[float] = None
    load_duration_s: Optional[float] = None
    prompt_eval_duration_s: Optional[float] = None
    eval_duration_s: Optional[float] = None

    # Token counts
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None

    # Derived token throughput (tokens per second)
    tokens_per_second: Optional[float] = None

    # External wall-clock elapsed duration
    wall_duration_s: Optional[float] = None

    # Measurement provenance
    measurement_status: str = "measured"  # "measured" or "not_measured"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OllamaInferenceResult:
    """Outcome of a single inference attempt."""

    fixture_id: str
    task: str
    model: str
    repetition_index: int
    is_cold_run: bool = False
    status: str = "success"  # success, not_installed, timeout, http_error, network_error, malformed_response
    error_message: Optional[str] = None
    raw_response_text: str = ""
    metrics: OllamaMetrics = field(default_factory=OllamaMetrics)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["metrics"] = self.metrics.to_dict()
        return d


class OllamaBenchmarkRunner:
    """Real Ollama runner connecting to local daemon."""

    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_OPTIONS = {
        "temperature": 0.0,
        "seed": 42,
        "num_ctx": 4096,
        "num_predict": 512,
    }

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 60,
    ):
        self.base_url = self._validate_and_normalize_url(base_url)
        self.timeout = timeout

    @staticmethod
    def _validate_and_normalize_url(url: str) -> str:
        """Enforces that only localhost/127.0.0.1 loopback endpoints are allowed."""
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"Security violation: Real Ollama benchmark must use a local loopback address, got '{hostname}'"
            )
        return url.rstrip("/")

    def check_reachability(self) -> Tuple[bool, Optional[str]]:
        """Checks if the local Ollama server is reachable and running."""
        url = f"{self.base_url}/api/version"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return True, data.get("version", "unknown")
                return False, None
        except Exception as e:
            return False, str(e)

    def list_installed_models(self) -> List[Dict[str, Any]]:
        """Queries /api/tags for installed models without modifying them."""
        url = f"{self.base_url}/api/tags"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("models", [])
        except Exception:
            pass
        return []

    def is_model_installed(self, model_name: str) -> bool:
        """Checks if the requested model is currently installed locally."""
        installed = self.list_installed_models()
        target = model_name.strip().lower()
        for m in installed:
            name = m.get("name", "").lower()
            if name == target:
                return True
            # Also handle tag without :latest
            if ":" not in target and name.startswith(target + ":"):
                return True
            if ":" in target and name == target:
                return True
        return False

    def execute_inference(
        self,
        fixture: Dict[str, Any],
        model: str,
        repetition_index: int = 0,
        is_cold_run: bool = False,
        custom_options: Optional[Dict[str, Any]] = None,
    ) -> OllamaInferenceResult:
        """
        Executes a real inference request against the local Ollama endpoint.
        Captures real durations and token metrics.
        Never injects synthetic latencies.
        """
        fid = fixture.get("fixture_id", "unknown")
        task = fixture.get("task", "unknown")
        prompt = fixture.get("user_prompt") or fixture.get("input_text", "")
        system = fixture.get("system_prompt", "")

        result = OllamaInferenceResult(
            fixture_id=fid,
            task=task,
            model=model,
            repetition_index=repetition_index,
            is_cold_run=is_cold_run,
        )

        options = dict(self.DEFAULT_OPTIONS)
        if custom_options:
            options.update(custom_options)

        payload = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": options,
        }

        url = f"{self.base_url}/api/generate"
        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        wall_start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                wall_end = time.perf_counter()
                wall_duration = wall_end - wall_start

                if resp.status != 200:
                    result.status = "http_error"
                    result.error_message = f"HTTP {resp.status}"
                    result.metrics.measurement_status = "not_measured"
                    return result

                raw_data = resp.read().decode("utf-8")
                data = json.loads(raw_data)
                response_text = data.get("response", "")
                result.raw_response_text = response_text
                result.status = "success"

                # Extract REAL Ollama duration metrics (nanoseconds -> seconds)
                total_dur_ns = data.get("total_duration")
                load_dur_ns = data.get("load_duration")
                prompt_eval_dur_ns = data.get("prompt_eval_duration")
                eval_dur_ns = data.get("eval_duration")
                prompt_eval_count = data.get("prompt_eval_count")
                eval_count = data.get("eval_count")

                total_dur_s = (total_dur_ns / 1e9) if total_dur_ns is not None else None
                load_dur_s = (load_dur_ns / 1e9) if load_dur_ns is not None else None
                prompt_eval_dur_s = (prompt_eval_dur_ns / 1e9) if prompt_eval_dur_ns is not None else None
                eval_dur_s = (eval_dur_ns / 1e9) if eval_dur_ns is not None else None

                # Compute real token throughput: eval_count / eval_duration_s
                tokens_per_sec = None
                if eval_count is not None and eval_dur_s is not None and eval_dur_s > 0:
                    tokens_per_sec = round(eval_count / eval_dur_s, 2)

                result.metrics = OllamaMetrics(
                    total_duration_s=total_dur_s,
                    load_duration_s=load_dur_s,
                    prompt_eval_duration_s=prompt_eval_dur_s,
                    eval_duration_s=eval_dur_s,
                    prompt_eval_count=prompt_eval_count,
                    eval_count=eval_count,
                    tokens_per_second=tokens_per_sec,
                    wall_duration_s=round(wall_duration, 4),
                    measurement_status="measured",
                )
                return result

        except urllib.error.HTTPError as e:
            wall_end = time.perf_counter()
            result.status = "http_error"
            result.error_message = f"HTTP {e.code}: {e.reason}"
            result.metrics.wall_duration_s = round(wall_end - wall_start, 4)
            result.metrics.measurement_status = "not_measured"
            # Model not found on Ollama usually returns 404
            if e.code == 404:
                result.status = "not_installed"
            return result

        except urllib.error.URLError as e:
            wall_end = time.perf_counter()
            result.status = "network_error"
            result.error_message = f"Connection error: {e.reason}"
            result.metrics.wall_duration_s = round(wall_end - wall_start, 4)
            result.metrics.measurement_status = "not_measured"
            return result

        except TimeoutError:
            wall_end = time.perf_counter()
            result.status = "timeout"
            result.error_message = f"Inference timed out after {self.timeout}s"
            result.metrics.wall_duration_s = round(wall_end - wall_start, 4)
            result.metrics.measurement_status = "not_measured"
            return result

        except Exception as e:
            wall_end = time.perf_counter()
            result.status = "error"
            result.error_message = str(e)
            result.metrics.wall_duration_s = round(wall_end - wall_start, 4)
            result.metrics.measurement_status = "not_measured"
            return result

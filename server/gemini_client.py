"""Thin Gemini REST client: text, JSON, embeddings, image reading. Auto-retries on rate limits."""
import base64
import json
import os
import re
import time

import requests

from . import settings

BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(RuntimeError):
    pass


class BusyError(LLMError):
    """Google is overloaded / rate-limiting. Another model may still work."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class ModelMissing(LLMError):
    """The model name is retired or wrong."""


_bad_until = {}       # model -> time before which we try it LAST (it was just overloaded / missing)
_thinking_off = set()  # models that rejected the "thinking" setting


def _models():
    """Main model first, then backups (GEMINI_FALLBACK_MODELS in .env, comma separated)."""
    backups = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.1-flash-lite")
    names = [settings.GEMINI_MODEL] + [m.strip() for m in backups.split(",") if m.strip()]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _ordered_models():
    """Models that failed in the last minutes go to the back, so one busy model does not slow every call."""
    now = time.time()
    models = _models()
    good = [m for m in models if _bad_until.get(m, 0) <= now]
    bad = [m for m in models if _bad_until.get(m, 0) > now]
    return good + bad


def _headers():
    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY.startswith("paste_"):
        raise LLMError("GEMINI_API_KEY is missing. Open the .env file and paste your key.")
    return {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}


def _api_message(text):
    try:
        return json.loads(text)["error"]["message"]
    except Exception:
        return text[:200]


def _retry_delay(resp):
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        pass
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', resp.text)
    return float(m.group(1)) if m else None


def _busy_message(status, last):
    if status == 429:
        why = ("You have reached Gemini's usage limit (too many requests per minute, or today's free quota is used up). "
               "The app already waited and retried. Wait a minute and try again.")
    elif status == "network":
        why = "Could not reach Google's AI service. Check your internet connection and try again."
    else:
        why = ("Google's AI servers are overloaded right now. This is not a problem with your app. "
               "The app already retried and tried backup models. Try again in a minute.")
    return f"{why} (details: {last[:140]})"


def _post(path, payload, retries=7, timeout=240):
    last, status = "", None
    for attempt in range(retries):
        is_last = attempt == retries - 1
        try:
            r = requests.post(f"{BASE}/{path}", headers=_headers(), json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last, status = str(exc), "network"
            if not is_last:
                time.sleep(min(30, 2 ** attempt))
            continue
        if r.status_code == 200:
            return r.json()
        status = r.status_code
        last = f"{r.status_code}: {_api_message(r.text)}"
        if r.status_code in (429, 500, 502, 503, 504):
            if r.status_code == 429 and "PerDay" in r.text:
                break  # daily quota is gone: waiting a minute will not help, let another model try
            if not is_last:
                wait = _retry_delay(r)
                if wait is None:
                    wait = 3 * (2 ** attempt)
                time.sleep(min(60, wait))
            continue
        if r.status_code in (400, 403) and "API key" in r.text:
            raise LLMError("Gemini rejected the API key. Check GEMINI_API_KEY in the .env file.")
        if r.status_code == 404:
            raise ModelMissing(f"Model not found. Check GEMINI_MODEL / GEMINI_EMBED_MODEL in .env. ({last})")
        raise LLMError(f"Gemini error {last}")
    raise BusyError(_busy_message(status, last), status)


def _with_thinking(payload, model, thinking):
    """Gemini 3.x thinks deeply by default, which adds minutes to long writing. 'low'/'minimal' keeps quality, cuts the wait."""
    if not thinking or model in _thinking_off:
        return payload
    cfg = dict(payload["generationConfig"])
    if re.search(r"gemini-2", model):
        cfg["thinkingConfig"] = {"thinkingBudget": {"minimal": 0, "low": 1024, "medium": 4096}.get(thinking, 1024)}
    else:
        cfg["thinkingConfig"] = {"thinkingLevel": thinking}
    return {**payload, "generationConfig": cfg}


def _try_model(model, payload, thinking, retries):
    try:
        return _post(f"models/{model}:generateContent", _with_thinking(payload, model, thinking), retries=retries, timeout=300)
    except (BusyError, ModelMissing):
        raise
    except LLMError as exc:
        if thinking and "thinking" in str(exc).lower():
            _thinking_off.add(model)  # this model does not accept the setting: remember and retry without it
            return _post(f"models/{model}:generateContent", payload, retries=retries, timeout=300)
        raise


def _generate_content(payload, thinking=None, label="call"):
    """generateContent with retry, backup models, and a timing line in the terminal."""
    models = _ordered_models()
    last = None
    for i, m in enumerate(models):
        t0 = time.time()
        try:
            data = _try_model(m, payload, thinking, retries=3 if i < len(models) - 1 else 5)
        except BusyError as exc:
            _bad_until[m] = time.time() + 90
            last = exc
            continue
        except ModelMissing as exc:
            _bad_until[m] = time.time() + 3600
            last = exc
            continue
        u = data.get("usageMetadata") or {}
        print(f"[gemini] {label}: {m} {time.time() - t0:.1f}s in={u.get('promptTokenCount', '?')} "
              f"out={u.get('candidatesTokenCount', '?')} thinking={u.get('thoughtsTokenCount', 0)}", flush=True)
        return data
    raise last


def generate(prompt, system=None, json_mode=False, temperature=0.4, max_tokens=16384, thinking=None, label="call"):
    cfg = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _generate_content(payload, thinking=thinking, label=label)
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer returned")
        raise LLMError(f"The AI returned nothing ({reason}).")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def parse_json(text):
    """Parse model output into JSON, tolerating code fences and stray text."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if starts:
        s = min(starts)
        e = max(t.rfind("}"), t.rfind("]"))
        if e > s:
            try:
                return json.loads(t[s:e + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("The AI answer was not valid JSON.")


def generate_json(prompt, system=None, temperature=0.4, thinking=None, label="json"):
    last = None
    for attempt in range(2):
        extra = "" if attempt == 0 else "\n\nIMPORTANT: Return COMPLETE, valid JSON only. Keep it shorter if needed."
        text = generate(prompt + extra, system=system, json_mode=True, temperature=temperature, thinking=thinking, label=label)
        try:
            return parse_json(text)
        except LLMError as exc:
            last = exc
    raise last


def embed(texts, task="RETRIEVAL_DOCUMENT"):
    """Return one vector per text. Batches of 64."""
    out = []
    for i in range(0, len(texts), 64):
        batch = texts[i:i + 64]
        reqs = []
        for t in batch:
            r = {
                "model": f"models/{settings.GEMINI_EMBED_MODEL}",
                "content": {"parts": [{"text": t[:7000]}]},
                "taskType": task,
            }
            if settings.EMBED_DIM:
                r["outputDimensionality"] = settings.EMBED_DIM
            reqs.append(r)
        try:
            data = _post(f"models/{settings.GEMINI_EMBED_MODEL}:batchEmbedContents", {"requests": reqs})
        except LLMError as exc:
            if "400" in str(exc) and settings.EMBED_DIM:
                for r in reqs:
                    r.pop("outputDimensionality", None)
                data = _post(f"models/{settings.GEMINI_EMBED_MODEL}:batchEmbedContents", {"requests": reqs})
            else:
                raise
        out.extend(e["values"] for e in data["embeddings"])
    return out


def read_image(image_bytes, mime, prompt):
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
    }
    data = _generate_content(payload, label="read-image")
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def transcribe_audio(audio_bytes, mime, prompt, label="transcribe"):
    """Send one audio chunk (inline, must stay under the 20 MB request limit) and return the model's JSON text."""
    b64 = base64.b64encode(audio_bytes).decode()
    payload = {
        "contents": [{"role": "user", "parts": [{"inline_data": {"mime_type": mime, "data": b64}}, {"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 32768, "responseMimeType": "application/json"},
    }
    data = _generate_content(payload, thinking="minimal", label=label)
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer returned")
        raise LLMError(f"The AI returned nothing for this audio part ({reason}).")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()

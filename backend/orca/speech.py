"""Speech-to-text for the chat microphone.

The browser records the question and uploads it; the server transcribes it in the speaker's own language and
script, so voice works in every browser and the language is detected rather than guessed from a UI setting.

Engines, tried in order until one succeeds:
  gemini   — Gemini (default gemini-2.5-flash, override ORCA_GEMINI_STT_MODEL; key GEMINI_API_KEY). Best on
             Indian languages in ORCA's tests (exact Hindi/Tamil/Malayalam), ~3.5 s.
  whisper  — Whisper on Groq (default whisper-large-v3, override ORCA_WHISPER_MODEL; key GROQ_API_KEY). ~0.8 s,
             but weaker on Dravidian languages (Malayalam came back in Gurmukhi script), so it is the fallback.
The transcript is only ever put in the chat box: the user sees and can correct it before sending."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from .i18n.detect import LANGUAGE_NAMES

log = logging.getLogger("orca.speech")

MAX_AUDIO_BYTES = 8 * 1024 * 1024  # ~4 min of 16 kHz mono WAV; the UI stops recording at 30 s

# Whisper reports language names; ORCA uses ISO 639-1 codes.
WHISPER_LANGUAGES = {
    "english": "en", "hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml", "kannada": "kn", "bengali": "bn",
    "marathi": "mr", "gujarati": "gu", "punjabi": "pa", "urdu": "ur", "odia": "or", "oriya": "or", "assamese": "as",
}


@dataclass
class Transcript:
    text: str
    language: str | None  # ISO 639-1
    engine: str
    latency_ms: float


class TranscriptionError(Exception):
    pass


class GeminiTranscriber:
    name = "gemini"

    def __init__(self, timeout_s: float = 45.0) -> None:
        self.model = os.getenv("ORCA_GEMINI_STT_MODEL", "gemini-2.5-flash")
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})

    async def transcribe(self, audio: bytes, mime: str, hint: str | None) -> tuple[str, str | None]:
        prompt = ("Transcribe this speech exactly, in its original language and native script. Do not translate, "
                  "answer or add anything. If there is no intelligible speech, return empty text.")
        if hint:
            prompt += f" The speaker has chosen {LANGUAGE_NAMES.get(hint, hint)}."
        body = {
            "contents": [{"parts": [{"inlineData": {"mimeType": mime, "data": base64.b64encode(audio).decode()}}, {"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": {"type": "object", "required": ["text", "language"], "properties": {
                    "text": {"type": "string"}, "language": {"type": "string", "description": "ISO 639-1 code of the speech"}}},
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        r = await self._client.post(url, json=body)
        if r.status_code != 200:
            log.warning("gemini transcription error %s: %s", r.status_code, r.text[:500])
            raise TranscriptionError("rate limited" if r.status_code == 429 else f"API error {r.status_code}")
        try:
            data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise TranscriptionError(f"unexpected response: {type(exc).__name__}") from exc
        return str(data.get("text", "")).strip(), (str(data.get("language") or "").lower()[:2] or None)


class WhisperTranscriber:
    name = "whisper"

    def __init__(self, timeout_s: float = 45.0) -> None:
        self.model = os.getenv("ORCA_WHISPER_MODEL", "whisper-large-v3")
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})

    async def transcribe(self, audio: bytes, mime: str, hint: str | None) -> tuple[str, str | None]:
        data = {"model": self.model, "response_format": "verbose_json", **({"language": hint} if hint else {})}
        r = await self._client.post("https://api.groq.com/openai/v1/audio/transcriptions", data=data,
                                    files={"file": ("speech.wav", audio, mime)})
        if r.status_code != 200:
            log.warning("whisper transcription error %s: %s", r.status_code, r.text[:500])
            raise TranscriptionError("rate limited" if r.status_code == 429 else f"API error {r.status_code}")
        j = r.json()
        return j.get("text", "").strip(), WHISPER_LANGUAGES.get(str(j.get("language", "")).lower(), hint)


class Transcriber:
    def __init__(self, engines: list) -> None:
        self.engines = engines

    def describe(self) -> dict:
        return {"available": bool(self.engines), "engines": [f"{e.name}:{e.model}" for e in self.engines]}

    async def transcribe(self, audio: bytes, mime: str, hint: str | None = None) -> Transcript:
        errors = []
        for engine in self.engines:
            started = time.perf_counter()
            try:
                text, language = await engine.transcribe(audio, mime, hint)
            except (TranscriptionError, httpx.HTTPError) as exc:
                errors.append(f"{engine.name}: {exc if isinstance(exc, TranscriptionError) else type(exc).__name__}")
                continue
            return Transcript(text, language, f"{engine.name}:{engine.model}", round((time.perf_counter() - started) * 1000, 1))
        raise TranscriptionError("; ".join(errors) or "no speech engine configured")


def transcriber_from_env() -> Transcriber:
    engines = []
    if os.getenv("GEMINI_API_KEY"):
        engines.append(GeminiTranscriber())
    if os.getenv("GROQ_API_KEY"):
        engines.append(WhisperTranscriber())
    return Transcriber(engines)


# ------------------------------------------------------------------------------ text-to-speech
# Phones often have no Tamil, Telugu or Malayalam voice, so the fishermen's page can ask the server to read the
# verdict aloud. Gemini's speech model (default gemini-2.5-flash-preview-tts, override ORCA_GEMINI_TTS_MODEL)
# returns 24 kHz 16-bit PCM, wrapped here as WAV. Answers repeat a lot (same harbour, same verdict), so a small
# in-memory cache saves the free-tier quota.
MAX_SPEECH_CHARS = 600


class SpeechError(Exception):
    pass


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    import struct

    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(pcm)) + pcm


class GeminiSpeaker:
    name = "gemini-tts"

    def __init__(self, timeout_s: float = 60.0, cache_size: int = 64) -> None:
        self.model = os.getenv("ORCA_GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        self._cache: dict[tuple[str, str], bytes] = {}
        self._cache_size = cache_size

    async def speak(self, text: str, language: str) -> bytes:
        key = (text, language)
        if key in self._cache:
            return self._cache[key]
        body = {
            "contents": [{"parts": [{"text": f"Read this aloud slowly and clearly, exactly as written: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            r = await self._client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise SpeechError(f"connection error: {type(exc).__name__}") from exc
        if r.status_code != 200:
            log.warning("gemini speech error %s: %s", r.status_code, r.text[:500])
            raise SpeechError("rate limited" if r.status_code == 429 else f"API error {r.status_code}")
        try:
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"])
        except (KeyError, IndexError, ValueError) as exc:
            raise SpeechError(f"unexpected response: {type(exc).__name__}") from exc
        wav = pcm_to_wav(pcm)
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = wav
        return wav


def speaker_from_env() -> GeminiSpeaker | None:
    return GeminiSpeaker() if os.getenv("GEMINI_API_KEY") else None

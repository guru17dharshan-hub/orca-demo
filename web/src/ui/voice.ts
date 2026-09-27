// Voice input for the chat: tap once, speak, pause — the question is sent.
//  • the recording goes to the server (/api/transcribe), which detects the language and writes it in its script;
//  • a level meter and silence detection run locally: ~1.4 s of quiet after speech ends the recording;
//  • where the browser has its own recognizer (Chrome/Edge) and the language is known, words also appear live
//    while speaking; the server's transcript replaces them. Without a server engine the browser result is final.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { toWav16k } from "../audio";
import { STATIC_DEMO } from "../demo";
import { SPEECH_LOCALE } from "../i18n";

export type VoicePhase = "idle" | "listening" | "working";

const Recognizer: any =
  typeof window !== "undefined" && !STATIC_DEMO ? (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition : undefined;
export const CAN_RECORD = typeof window !== "undefined" && !STATIC_DEMO && "MediaRecorder" in window && !!navigator.mediaDevices?.getUserMedia;
export const CAN_RECOGNIZE = !!Recognizer;

const MAX_MS = 30000; // hard stop
const SILENCE_MS = 1400; // quiet after speech that ends the question
const NO_SPEECH_MS = 8000; // give up if nothing was said
const SPEECH_RMS = 0.02; // loudness that counts as speech

interface Options {
  serverStt: boolean;
  /** Language to transcribe in: a code, or null to let the server detect it. */
  hint: string | null;
  /** Language for the live preview; null = no live words (an unknown language would preview as gibberish). */
  previewLang: string | null;
  onFinal: (text: string, language: string | null) => void;
  onError: (kind: "denied" | "failed") => void;
}

export function useVoice({ serverStt, hint, previewLang, onFinal, onError }: Options) {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [live, setLive] = useState("");
  const [level, setLevel] = useState(0);
  const stopRef = useRef<(() => void) | null>(null);
  const opts = useRef({ hint, previewLang, onFinal, onError });
  opts.current = { hint, previewLang, onFinal, onError };

  useEffect(() => () => stopRef.current?.(), []);

  const startPreview = (onText: (t: string) => void, continuous: boolean): any => {
    const lang = opts.current.previewLang;
    if (!Recognizer || !lang) return null;
    const rec = new Recognizer();
    rec.lang = SPEECH_LOCALE[lang] ?? "en-IN";
    rec.interimResults = true;
    rec.continuous = continuous;
    rec.onresult = (e: any) => onText(Array.from(e.results as ArrayLike<any>, (r: any) => r[0].transcript).join(" "));
    try {
      rec.start();
    } catch {
      return null;
    }
    return rec;
  };

  // Server path: record + meter + silence detection, then transcribe.
  const startServer = async () => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      setPhase("idle");
      opts.current.onError("denied");
      return;
    }
    // Start recording before anything else so the first word is not lost while the meter is being set up.
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    recorder.start();
    const ctx = new AudioContext();
    void ctx.resume(); // created after an await, so some browsers start it suspended
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const started = performance.now();
    let heard = false;
    let lastLoud = started;
    let spoke = false;
    const preview = startPreview(setLive, true);

    const meter = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      const rms = Math.sqrt(sum / buf.length);
      setLevel(Math.min(1, rms * 9));
      const now = performance.now();
      if (rms > SPEECH_RMS) {
        heard = true;
        lastLoud = now;
      }
      if ((heard && now - lastLoud > SILENCE_MS) || now - started > MAX_MS) finish(true);
      else if (!heard && now - started > NO_SPEECH_MS) finish(false);
    }, 100);

    const finish = (keep: boolean) => {
      if (recorder.state !== "recording") return;
      spoke = keep;
      window.clearInterval(meter);
      preview?.stop();
      recorder.stop();
    };
    stopRef.current = () => finish(heard);

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
      stopRef.current = null;
      setLevel(0);
      if (!spoke) {
        setPhase("idle");
        setLive("");
        opts.current.onError("failed");
        return;
      }
      setPhase("working");
      try {
        const wav = await toWav16k(new Blob(chunks, { type: recorder.mimeType }));
        const res = await api.transcribe(wav, opts.current.hint ?? undefined);
        if (res.text.trim()) opts.current.onFinal(res.text.trim(), res.language);
        else opts.current.onError("failed");
      } catch {
        opts.current.onError("failed");
      } finally {
        setPhase("idle");
        setLive("");
      }
    };
  };

  // Browser-only path: the recognizer ends by itself when the speaker pauses.
  const startBrowser = () => {
    let text = "";
    const rec = new Recognizer();
    rec.lang = SPEECH_LOCALE[opts.current.previewLang ?? opts.current.hint ?? navigator.language.slice(0, 2)] ?? "en-IN";
    rec.interimResults = true;
    rec.continuous = false;
    rec.onresult = (e: any) => {
      text = Array.from(e.results as ArrayLike<any>, (r: any) => r[0].transcript).join(" ");
      setLive(text);
      setLevel(0.6);
    };
    rec.onerror = (e: any) => opts.current.onError(e?.error === "not-allowed" ? "denied" : "failed");
    rec.onend = () => {
      stopRef.current = null;
      setPhase("idle");
      setLive("");
      setLevel(0);
      if (text.trim()) opts.current.onFinal(text.trim(), opts.current.previewLang);
    };
    stopRef.current = () => rec.stop();
    rec.start();
  };

  const start = (serverFirst = serverStt) => {
    if (phase !== "idle") return;
    setLive("");
    setPhase("listening");
    if (serverFirst && CAN_RECORD) void startServer();
    else if (Recognizer) startBrowser();
    else setPhase("idle");
  };

  const stop = () => stopRef.current?.();

  return { phase, live, level, start, stop, supported: (serverStt && CAN_RECORD) || !!Recognizer };
}

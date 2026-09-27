import { useEffect, useMemo, useRef, useState } from "react";
import { SPEECH_LOCALE, levelLabel, tr } from "../i18n";
import { useApp } from "../store";
import Icon from "./Icon";
import { CAN_RECORD, useVoice } from "./voice";
import { levelClass } from "../format";
import type { ChatResponse } from "../types";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  res?: ChatResponse;
  error?: boolean;
}

interface Props {
  messages: Message[];
  busy: boolean;
  lang: string;
  activeId: string | null;
  suggestions: string[];
  onSend: (text: string) => void;
  onSelect: (id: string) => void;
}

export default function Chat({ messages, busy, lang, activeId, suggestions, onSend, onSelect }: Props) {
  const [text, setText] = useState("");
  const [micError, setMicError] = useState<string | null>(null);
  const [voiceLang, setVoiceLang] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const { health } = useApp();
  const ui = lang === "auto" ? "en" : lang;

  // The language to preview live words in: the chosen reply language, else the one ORCA last heard or answered in.
  // Unknown (first question in Auto) → no live words, only the level meter, rather than gibberish.
  const lastAnswerLang = useMemo(() => [...messages].reverse().find((m) => m.res)?.res?.language ?? null, [messages]);
  const previewLang = lang !== "auto" ? lang : voiceLang ?? lastAnswerLang;

  const submit = (value: string) => {
    const v = value.trim();
    if (!v || busy) return;
    onSend(v);
    setText("");
  };

  const voice = useVoice({
    serverStt: CAN_RECORD && !!health?.stt?.available,
    hint: lang === "auto" ? null : lang,
    previewLang,
    onFinal: (heard, language) => {
      if (language) setVoiceLang(language);
      if (busy) setText(heard); // ORCA is still answering: keep the question ready to send
      else onSend(heard);
    },
    onError: (kind) => setMicError(tr(ui, kind === "denied" ? "micDenied" : "micError")),
  });

  useEffect(() => {
    // scroll the message list only — never the page (on phones the decision card must stay in view)
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy, voice.phase, voice.live]);

  const toggleMic = () => {
    if (voice.phase === "listening") return voice.stop();
    if (voice.phase !== "idle") return;
    setMicError(null);
    voice.start();
  };

  const speak = (m: Message) => {
    if (!("speechSynthesis" in window) || !m.res) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(m.text);
    u.lang = SPEECH_LOCALE[m.res.language] ?? "en-IN";
    window.speechSynthesis.speak(u);
  };

  return (
    <section className="chat card" aria-label="Conversation">
      <div className="messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="welcome">
            <p>
              <b>Namaste! ORCA</b> checks sea safety, fishing zones, routes, tides and official warnings — in English, हिन्दी, தமிழ்,
              తెలుగు, മലയാളം and more.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`msg ${m.role} ${m.error ? "error" : ""} ${m.id === activeId ? "active" : ""}`}
            onClick={() => m.res && onSelect(m.id)}
          >
            {m.role === "assistant" && m.res?.cards.safety && (
              <span className={`badge ${levelClass(m.res.cards.safety.risk_level)}`}>
                {levelLabel(lang === "auto" ? m.res.language : lang, m.res.cards.safety.risk_level)}
              </span>
            )}
            <div className="msg-text" lang={m.res?.language}>
              {m.text}
            </div>
            {m.role === "assistant" && m.res && (
              <div className="msg-meta small muted">
                {m.res.answer_source === "llm" ? "LLM explanation (verified)" : "template explanation"} · {m.res.language_name}
                {m.res.simulated && " · SIMULATED"}
                {"speechSynthesis" in window && (
                  <button className="linkish" onClick={(e) => (e.stopPropagation(), speak(m))}>
                    <Icon name="speaker" size={16} /> {tr(lang === "auto" ? m.res.language : lang, "listen")}
                  </button>
                )}
                {m.res.answer_note && <div className="note">{m.res.answer_note}</div>}
              </div>
            )}
          </div>
        ))}
        {voice.phase !== "idle" && (
          <div className={`msg user voice-live ${voice.phase}`} aria-live="polite">
            <div className="msg-text" lang={previewLang ?? undefined}>
              {voice.live || <span className="voice-hint">{tr(ui, voice.phase === "listening" ? "micListening" : "micWorking")}</span>}
            </div>
            <span className="vu" style={{ ["--lvl" as string]: voice.level }} aria-hidden>
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          </div>
        )}
        {busy && <div className="msg assistant pending">ORCA agents are working…</div>}
      </div>
      <div className="chips" aria-label="Suggestions">
        {suggestions.map((s) => (
          <button key={s} className="chip" disabled={busy} onClick={() => submit(s)}>
            {s}
          </button>
        ))}
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <input
          id="question"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={tr(lang === "auto" ? "en" : lang, "placeholder")}
          aria-label="Your question"
        />
        {voice.supported && (
          <button
            type="button"
            className={`icon mic-btn ${voice.phase !== "idle" ? "active" : ""}`}
            onClick={toggleMic}
            disabled={voice.phase === "working"}
            aria-label={tr(ui, "speak")}
            aria-pressed={voice.phase === "listening"}
            title={tr(ui, "speak")}
          >
            {voice.phase === "listening" ? <Icon name="stop" size={20} /> : voice.phase === "working" ? <span className="mic-busy" /> : <Icon name="mic" size={20} />}
          </button>
        )}
        <button type="submit" className="send-btn" disabled={busy || !text.trim()}>
          <Icon name="send" size={18} />
          <span>{tr(lang === "auto" ? "en" : lang, "send")}</span>
        </button>
      </form>
      {micError && voice.phase === "idle" && (
        <p className="mic-status small error" role="status">
          {micError}
        </p>
      )}
    </section>
  );
}

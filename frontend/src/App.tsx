import { useEffect, useRef, useState } from "react";

type Msg = { role: "user" | "assistant"; content: string };
type Model = { nome: string; papel: string; local: boolean };
type Health = { ok: boolean; servicos: { servico: string; ok: boolean; ms: number; detalhe: string }[] };
type Stats = { latencia_ms: number; primeiro_token_ms: number | null; usage?: { completion_tokens?: number } };

export default function App() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("chat-4b");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [showHealth, setShowHealth] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => r.json())
      .then((all: Model[]) => {
        const chat = all.filter((m) => m.papel === "chat");
        setModels(chat);
        if (chat.length && !chat.some((m) => m.nome === model)) setModel(chat[0].nome);
      })
      .catch(() => setModels([]));
    refreshHealth();
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  function refreshHealth() {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const history: Msg[] = [...msgs, { role: "user", content: text }];
    setMsgs([...history, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    setStats(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const append = (piece: string) =>
      setMsgs((cur) => {
        const copy = cur.slice();
        const last = copy[copy.length - 1];
        copy[copy.length - 1] = { ...last, content: last.content + piece };
        return copy;
      });

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, messages: history }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const ev of events) {
          const line = ev.trim();
          if (!line.startsWith("data:")) continue;
          const data = JSON.parse(line.slice(5).trim());
          if (data.delta) append(data.delta);
          if (data.error) append(`\n\n[erro] ${data.error}`);
          if (data.done) setStats(data.stats);
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") append(`\n\n[erro] ${(err as Error).message}`);
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  return (
    <div className="app">
      <header>
        <strong>Chat LLM Local</strong>
        <select value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
          {models.length === 0 && <option value={model}>{model}</option>}
          {models.map((m) => (
            <option key={m.nome} value={m.nome}>
              {m.nome}
            </option>
          ))}
        </select>
        <button
          className={`dot ${health ? (health.ok ? "ok" : "bad") : ""}`}
          title="Estado dos serviços"
          onClick={() => {
            refreshHealth();
            setShowHealth((v) => !v);
          }}
        >
          ●
        </button>
        <button onClick={() => setMsgs([])} disabled={busy}>
          Nova conversa
        </button>
      </header>

      {showHealth && health && (
        <section className="health">
          {health.servicos.map((s) => (
            <div key={s.servico} className={s.ok ? "ok" : "bad"}>
              <b>{s.servico}</b> {s.ok ? "ok" : "falhou"} · {s.ms} ms · {s.detalhe}
            </div>
          ))}
        </section>
      )}

      <main>
        {msgs.length === 0 && (
          <p className="empty">Pergunte algo. Na primeira mensagem o modelo é carregado na GPU e pode demorar.</p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.content || (busy && i === msgs.length - 1 ? "…" : "")}
          </div>
        ))}
        <div ref={endRef} />
      </main>

      {stats && (
        <div className="stats">
          primeiro token {stats.primeiro_token_ms ?? "–"} ms · total {stats.latencia_ms} ms
          {stats.usage?.completion_tokens
            ? ` · ${(stats.usage.completion_tokens / (stats.latencia_ms / 1000)).toFixed(1)} tokens/s`
            : ""}
        </div>
      )}

      <footer>
        <textarea
          value={input}
          placeholder="Escreva sua mensagem (Enter envia, Shift+Enter quebra linha)"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={2}
        />
        {busy ? (
          <button onClick={() => abortRef.current?.abort()}>Parar</button>
        ) : (
          <button onClick={send} disabled={!input.trim()}>
            Enviar
          </button>
        )}
      </footer>
    </div>
  );
}

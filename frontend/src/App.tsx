import { useEffect, useRef, useState } from "react";
import Admin from "./Admin";
import Historico from "./Historico";
import Login from "./Login";
import { api, apiFetch, limparSessao, quandoExpirar, sessaoSalva, type Usuario } from "./api";

type Msg = { role: "user" | "assistant"; content: string; id?: string; feedback?: number | null };
type Model = { nome: string; papel: string; local: boolean };
type Health = { ok: boolean; servicos: { servico: string; ok: boolean; ms: number; detalhe: string }[] };
type Stats = {
  latencia_ms: number;
  primeiro_token_ms: number | null;
  usage?: { prompt_tokens?: number; completion_tokens?: number } | null;
};
type Conversa = { id: string; titulo: string | null; criada_em: string; atualizada_em: string; perguntas: number };
type Aba = "chat" | "historico" | "admin";

export default function App() {
  const [usuario, setUsuario] = useState<Usuario | null>(() => sessaoSalva()?.usuario ?? null);

  useEffect(() => {
    quandoExpirar(() => setUsuario(null));
  }, []);

  if (!usuario) return <Login onEntrar={setUsuario} />;
  return (
    <Principal
      key={usuario.id}
      usuario={usuario}
      sair={() => {
        limparSessao();
        setUsuario(null);
      }}
    />
  );
}

function Principal({ usuario, sair }: { usuario: Usuario; sair: () => void }) {
  const admin = usuario.papel === "admin";
  const [aba, setAba] = useState<Aba>("chat");
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("chat-4b");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversas, setConversas] = useState<Conversa[]>([]);
  const [lateral, setLateral] = useState(false); // gaveta de conversas no celular
  const [editando, setEditando] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [showHealth, setShowHealth] = useState(false);
  const [showSenha, setShowSenha] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Model[]>("/api/models")
      .then((all) => {
        const chat = all.filter((m) => m.papel === "chat");
        setModels(chat);
        if (chat.length && !chat.some((m) => m.nome === model)) setModel(chat[0].nome);
      })
      .catch(() => setModels([]));
    carregarConversas();
    refreshHealth();
  }, []);

  // Chaves obrigatórias: o efeito não pode devolver o valor de scrollIntoView
  // (no Chrome novo ele é uma Promise e o React tentaria chamá-la como limpeza).
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  function refreshHealth() {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }

  function carregarConversas() {
    api<Conversa[]>("/api/conversations")
      .then(setConversas)
      .catch(() => setConversas([]));
  }

  function novaConversa() {
    setMsgs([]);
    setConversationId(null);
    setStats(null);
    setLateral(false);
  }

  async function abrirConversa(id: string) {
    if (busy) return;
    setLateral(false);
    setStats(null);
    try {
      const rows = await api<{ id: string; papel: string; conteudo: string; feedback: number | null; modelo: string | null }[]>(
        `/api/conversations/${id}/messages`,
      );
      setMsgs(
        rows
          .filter((r) => r.papel === "user" || r.papel === "assistant")
          .map((r) => ({ role: r.papel as Msg["role"], content: r.conteudo, id: r.id, feedback: r.feedback })),
      );
      setConversationId(id);
      const ultimo = [...rows].reverse().find((r) => r.modelo)?.modelo;
      if (ultimo && models.some((m) => m.nome === ultimo)) setModel(ultimo);
    } catch (e) {
      alert((e as Error).message);
    }
  }

  async function renomear(c: Conversa, titulo: string) {
    setEditando(null);
    const t = titulo.trim();
    if (!t || t === c.titulo) return;
    await api(`/api/conversations/${c.id}`, "PATCH", { titulo: t }).catch(() => undefined);
    carregarConversas();
  }

  async function arquivar(c: Conversa) {
    if (!confirm(`Arquivar a conversa "${c.titulo ?? "sem título"}"? Ela some da lista, mas continua no histórico.`)) return;
    await api(`/api/conversations/${c.id}`, "PATCH", { arquivar: true }).catch(() => undefined);
    if (c.id === conversationId) novaConversa();
    carregarConversas();
  }

  async function avaliar(i: number, nota: number) {
    const m = msgs[i];
    if (!m.id) return;
    const nova = m.feedback === nota ? 0 : nota; // clicar de novo desfaz
    try {
      await api("/api/feedback", "POST", { message_id: m.id, nota: nova });
      setMsgs((cur) => cur.map((x, j) => (j === i ? { ...x, feedback: nova || null } : x)));
    } catch (e) {
      alert((e as Error).message);
    }
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

    const patchLast = (fn: (m: Msg) => Msg) =>
      setMsgs((cur) => {
        const copy = cur.slice();
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });
    const append = (piece: string) => patchLast((m) => ({ ...m, content: m.content + piece }));

    try {
      const res = await apiFetch("/api/chat/stream", {
        method: "POST",
        body: JSON.stringify({
          model,
          messages: history.map((m) => ({ role: m.role, content: m.content })),
          conversation_id: conversationId,
        }),
        signal: ctrl.signal,
      });
      if (!res.body) throw new Error("Resposta vazia do servidor");
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
          if (data.conversation_id) {
            setConversationId(data.conversation_id);
            if (!conversationId) carregarConversas();
          }
          if (data.delta) append(data.delta);
          if (data.error) append(`\n\n[erro] ${data.error}`);
          if (data.done) {
            setStats(data.stats);
            if (data.message_id) patchLast((m) => ({ ...m, id: data.message_id }));
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") append(`\n\n[erro] ${(err as Error).message}`);
    } finally {
      setBusy(false);
      abortRef.current = null;
      setTimeout(carregarConversas, 800);
    }
  }

  const tokIn = stats?.usage?.prompt_tokens;
  const tokOut = stats?.usage?.completion_tokens;
  const geracaoMs = stats ? stats.latencia_ms - (stats.primeiro_token_ms ?? 0) : 0;

  return (
    <div className="app">
      <header>
        {aba === "chat" && (
          <button className="so-celular" onClick={() => setLateral((v) => !v)} title="Conversas">
            ☰
          </button>
        )}
        <strong>Chat LLM Local</strong>
        <nav className="tabs">
          <button className={aba === "chat" ? "on" : ""} onClick={() => setAba("chat")}>
            Chat
          </button>
          <button className={aba === "historico" ? "on" : ""} onClick={() => setAba("historico")}>
            Histórico
          </button>
          {admin && (
            <button className={aba === "admin" ? "on" : ""} onClick={() => setAba("admin")}>
              Admin
            </button>
          )}
        </nav>
        <span className="spacer" />
        {aba === "chat" && (
          <select value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
            {models.length === 0 && <option value={model}>nenhum modelo disponível</option>}
            {models.map((m) => (
              <option key={m.nome} value={m.nome}>
                {m.nome}
              </option>
            ))}
          </select>
        )}
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
        <span className="quem" title={`${usuario.login} · ${usuario.papel}`}>
          {usuario.nome}
        </span>
        <button onClick={() => setShowSenha((v) => !v)} title="Trocar minha senha">
          Senha
        </button>
        <button onClick={sair}>Sair</button>
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

      {showSenha && <TrocarSenha fechar={() => setShowSenha(false)} />}

      {aba === "historico" ? (
        <Historico admin={admin} />
      ) : aba === "admin" && admin ? (
        <Admin eu={usuario} />
      ) : (
        <div className="chat-layout">
          <aside className={`lateral ${lateral ? "aberta" : ""}`}>
            <button className="primario nova" onClick={novaConversa} disabled={busy}>
              + Nova conversa
            </button>
            <div className="lista">
              {conversas.length === 0 && <div className="vazio">Nenhuma conversa ainda.</div>}
              {conversas.map((c) => (
                <div key={c.id} className={`conv ${c.id === conversationId ? "on" : ""}`}>
                  {editando === c.id ? (
                    <input
                      className="inline"
                      defaultValue={c.titulo ?? ""}
                      autoFocus
                      onBlur={(e) => renomear(c, e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                        if (e.key === "Escape") setEditando(null);
                      }}
                    />
                  ) : (
                    <button className="conv-titulo" onClick={() => abrirConversa(c.id)} title={c.titulo ?? ""}>
                      {c.titulo || "Sem título"}
                    </button>
                  )}
                  <span className="conv-acoes">
                    <button title="Renomear" onClick={() => setEditando(c.id)}>
                      ✎
                    </button>
                    <button title="Arquivar" onClick={() => arquivar(c)}>
                      🗑
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </aside>
          {lateral && <div className="veu" onClick={() => setLateral(false)} />}

          <section className="conversa">
            <main>
              {msgs.length === 0 && (
                <p className="empty">
                  Olá, {usuario.nome.split(" ")[0]}! Pergunte algo. Na primeira mensagem o modelo é carregado na GPU e pode demorar.
                </p>
              )}
              {msgs.map((m, i) => (
                <div key={i} className={`msg ${m.role}`}>
                  {m.content || (busy && i === msgs.length - 1 ? "…" : "")}
                  {m.role === "assistant" && m.id && !(busy && i === msgs.length - 1) && (
                    <div className="aval">
                      <button className={m.feedback === 1 ? "on" : ""} title="Resposta útil" onClick={() => avaliar(i, 1)}>
                        👍
                      </button>
                      <button className={m.feedback === -1 ? "on" : ""} title="Resposta não ajudou" onClick={() => avaliar(i, -1)}>
                        👎
                      </button>
                    </div>
                  )}
                </div>
              ))}
              <div ref={endRef} />
            </main>

            {stats && (
              <div className="stats">
                1º token {stats.primeiro_token_ms ?? "–"} ms · total {stats.latencia_ms} ms
                {tokIn !== undefined ? ` · entrada ${tokIn} tokens` : ""}
                {tokOut !== undefined ? ` · saída ${tokOut} tokens` : ""}
                {tokOut && geracaoMs > 0
                  ? ` · ${(tokOut / (geracaoMs / 1000)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} tokens/s`
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
                <button className="primario" onClick={send} disabled={!input.trim() || models.length === 0}>
                  Enviar
                </button>
              )}
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

function TrocarSenha({ fechar }: { fechar: () => void }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [repete, setRepete] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null);

  async function salvar(e: React.FormEvent) {
    e.preventDefault();
    if (nova !== repete) return setMsg({ ok: false, t: "As senhas novas não conferem." });
    try {
      await api("/api/auth/trocar-senha", "POST", { senha_atual: atual, senha_nova: nova });
      setMsg({ ok: true, t: "Senha alterada." });
      setTimeout(fechar, 1200);
    } catch (err) {
      setMsg({ ok: false, t: (err as Error).message });
    }
  }

  return (
    <form className="painel-senha" onSubmit={salvar}>
      <input type="password" placeholder="Senha atual" value={atual} onChange={(e) => setAtual(e.target.value)} autoComplete="current-password" />
      <input type="password" placeholder="Nova senha (mín. 6)" value={nova} onChange={(e) => setNova(e.target.value)} autoComplete="new-password" />
      <input type="password" placeholder="Repita a nova" value={repete} onChange={(e) => setRepete(e.target.value)} autoComplete="new-password" />
      <button type="submit" className="primario" disabled={!atual || nova.length < 6}>
        Salvar
      </button>
      <button type="button" onClick={fechar}>
        Cancelar
      </button>
      {msg && <span className={msg.ok ? "ok-txt" : "erro-msg"}>{msg.t}</span>}
    </form>
  );
}

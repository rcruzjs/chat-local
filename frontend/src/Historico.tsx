import { Fragment, useEffect, useState } from "react";

type Agg = {
  perguntas: number;
  tokens_in: number;
  tokens_out: number;
  latencia_media_ms: number | null;
  latencia_p95_ms: number | null;
  primeiro_token_medio_ms: number | null;
  tokens_por_s: number | null;
};
type Metrics = {
  dias: number;
  totais: Agg & { erros: number };
  por_modelo: (Agg & { modelo: string })[];
  por_dia: { dia: string; perguntas: number; tokens_in: number; tokens_out: number }[];
  por_hora: { hora: number; perguntas: number }[];
};
type Item = {
  id: string;
  criada_em: string;
  modelo: string | null;
  pergunta: string | null;
  resposta: string;
  tokens_in: number | null;
  tokens_out: number | null;
  latencia_ms: number | null;
  primeiro_token_ms: number | null;
  tokens_por_s: number | null;
  erro: string | null;
  interrompido: boolean;
};

const PAGE = 50;
const nf = new Intl.NumberFormat("pt-BR");
const fmtNum = (v: number | null | undefined) => (v === null || v === undefined ? "–" : nf.format(Number(v)));
const fmtMs = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "–";
  const n = Number(v);
  return n >= 1000 ? `${(n / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s` : `${nf.format(n)} ms`;
};
const fmtData = (iso: string) =>
  new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

export default function Historico() {
  const [dias, setDias] = useState(30);
  const [modelo, setModelo] = useState("");
  const [busca, setBusca] = useState("");
  const [buscaAplicada, setBuscaAplicada] = useState("");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [itens, setItens] = useState<Item[]>([]);
  const [fim, setFim] = useState(false);
  const [aberto, setAberto] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  const filtroQS = () => {
    const p = new URLSearchParams();
    if (modelo) p.set("modelo", modelo);
    if (buscaAplicada) p.set("q", buscaAplicada);
    return p;
  };

  useEffect(() => {
    fetch(`/api/metrics?dias=${dias}`)
      .then((r) => r.json())
      .then(setMetrics)
      .catch(() => setMetrics(null));
  }, [dias]);

  useEffect(() => {
    carregar(true);
  }, [modelo, buscaAplicada]);

  async function carregar(reiniciar: boolean) {
    setCarregando(true);
    const p = filtroQS();
    p.set("limit", String(PAGE));
    p.set("offset", String(reiniciar ? 0 : itens.length));
    try {
      const novos: Item[] = await fetch(`/api/history?${p}`).then((r) => r.json());
      setItens((cur) => (reiniciar ? novos : [...cur, ...novos]));
      setFim(novos.length < PAGE);
    } finally {
      setCarregando(false);
    }
  }

  const t = metrics?.totais;
  const csvHref = `/api/history.csv?${filtroQS()}`;

  return (
    <div className="hist">
      <div className="filtros">
        <label>
          Período
          <select value={dias} onChange={(e) => setDias(Number(e.target.value))}>
            <option value={1}>Hoje</option>
            <option value={7}>7 dias</option>
            <option value={30}>30 dias</option>
            <option value={90}>90 dias</option>
            <option value={365}>12 meses</option>
          </select>
        </label>
        <label>
          Modelo
          <select value={modelo} onChange={(e) => setModelo(e.target.value)}>
            <option value="">Todos</option>
            {metrics?.por_modelo.map((m) => (
              <option key={m.modelo} value={m.modelo}>
                {m.modelo}
              </option>
            ))}
          </select>
        </label>
        <form
          className="busca"
          onSubmit={(e) => {
            e.preventDefault();
            setBuscaAplicada(busca.trim());
          }}
        >
          <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar nas perguntas e respostas" />
          <button type="submit">Buscar</button>
        </form>
        <a className="btn" href={csvHref}>
          Exportar CSV
        </a>
      </div>

      <div className="kpis">
        <Kpi rotulo="Perguntas" valor={fmtNum(t?.perguntas)} />
        <Kpi rotulo="Tokens de entrada" valor={fmtNum(t?.tokens_in)} />
        <Kpi rotulo="Tokens de saída" valor={fmtNum(t?.tokens_out)} />
        <Kpi rotulo="Tempo médio de resposta" valor={fmtMs(t?.latencia_media_ms)} />
        <Kpi rotulo="Tempo p95" valor={fmtMs(t?.latencia_p95_ms)} dica="95% das respostas ficam abaixo deste tempo" />
        <Kpi rotulo="1º token (médio)" valor={fmtMs(t?.primeiro_token_medio_ms)} />
        <Kpi rotulo="Velocidade média" valor={t?.tokens_por_s ? `${fmtNum(t.tokens_por_s)} tok/s` : "–"} />
        <Kpi rotulo="Erros" valor={fmtNum(t?.erros)} />
      </div>

      <div className="graficos">
        <Barras
          titulo="Perguntas por dia"
          dados={diasContinuos(metrics).map((d) => ({
            rotulo: d.dia.slice(8, 10) + "/" + d.dia.slice(5, 7),
            valor: d.perguntas,
            dica: `${d.dia.slice(8, 10)}/${d.dia.slice(5, 7)}: ${d.perguntas} perguntas · ${nf.format(d.tokens_in)} tokens entrada · ${nf.format(d.tokens_out)} saída`,
          }))}
        />
        <Barras
          titulo="Pico de demanda: perguntas por hora do dia"
          dados={Array.from({ length: 24 }, (_, h) => {
            const v = metrics?.por_hora.find((x) => x.hora === h)?.perguntas ?? 0;
            return { rotulo: String(h).padStart(2, "0"), valor: v, dica: `${h}h: ${v} perguntas` };
          })}
        />
      </div>

      <h3>Por modelo</h3>
      <div className="tabela-wrap">
        <table>
          <thead>
            <tr>
              <th>Modelo</th>
              <th className="n">Perguntas</th>
              <th className="n">Tokens entrada</th>
              <th className="n">Tokens saída</th>
              <th className="n">Tempo médio</th>
              <th className="n">Tempo p95</th>
              <th className="n">1º token</th>
              <th className="n">tok/s</th>
            </tr>
          </thead>
          <tbody>
            {(metrics?.por_modelo ?? []).map((m) => (
              <tr key={m.modelo}>
                <td>{m.modelo}</td>
                <td className="n">{fmtNum(m.perguntas)}</td>
                <td className="n">{fmtNum(m.tokens_in)}</td>
                <td className="n">{fmtNum(m.tokens_out)}</td>
                <td className="n">{fmtMs(m.latencia_media_ms)}</td>
                <td className="n">{fmtMs(m.latencia_p95_ms)}</td>
                <td className="n">{fmtMs(m.primeiro_token_medio_ms)}</td>
                <td className="n">{fmtNum(m.tokens_por_s)}</td>
              </tr>
            ))}
            {metrics && metrics.por_modelo.length === 0 && (
              <tr>
                <td colSpan={8} className="vazio">
                  Sem perguntas no período.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <h3>Histórico de perguntas</h3>
      <div className="tabela-wrap">
        <table className="historico">
          <thead>
            <tr>
              <th>Data/hora</th>
              <th>Modelo</th>
              <th>Pergunta</th>
              <th className="n">Entrada</th>
              <th className="n">Saída</th>
              <th className="n">Tempo</th>
              <th className="n">1º token</th>
              <th className="n">tok/s</th>
            </tr>
          </thead>
          <tbody>
            {itens.map((it) => (
              <Fragment key={it.id}>
                <tr className={`linha ${aberto === it.id ? "aberta" : ""}`} onClick={() => setAberto(aberto === it.id ? null : it.id)}>
                  <td className="data">{fmtData(it.criada_em)}</td>
                  <td>{it.modelo ?? "–"}</td>
                  <td className="perg">
                    {it.erro ? <span className="tag erro">erro</span> : null}
                    {it.interrompido ? <span className="tag">interrompida</span> : null}
                    {it.pergunta ?? ""}
                  </td>
                  <td className="n">{fmtNum(it.tokens_in)}</td>
                  <td className="n">{fmtNum(it.tokens_out)}</td>
                  <td className="n">{fmtMs(it.latencia_ms)}</td>
                  <td className="n">{fmtMs(it.primeiro_token_ms)}</td>
                  <td className="n">{fmtNum(it.tokens_por_s)}</td>
                </tr>
                {aberto === it.id && (
                  <tr className="detalhe">
                    <td colSpan={8}>
                      <div className="rotulo">Pergunta</div>
                      <div className="texto">{it.pergunta}</div>
                      <div className="rotulo">Resposta</div>
                      <div className="texto">{it.erro ? `[erro] ${it.erro}` : it.resposta || "(vazia)"}</div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {!carregando && itens.length === 0 && (
              <tr>
                <td colSpan={8} className="vazio">
                  Nenhuma pergunta encontrada. As perguntas feitas a partir desta versão aparecem aqui.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!fim && itens.length > 0 && (
        <button className="mais" onClick={() => carregar(false)} disabled={carregando}>
          {carregando ? "Carregando…" : "Carregar mais"}
        </button>
      )}
    </div>
  );
}

function Kpi({ rotulo, valor, dica }: { rotulo: string; valor: string; dica?: string }) {
  return (
    <div className="kpi" title={dica}>
      <div className="kpi-valor">{valor}</div>
      <div className="kpi-rotulo">{rotulo}</div>
    </div>
  );
}

function Barras({ titulo, dados }: { titulo: string; dados: { rotulo: string; valor: number; dica: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, H = 160, padL = 32, padB = 22, padT = 8;
  const max = Math.max(1, ...dados.map((d) => d.valor));
  const passo = dados.length ? (W - padL) / dados.length : 0;
  const larg = Math.max(2, Math.min(passo - 2, 24));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const cadaRotulo = Math.ceil(dados.length / 12);
  return (
    <figure className="grafico">
      <figcaption>{titulo}</figcaption>
      {dados.length === 0 ? (
        <div className="vazio">Sem dados no período.</div>
      ) : (
        <div className="svg-wrap">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={titulo}>
            {[0, 0.5, 1].map((f) => (
              <g key={f}>
                <line x1={padL} x2={W} y1={y(max * f)} y2={y(max * f)} className="grade" />
                <text x={padL - 6} y={y(max * f) + 4} textAnchor="end" className="eixo">
                  {Math.round(max * f)}
                </text>
              </g>
            ))}
            {dados.map((d, i) => {
              const x = padL + i * passo + (passo - larg) / 2;
              const h = H - padB - y(d.valor);
              return (
                <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  <rect x={padL + i * passo} y={padT} width={passo} height={H - padT - padB} fill="transparent" />
                  {d.valor > 0 && (
                    <rect x={x} y={y(d.valor)} width={larg} height={Math.max(h, 2)} rx={Math.min(4, larg / 2)} className={`barra ${hover === i ? "ativa" : ""}`} />
                  )}
                  {i % cadaRotulo === 0 && (
                    <text x={x + larg / 2} y={H - 6} textAnchor="middle" className="eixo">
                      {d.rotulo}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          {hover !== null && <div className="tooltip">{dados[hover].dica}</div>}
        </div>
      )}
    </figure>
  );
}

// Preenche os dias sem perguntas com zero, para o eixo do tempo ficar contínuo (até 90 dias)
function diasContinuos(m: Metrics | null) {
  if (!m) return [];
  const mapa = new Map(m.por_dia.map((d) => [d.dia, d]));
  const n = Math.min(m.dias, 90);
  const hoje = new Date();
  const out: Metrics["por_dia"] = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate() - i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    out.push(mapa.get(iso) ?? { dia: iso, perguntas: 0, tokens_in: 0, tokens_out: 0 });
  }
  return m.dias > 90 ? m.por_dia : out;
}

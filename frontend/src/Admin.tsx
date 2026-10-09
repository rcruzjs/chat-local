import { useEffect, useState } from "react";
import { api, type Usuario } from "./api";

type UsuarioAdm = {
  id: string;
  login: string;
  nome: string;
  papel: Usuario["papel"];
  ativo: boolean;
  criada_em: string;
  perguntas: number;
  ultimo_uso: string | null;
};
type ModeloAdm = { nome: string; papel: string; local: boolean; contexto_max: number; ativo: boolean; baixado: boolean };

const PAPEIS: { v: Usuario["papel"]; t: string }[] = [
  { v: "usuario", t: "Usuário" },
  { v: "curador", t: "Curador" },
  { v: "admin", t: "Administrador" },
];
const fmtData = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";

export default function Admin({ eu }: { eu: Usuario }) {
  const [usuarios, setUsuarios] = useState<UsuarioAdm[]>([]);
  const [modelos, setModelos] = useState<ModeloAdm[]>([]);
  const [aviso, setAviso] = useState<{ ok: boolean; texto: string } | null>(null);
  const [novo, setNovo] = useState({ login: "", nome: "", senha: "", papel: "usuario" as Usuario["papel"] });
  const [senhaDe, setSenhaDe] = useState<string | null>(null);
  const [novaSenha, setNovaSenha] = useState("");

  const carregar = () => {
    api<UsuarioAdm[]>("/api/admin/users").then(setUsuarios).catch((e) => avisar(false, e.message));
    api<ModeloAdm[]>("/api/admin/models").then(setModelos).catch((e) => avisar(false, e.message));
  };
  useEffect(carregar, []);

  function avisar(ok: boolean, texto: string) {
    setAviso({ ok, texto });
    setTimeout(() => setAviso(null), 4000);
  }

  async function editar(u: UsuarioAdm, dados: Partial<{ nome: string; papel: string; ativo: boolean; senha: string }>, msg: string) {
    try {
      await api(`/api/admin/users/${u.id}`, "PATCH", dados);
      avisar(true, msg);
      carregar();
    } catch (e) {
      avisar(false, (e as Error).message);
    }
  }

  async function criar(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/api/admin/users", "POST", { ...novo, login: novo.login.trim(), nome: novo.nome.trim() });
      avisar(true, `Usuário ${novo.login} criado.`);
      setNovo({ login: "", nome: "", senha: "", papel: "usuario" });
      carregar();
    } catch (err) {
      avisar(false, (err as Error).message);
    }
  }

  async function alternarModelo(m: ModeloAdm) {
    try {
      await api(`/api/admin/models/${m.nome}`, "PATCH", { ativo: !m.ativo });
      carregar();
    } catch (e) {
      avisar(false, (e as Error).message);
    }
  }

  return (
    <div className="hist admin">
      {aviso && <div className={`aviso ${aviso.ok ? "ok" : "bad"}`}>{aviso.texto}</div>}

      <h3>Novo usuário</h3>
      <form className="filtros" onSubmit={criar}>
        <label>
          Login
          <input value={novo.login} onChange={(e) => setNovo({ ...novo, login: e.target.value })} placeholder="ex.: maria.silva" autoCapitalize="none" />
        </label>
        <label>
          Nome
          <input value={novo.nome} onChange={(e) => setNovo({ ...novo, nome: e.target.value })} placeholder="Maria Silva" />
        </label>
        <label>
          Senha (mín. 6)
          <input value={novo.senha} onChange={(e) => setNovo({ ...novo, senha: e.target.value })} autoComplete="new-password" />
        </label>
        <label>
          Perfil
          <select value={novo.papel} onChange={(e) => setNovo({ ...novo, papel: e.target.value as Usuario["papel"] })}>
            {PAPEIS.map((p) => (
              <option key={p.v} value={p.v}>
                {p.t}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primario" disabled={novo.login.trim().length < 3 || !novo.nome.trim() || novo.senha.length < 6}>
          Criar
        </button>
      </form>

      <h3>Usuários ({usuarios.length})</h3>
      <div className="tabela-wrap">
        <table>
          <thead>
            <tr>
              <th>Login</th>
              <th>Nome</th>
              <th>Perfil</th>
              <th>Situação</th>
              <th className="n">Perguntas</th>
              <th>Último uso</th>
              <th>Senha</th>
            </tr>
          </thead>
          <tbody>
            {usuarios.map((u) => {
              const souEu = u.id === eu.id;
              return (
                <tr key={u.id} className={u.ativo ? "" : "inativo"}>
                  <td>
                    <b>{u.login}</b>
                    {souEu && <span className="tag">você</span>}
                  </td>
                  <td>
                    <input
                      className="inline"
                      defaultValue={u.nome}
                      onBlur={(e) => e.target.value.trim() && e.target.value !== u.nome && editar(u, { nome: e.target.value.trim() }, "Nome alterado.")}
                    />
                  </td>
                  <td>
                    <select value={u.papel} disabled={souEu} onChange={(e) => editar(u, { papel: e.target.value }, `Perfil de ${u.login} alterado.`)}>
                      {PAPEIS.map((p) => (
                        <option key={p.v} value={p.v}>
                          {p.t}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button
                      className={`chip ${u.ativo ? "ok" : "bad"}`}
                      disabled={souEu}
                      title={u.ativo ? "Clique para desativar" : "Clique para reativar"}
                      onClick={() => editar(u, { ativo: !u.ativo }, `${u.login} ${u.ativo ? "desativado" : "reativado"}.`)}
                    >
                      {u.ativo ? "Ativo" : "Inativo"}
                    </button>
                  </td>
                  <td className="n">{u.perguntas}</td>
                  <td className="data">{fmtData(u.ultimo_uso)}</td>
                  <td>
                    {senhaDe === u.id ? (
                      <form
                        className="senha-inline"
                        onSubmit={(e) => {
                          e.preventDefault();
                          editar(u, { senha: novaSenha }, `Senha de ${u.login} redefinida.`);
                          setSenhaDe(null);
                          setNovaSenha("");
                        }}
                      >
                        <input className="inline" value={novaSenha} onChange={(e) => setNovaSenha(e.target.value)} placeholder="nova senha" autoFocus />
                        <button type="submit" disabled={novaSenha.length < 6}>
                          Salvar
                        </button>
                        <button type="button" onClick={() => setSenhaDe(null)}>
                          ✕
                        </button>
                      </form>
                    ) : (
                      <button onClick={() => (setSenhaDe(u.id), setNovaSenha(""))}>Redefinir</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>Modelos</h3>
      <p className="nota">Só aparecem no chat os modelos de conversa que estão <b>ativos</b> e <b>baixados</b> (arquivo .gguf na pasta models).</p>
      <div className="tabela-wrap">
        <table>
          <thead>
            <tr>
              <th>Modelo</th>
              <th>Tipo</th>
              <th className="n">Contexto</th>
              <th>Arquivo</th>
              <th>Ativo</th>
            </tr>
          </thead>
          <tbody>
            {modelos.map((m) => (
              <tr key={m.nome}>
                <td>
                  <b>{m.nome}</b>
                </td>
                <td>{m.papel}</td>
                <td className="n">{m.contexto_max.toLocaleString("pt-BR")}</td>
                <td>{m.local ? (m.baixado ? <span className="tag ok">baixado</span> : <span className="tag erro">não baixado</span>) : <span className="tag">nuvem</span>}</td>
                <td>
                  <button className={`chip ${m.ativo ? "ok" : "bad"}`} onClick={() => alternarModelo(m)}>
                    {m.ativo ? "Ativo" : "Inativo"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

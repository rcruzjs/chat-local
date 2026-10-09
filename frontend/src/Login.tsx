import { useState } from "react";
import { api, salvarSessao, type Usuario } from "./api";

export default function Login({ onEntrar }: { onEntrar: (u: Usuario) => void }) {
  const [login, setLogin] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState("");
  const [busy, setBusy] = useState(false);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setErro("");
    setBusy(true);
    try {
      const r = await api<{ token: string; usuario: Usuario }>("/api/auth/login", "POST", { login: login.trim(), senha });
      salvarSessao(r.token, r.usuario);
      onEntrar(r.usuario);
    } catch (err) {
      setErro((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-tela">
      <form className="login-caixa" onSubmit={entrar}>
        <h1>Chat LLM Local</h1>
        <label>
          Usuário
          <input value={login} onChange={(e) => setLogin(e.target.value)} autoFocus autoComplete="username" autoCapitalize="none" />
        </label>
        <label>
          Senha
          <input type="password" value={senha} onChange={(e) => setSenha(e.target.value)} autoComplete="current-password" />
        </label>
        {erro && <div className="erro-msg">{erro}</div>}
        <button type="submit" className="primario" disabled={busy || !login.trim() || !senha}>
          {busy ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}

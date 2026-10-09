// Acesso à API com o token de login. O token fica no navegador (localStorage) por até 12 horas.
export type Usuario = { id: string; nome: string; login: string; papel: "admin" | "curador" | "usuario" };

const CHAVE = "chatlocal.sessao";
let aoExpirar: (() => void) | null = null;

export function sessaoSalva(): { token: string; usuario: Usuario } | null {
  try {
    const s = localStorage.getItem(CHAVE);
    return s ? JSON.parse(s) : null;
  } catch {
    return null;
  }
}

export function salvarSessao(token: string, usuario: Usuario) {
  try {
    localStorage.setItem(CHAVE, JSON.stringify({ token, usuario }));
  } catch {
    /* navegador sem armazenamento: a sessão dura até recarregar a página */
  }
  tokenMemoria = token;
}

export function limparSessao() {
  try {
    localStorage.removeItem(CHAVE);
  } catch {
    /* ignore */
  }
  tokenMemoria = null;
}

let tokenMemoria: string | null = sessaoSalva()?.token ?? null;

export function quandoExpirar(fn: () => void) {
  aoExpirar = fn;
}

export class ErroApi extends Error {
  status: number;
  constructor(status: number, msg: string) {
    super(msg);
    this.status = status;
  }
}

async function mensagemDeErro(r: Response): Promise<string> {
  try {
    const d = await r.json();
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail)) return d.detail.map((x: { msg: string }) => x.msg).join("; ");
  } catch {
    /* corpo não é JSON */
  }
  return `Erro ${r.status}`;
}

export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (tokenMemoria) headers.set("Authorization", `Bearer ${tokenMemoria}`);
  if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const r = await fetch(url, { ...init, headers });
  if (r.status === 401 && !url.startsWith("/api/auth/login")) {
    limparSessao();
    aoExpirar?.();
  }
  if (!r.ok) throw new ErroApi(r.status, await mensagemDeErro(r));
  return r;
}

export async function api<T = unknown>(url: string, metodo = "GET", corpo?: unknown): Promise<T> {
  const r = await apiFetch(url, { method: metodo, body: corpo === undefined ? undefined : JSON.stringify(corpo) });
  return r.json();
}

// Baixa um arquivo protegido por login (ex.: CSV do histórico)
export async function baixar(url: string, nomeArquivo: string) {
  const r = await apiFetch(url);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nomeArquivo;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

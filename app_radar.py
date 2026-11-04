#!/usr/bin/env python3
"""
Aplicativo Radar Fiscal Cerbras
--------------------------------
Interface local para editar conteúdo, checar fontes oficiais,
gerar atualizacoes.json e abrir o dashboard.

Uso:
  python3 app_radar.py
  # Abre http://127.0.0.1:8765

Só usa biblioteca padrão do Python (sem pip).
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DIR = Path(__file__).resolve().parent
HTML_RADAR = DIR / "radar-fiscal-cerbras.html"
JSON_PATH = DIR / "atualizacoes.json"
HOST = "127.0.0.1"
PORT = 8765
BRT = timezone(timedelta(hours=-3))
USER_AGENT = "Mozilla/5.0 (compatible; RadarFiscalCerbrasApp/1.0)"

URLS_MONITORADAS = [
    {
        "id": "portal_rt",
        "url": "https://www.reformatributaria.com/",
        "label": "Portal da Reforma Tributária",
    },
    {
        "id": "rfb_curso_rtc",
        "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria-do-consumo/curso",
        "label": "RFB — Curso RTC",
    },
    {
        "id": "mf_portal",
        "url": "https://www.gov.br/fazenda/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria",
        "label": "Ministério da Fazenda",
    },
    {
        "id": "nfe_portal",
        "url": "https://www.nfe.fazenda.gov.br",
        "label": "Portal NF-e",
    },
    {
        "id": "cfc",
        "url": "https://cfc.org.br",
        "label": "CFC",
    },
]

DEFAULT_DATA = {
    "gerado_em": "",
    "versao": "1.1",
    "revisao_radar": "",
    "briefing": {
        "titulo": "Briefing do dia — 2 minutos",
        "bullets": [
            "Ano-teste em curso: CBS 0,9% + IBS 0,1% com destaque nas notas; sem recolhimento se obrigações forem cumpridas.",
            "Senado até 15/12: alíquota de referência da CBS (cálculo TCU 11,3%) ainda não fixada — impacto direto em preço e margem 2027.",
            "NTs críticas ativas: NT 2025.002, 2026.008 e tabela cClassTrib definem leiaute e créditos — prioridade de validação no emissor.",
        ],
        "acao_prioritaria": (
            "Validar se NF-e/NFC-e/CT-e da Cerbras estão saindo com CST e cClassTrib corretos "
            "e se o time fiscal/contábil já leu a Orientação Técnica CFC 01/2026."
        ),
    },
    "alertas": [
        {
            "tipo": "atencao",
            "titulo": "Alíquota CBS — Senado",
            "texto": "TCU enviou cálculo de 11,3%. Senado tem até 15/12 para fixar a alíquota de referência de 2027.",
            "prazo": "15 de dezembro de 2026",
        },
        {
            "tipo": "critico",
            "titulo": "Campos obrigatórios nas notas",
            "texto": "NF-e, NFC-e e CT-e já exigem destaque de CBS/IBS. NFS-e (maioria) desde 01/10/2026.",
            "prazo": "Já em vigor — validar na Cerbras",
        },
        {
            "tipo": "info",
            "titulo": "Ato Técnico Conjunto nº 10/2026",
            "texto": "Pacote de NTs (2025.002, 2026.006, 2026.008, cClassTrib v1.71) aprovado em 28/10.",
            "prazo": "Publicado no DOU · 29/10/2026",
        },
    ],
    "prazos": [
        {
            "quando": "Contínuo",
            "urgencia": "urgente",
            "titulo": "Validação diária de NF-e / NFC-e / CT-e",
            "detalhe": "CST, cClassTrib e destaque de CBS/IBS",
        },
        {
            "quando": "15/12/2026",
            "urgencia": "normal",
            "titulo": "Senado — alíquota de referência da CBS",
            "detalhe": "Prazo para deliberar o cálculo do TCU (11,3468%)",
        },
        {
            "quando": "31/12/2026",
            "urgencia": "normal",
            "titulo": "Estoque e créditos na virada do ano",
            "detalhe": "Inventário e análise de crédito presumido de CBS",
        },
        {
            "quando": "01/01/2027",
            "urgencia": "normal",
            "titulo": "Início da CBS integral",
            "detalhe": "PIS/Cofins extintos; CBS com alíquota de referência",
        },
    ],
    "noticias": [
        {
            "data": "02/11/2026",
            "titulo": "CBS de 11,3% surpreende empresas e pressiona preços, dizem especialistas",
            "fonte": "Portal da Reforma Tributária",
            "url": "https://www.reformatributaria.com/iva/aliquota-de-113-da-cbs-surpreende-empresas-e-pressiona-precos-dizem-especialistas/",
        },
        {
            "data": "28/10/2026",
            "titulo": "TCU propõe CBS de 11,3% em 2027 (11,3468%)",
            "fonte": "Portal da Reforma Tributária",
            "url": "https://www.reformatributaria.com/iva/urgente-novo-tributo-cbs-sera-de-1123-em-2027-propoe-tcu/",
        },
        {
            "data": "30/10/2026",
            "titulo": "NT 2026.005 atualiza leiautes de DF-e para o Simples Nacional",
            "fonte": "Portal da Reforma Tributária",
            "url": "https://www.reformatributaria.com/documentos-fiscais/reforma-tributaria-nota-tecnica-atualiza-leiautes-de-documentos-fiscais-para-o-simples-nacional/",
        },
        {
            "data": "27/10/2026",
            "titulo": "MP do Imposto Seletivo “provavelmente” sai esta semana, diz Fazenda",
            "fonte": "Portal da Reforma Tributária",
            "url": "https://www.reformatributaria.com/impostoseletivo/mp-do-imposto-seletivo-provavelmente-sai-esta-semana-diz-ministro-da-fazenda/",
        },
    ],
    "nts_criticas_lucro_real": [
        "NT 2025.002 — Adequações NF-e/NFC-e (CST, cClassTrib, destaque CBS/IBS)",
        "NT 2026.008 — Valor líquido do produto (lógica por fora a partir de 2027)",
        "Tabela cClassTrib + Informe Técnico 2025.002 v1.71",
        "Orientação Técnica CFC nº 01/2026 — aspectos contábeis no ano-teste",
    ],
}


def agora_brt() -> datetime:
    return datetime.now(BRT)


def revisao_label() -> str:
    meses = {
        1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
        7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
    }
    d = agora_brt()
    return f"{d.day:02d} {meses[d.month]}/{d.year}"


def load_data() -> dict:
    if JSON_PATH.exists():
        try:
            return json.loads(JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = json.loads(json.dumps(DEFAULT_DATA))
    data["gerado_em"] = agora_brt().isoformat(timespec="seconds")
    data["revisao_radar"] = revisao_label()
    return data


def save_data(data: dict) -> None:
    data["gerado_em"] = agora_brt().isoformat(timespec="seconds")
    data["revisao_radar"] = data.get("revisao_radar") or revisao_label()
    data["versao"] = data.get("versao") or "1.1"
    JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_title(url: str, timeout: int = 15) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            title = re.sub(r"&[#\w]+;", " ", title)
            return True, title[:120]
        return True, "(sem título)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]


def checar_fontes() -> list[dict]:
    out = []
    for item in URLS_MONITORADAS:
        ok, info = fetch_title(item["url"])
        out.append(
            {
                "id": item["id"],
                "label": item["label"],
                "url": item["url"],
                "ok": ok,
                "titulo_pagina": info if ok else None,
                "erro": None if ok else info,
                "verificado_em": agora_brt().isoformat(timespec="seconds"),
            }
        )
    return out


def admin_page() -> bytes:
    data = load_data()
    briefing = data.get("briefing", {})
    bullets = "\n".join(briefing.get("bullets") or [])
    nts = "\n".join(data.get("nts_criticas_lucro_real") or [])
    noticias = data.get("noticias") or []
    alertas = data.get("alertas") or []
    prazos = data.get("prazos") or []

    noticias_json = json.dumps(noticias, ensure_ascii=False)
    alertas_json = json.dumps(alertas, ensure_ascii=False)
    prazos_json = json.dumps(prazos, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>App Radar Fiscal · Cerbras</title>
  <style>
    :root {{
      --verde: #00A651;
      --verde-escuro: #0B3D2E;
      --fundo: #F4F6F5;
      --borda: #E0E5E2;
      --texto: #1a1a1a;
      --cinza: #667085;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--fundo);
      color: var(--texto);
      line-height: 1.45;
    }}
    header {{
      background: linear-gradient(135deg, var(--verde-escuro), var(--verde));
      color: #fff;
      padding: 1.1rem 1.5rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
    }}
    header h1 {{ font-size: 1.15rem; font-weight: 600; }}
    header p {{ font-size: 0.8rem; opacity: 0.85; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 0.5rem; }}
    .btn {{
      border: none;
      border-radius: 8px;
      padding: 0.55rem 0.9rem;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      font-family: inherit;
    }}
    .btn-primary {{ background: #fff; color: var(--verde-escuro); }}
    .btn-secondary {{ background: rgba(255,255,255,0.18); color: #fff; border: 1px solid rgba(255,255,255,0.35); }}
    .btn-ok {{ background: var(--verde); color: #fff; }}
    .btn:disabled {{ opacity: 0.6; cursor: wait; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }}
    .status {{
      background: #fff;
      border: 1px solid var(--borda);
      border-radius: 10px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
      font-size: 0.85rem;
      color: var(--cinza);
    }}
    .status strong {{ color: var(--verde-escuro); }}
    .card {{
      background: #fff;
      border: 1px solid var(--borda);
      border-radius: 12px;
      padding: 1.1rem 1.2rem;
      margin-bottom: 1rem;
    }}
    .card h2 {{
      font-size: 0.95rem;
      color: var(--verde-escuro);
      margin-bottom: 0.75rem;
    }}
    label {{
      display: block;
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--cinza);
      margin-bottom: 0.3rem;
      margin-top: 0.65rem;
    }}
    input, textarea, select {{
      width: 100%;
      border: 1px solid var(--borda);
      border-radius: 8px;
      padding: 0.55rem 0.7rem;
      font-size: 0.88rem;
      font-family: inherit;
    }}
    textarea {{ min-height: 90px; resize: vertical; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }}
    @media (max-width: 700px) {{ .row {{ grid-template-columns: 1fr; }} }}
    .item {{
      border: 1px dashed var(--borda);
      border-radius: 8px;
      padding: 0.75rem;
      margin-bottom: 0.55rem;
      background: #FAFBFA;
    }}
    .item-actions {{ margin-top: 0.45rem; }}
    .item-actions button {{
      font-size: 0.75rem;
      padding: 0.3rem 0.55rem;
      margin-right: 0.35rem;
    }}
    .btn-danger {{ background: #FFEBEE; color: #C62828; border: none; border-radius: 6px; cursor: pointer; }}
    .btn-add {{ background: #E8F5E9; color: var(--verde-escuro); border: none; border-radius: 6px; cursor: pointer; padding: 0.4rem 0.7rem; font-size: 0.8rem; font-weight: 600; }}
    .toast {{
      position: fixed; bottom: 1.25rem; right: 1.25rem;
      background: var(--verde-escuro); color: #fff;
      padding: 0.75rem 1rem; border-radius: 10px;
      font-size: 0.85rem; display: none; z-index: 50;
      box-shadow: 0 8px 24px rgba(0,0,0,0.15);
    }}
    .toast.show {{ display: block; }}
    .fontes {{ font-size: 0.8rem; }}
    .fontes li {{ margin: 0.35rem 0; }}
    .ok {{ color: #2E7D32; }}
    .falha {{ color: #C62828; }}
    footer {{ text-align: center; font-size: 0.75rem; color: var(--cinza); padding: 1rem; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>App Radar Fiscal · Cerbras</h1>
      <p>Edite o conteúdo, atualize fontes e abra o dashboard</p>
    </div>
    <div class="actions">
      <button class="btn btn-secondary" onclick="window.open('/radar','_blank')">Abrir dashboard</button>
      <button class="btn btn-secondary" id="btnFontes" onclick="checarFontes()">Checar fontes</button>
      <button class="btn btn-primary" id="btnSalvar" onclick="salvar()">Salvar JSON</button>
    </div>
  </header>

  <main>
    <div class="status" id="statusBox">
      Última geração: <strong id="stGerado">{data.get("gerado_em") or "—"}</strong>
      · Revisão: <strong id="stRevisao">{data.get("revisao_radar") or "—"}</strong>
      · Arquivo: <code>atualizacoes.json</code>
    </div>

    <div class="card">
      <h2>⚡ Briefing do dia</h2>
      <label>Bullets (um por linha)</label>
      <textarea id="bullets">{bullets}</textarea>
      <label>Ação prioritária</label>
      <textarea id="acao" style="min-height:60px">{briefing.get("acao_prioritaria") or ""}</textarea>
      <label>Rótulo de revisão (ex.: 03 nov/2026)</label>
      <input id="revisao" value="{data.get("revisao_radar") or ""}" />
    </div>

    <div class="card">
      <h2>🚨 Alertas</h2>
      <div id="listaAlertas"></div>
      <button type="button" class="btn-add" onclick="addAlerta()">+ Alerta</button>
    </div>

    <div class="card">
      <h2>📅 Prazos</h2>
      <div id="listaPrazos"></div>
      <button type="button" class="btn-add" onclick="addPrazo()">+ Prazo</button>
    </div>

    <div class="card">
      <h2>📰 Notícias</h2>
      <div id="listaNoticias"></div>
      <button type="button" class="btn-add" onclick="addNoticia()">+ Notícia</button>
    </div>

    <div class="card">
      <h2>⚠️ NTs críticas (Lucro Real)</h2>
      <label>Uma por linha</label>
      <textarea id="nts">{nts}</textarea>
    </div>

    <div class="card">
      <h2>🔗 Monitoramento de fontes</h2>
      <p style="font-size:0.8rem;color:var(--cinza);margin-bottom:0.5rem;">Clique em “Checar fontes” para testar os portais oficiais.</p>
      <ul class="fontes" id="listaFontes"><li>Ainda não verificado nesta sessão.</li></ul>
    </div>
  </main>

  <footer>Feito por Caio Nadson · Radar Fiscal Cerbras · App local (só Python padrão)</footer>
  <div class="toast" id="toast"></div>

  <script>
    let alertas = {alertas_json};
    let prazos = {prazos_json};
    let noticias = {noticias_json};

    function toast(msg) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2800);
    }}

    function esc(s) {{
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
    }}

    function renderAlertas() {{
      const box = document.getElementById('listaAlertas');
      box.innerHTML = alertas.map((a, i) => `
        <div class="item">
          <div class="row">
            <div>
              <label>Tipo</label>
              <select onchange="alertas[${{i}}].tipo=this.value">
                <option value="critico" ${{a.tipo==='critico'?'selected':''}}>Crítico</option>
                <option value="atencao" ${{a.tipo==='atencao'?'selected':''}}>Atenção</option>
                <option value="info" ${{a.tipo==='info'?'selected':''}}>Info</option>
              </select>
            </div>
            <div>
              <label>Prazo / etiqueta</label>
              <input value="${{esc(a.prazo)}}" onchange="alertas[${{i}}].prazo=this.value" />
            </div>
          </div>
          <label>Título</label>
          <input value="${{esc(a.titulo)}}" onchange="alertas[${{i}}].titulo=this.value" />
          <label>Texto</label>
          <textarea style="min-height:54px" onchange="alertas[${{i}}].texto=this.value">${{esc(a.texto)}}</textarea>
          <div class="item-actions"><button class="btn-danger" onclick="alertas.splice(${{i}},1);renderAlertas()">Remover</button></div>
        </div>`).join('') || '<p style="font-size:0.85rem;color:#667085">Nenhum alerta.</p>';
    }}

    function renderPrazos() {{
      const box = document.getElementById('listaPrazos');
      box.innerHTML = prazos.map((p, i) => `
        <div class="item">
          <div class="row">
            <div>
              <label>Quando</label>
              <input value="${{esc(p.quando)}}" onchange="prazos[${{i}}].quando=this.value" />
            </div>
            <div>
              <label>Urgência</label>
              <select onchange="prazos[${{i}}].urgencia=this.value">
                <option value="urgente" ${{p.urgencia==='urgente'?'selected':''}}>Urgente</option>
                <option value="atencao" ${{p.urgencia==='atencao'?'selected':''}}>Atenção</option>
                <option value="normal" ${{p.urgencia==='normal'?'selected':''}}>Normal</option>
              </select>
            </div>
          </div>
          <label>Título</label>
          <input value="${{esc(p.titulo)}}" onchange="prazos[${{i}}].titulo=this.value" />
          <label>Detalhe</label>
          <input value="${{esc(p.detalhe)}}" onchange="prazos[${{i}}].detalhe=this.value" />
          <div class="item-actions"><button class="btn-danger" onclick="prazos.splice(${{i}},1);renderPrazos()">Remover</button></div>
        </div>`).join('') || '<p style="font-size:0.85rem;color:#667085">Nenhum prazo.</p>';
    }}

    function renderNoticias() {{
      const box = document.getElementById('listaNoticias');
      box.innerHTML = noticias.map((n, i) => `
        <div class="item">
          <div class="row">
            <div>
              <label>Data</label>
              <input value="${{esc(n.data)}}" onchange="noticias[${{i}}].data=this.value" />
            </div>
            <div>
              <label>Fonte</label>
              <input value="${{esc(n.fonte)}}" onchange="noticias[${{i}}].fonte=this.value" />
            </div>
          </div>
          <label>Título</label>
          <input value="${{esc(n.titulo)}}" onchange="noticias[${{i}}].titulo=this.value" />
          <label>URL</label>
          <input value="${{esc(n.url)}}" onchange="noticias[${{i}}].url=this.value" />
          <div class="item-actions"><button class="btn-danger" onclick="noticias.splice(${{i}},1);renderNoticias()">Remover</button></div>
        </div>`).join('') || '<p style="font-size:0.85rem;color:#667085">Nenhuma notícia.</p>';
    }}

    function addAlerta() {{
      alertas.push({{ tipo: 'info', titulo: '', texto: '', prazo: '' }});
      renderAlertas();
    }}
    function addPrazo() {{
      prazos.push({{ quando: '', urgencia: 'normal', titulo: '', detalhe: '' }});
      renderPrazos();
    }}
    function addNoticia() {{
      noticias.push({{ data: '', titulo: '', fonte: '', url: '' }});
      renderNoticias();
    }}

    function montarPayload() {{
      const bullets = document.getElementById('bullets').value.split('\\n').map(s => s.trim()).filter(Boolean);
      const nts = document.getElementById('nts').value.split('\\n').map(s => s.trim()).filter(Boolean);
      return {{
        versao: '1.1',
        revisao_radar: document.getElementById('revisao').value.trim(),
        briefing: {{
          titulo: 'Briefing do dia — 2 minutos',
          bullets,
          acao_prioritaria: document.getElementById('acao').value.trim()
        }},
        alertas,
        prazos,
        noticias,
        nts_criticas_lucro_real: nts
      }};
    }}

    async function salvar() {{
      const btn = document.getElementById('btnSalvar');
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/salvar', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(montarPayload())
        }});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.erro || 'Falha ao salvar');
        document.getElementById('stGerado').textContent = data.gerado_em;
        document.getElementById('stRevisao').textContent = data.revisao_radar;
        toast('atualizacoes.json salvo com sucesso');
      }} catch (e) {{
        toast('Erro: ' + e.message);
      }} finally {{
        btn.disabled = false;
      }}
    }}

    async function checarFontes() {{
      const btn = document.getElementById('btnFontes');
      btn.disabled = true;
      btn.textContent = 'Checando…';
      try {{
        const resp = await fetch('/api/fontes', {{ method: 'POST' }});
        const data = await resp.json();
        const ul = document.getElementById('listaFontes');
        ul.innerHTML = (data.monitoramento || []).map(m =>
          `<li class="${{m.ok ? 'ok' : 'falha'}}">
            <strong>${{esc(m.label)}}</strong> — ${{m.ok ? 'OK · ' + esc(m.titulo_pagina) : 'FALHA · ' + esc(m.erro)}}
          </li>`
        ).join('');
        if (data.gerado_em) {{
          document.getElementById('stGerado').textContent = data.gerado_em;
          document.getElementById('stRevisao').textContent = data.revisao_radar;
        }}
        toast('Fontes verificadas · JSON atualizado');
      }} catch (e) {{
        toast('Erro: ' + e.message);
      }} finally {{
        btn.disabled = false;
        btn.textContent = 'Checar fontes';
      }}
    }}

    renderAlertas();
    renderPrazos();
    renderNoticias();
  </script>
</body>
</html>"""
    return html.encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[app] {args[0]}" if args else fmt)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in ("/", "/app", "/admin"):
            self._send(200, admin_page(), "text/html; charset=utf-8")
            return

        if path == "/radar":
            if not HTML_RADAR.exists():
                self._json(404, {"erro": "radar-fiscal-cerbras.html não encontrado"})
                return
            body = HTML_RADAR.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path == "/atualizacoes.json":
            data = load_data()
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, raw, "application/json; charset=utf-8")
            return

        # arquivos estáticos da pasta (css embutido no html, etc.)
        file_path = (DIR / path.lstrip("/")).resolve()
        if str(file_path).startswith(str(DIR)) and file_path.is_file():
            data = file_path.read_bytes()
            ctype = "application/octet-stream"
            if file_path.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            elif file_path.suffix == ".json":
                ctype = "application/json; charset=utf-8"
            elif file_path.suffix == ".js":
                ctype = "application/javascript"
            elif file_path.suffix == ".css":
                ctype = "text/css"
            self._send(200, data, ctype)
            return

        self._json(404, {"erro": "não encontrado", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/salvar":
            try:
                payload = self._read_json()
                current = load_data()
                current.update(payload)
                current["automacao"] = current.get("automacao") or {}
                current["automacao"]["origem"] = "app_radar"
                save_data(current)
                self._json(
                    200,
                    {
                        "ok": True,
                        "gerado_em": current["gerado_em"],
                        "revisao_radar": current["revisao_radar"],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"erro": str(exc)})
            return

        if path == "/api/fontes":
            try:
                monitoramento = checar_fontes()
                current = load_data()
                current["automacao"] = {
                    "origem": "app_radar",
                    "fontes_ok": sum(1 for m in monitoramento if m["ok"]),
                    "fontes_total": len(monitoramento),
                    "monitoramento": monitoramento,
                }
                if not current.get("revisao_radar"):
                    current["revisao_radar"] = revisao_label()
                save_data(current)
                self._json(
                    200,
                    {
                        "ok": True,
                        "gerado_em": current["gerado_em"],
                        "revisao_radar": current["revisao_radar"],
                        "monitoramento": monitoramento,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"erro": str(exc)})
            return

        self._json(404, {"erro": "endpoint não encontrado"})


def main() -> None:
    if not JSON_PATH.exists():
        save_data(load_data())
        print(f"Criado {JSON_PATH.name}")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print("=" * 56)
    print("  App Radar Fiscal · Cerbras")
    print(f"  Painel:     {url}")
    print(f"  Dashboard:  {url}radar")
    print(f"  JSON:       {url}atualizacoes.json")
    print("  Ctrl+C para encerrar")
    print("=" * 56)

    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
        server.shutdown()


if __name__ == "__main__":
    main()

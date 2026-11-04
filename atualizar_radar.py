#!/usr/bin/env python3
"""
Fase D — Automação do Radar Fiscal Cerbras
------------------------------------------
Atualiza o arquivo atualizacoes.json com dados do RSS e Inteligência Artificial.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import os

# Timezone de Brasília (UTC-3, sem DST desde 2019)
BRT = timezone(timedelta(hours=-3))

DIR = Path(__file__).resolve().parent
DEFAULT_JSON = DIR / "atualizacoes.json"
USER_AGENT = (
    "Mozilla/5.0 (compatible; RadarFiscalCerbras/1.0; +interno-cerbras)"
)

# ---------------------------------------------------------------------------
# Dados Padrão (Caso a IA falhe ou não esteja configurada)
# ---------------------------------------------------------------------------
BRIEFING_PADRAO = {
    "titulo": "Briefing do dia — Fallback",
    "bullets": [
        "Atenção: Não foi possível gerar o resumo por IA hoje. Verifique as notícias abaixo.",
        "Ano-teste em curso: CBS 0,9% + IBS 0,1% com destaque nas notas.",
        "NTs críticas ativas: NT 2025.002, 2026.008 e tabela cClassTrib."
    ],
    "acao_prioritaria": "Validar se NF-e/NFC-e/CT-e estão saindo com CST e cClassTrib corretos."
}

ALERTAS_PADRAO = [
    {"tipo": "atencao", "titulo": "Alerta Padrão", "texto": "Verifique as fontes oficiais.", "prazo": "Contínuo"},
    {"tipo": "critico", "titulo": "Validação de Notas", "texto": "NF-e e CT-e exigem destaque CBS/IBS.", "prazo": "Já em vigor"}
]

PRAZOS_PADRAO = [
    {"quando": "Contínuo", "urgencia": "urgente", "titulo": "Validação de Notas", "detalhe": "CST, cClassTrib e CBS/IBS."},
    {"quando": "15/12/2026", "urgencia": "atencao", "titulo": "Senado", "detalhe": "Alíquota de referência da CBS."}
]

NTS_CRITICAS = [
    "NT 2025.002 — Adequações NF-e/NFC-e (CST, cClassTrib, destaque CBS/IBS)",
    "NT 2026.008 — Valor líquido do produto (lógica por fora a partir de 2027)",
    "Tabela cClassTrib + Informe Técnico 2025.002 v1.71",
    "NT 2026.006 — Vinculação DF-e × pagamento (split payment)",
    "Orientação Técnica CFC nº 01/2026 — aspectos contábeis no ano-teste",
    "Ato Conjunto RFB/CGIBS nº 4/2026 — calendário de obrigatoriedade",
]

FONTES_NOTICIAS = [
    {
        "data": "02/11/2026",
        "titulo": "CBS de 11,3% surpreende empresas e pressiona preços, dizem especialistas",
        "fonte": "Portal da Reforma Tributária",
        "url": "https://www.reformatributaria.com/iva/aliquota-de-113-da-cbs-surpreende-empresas-e-pressiona-precos-dizem-especialistas/",
    }
]

RSS_FEEDS = [
    {"id": "portal_rt", "url": "https://www.reformatributaria.com/feed/", "fonte": "Portal da Reforma Tributária"},
]

RSS_PALAVRAS_CHAVE = [
    "cbs", "ibs", "iva", "reforma", "tributár", "tributar", "nota técnica", 
    "nt 20", "nf-e", "nfe", "nfc-e", "ct-e", "nfs-e", "cclastrib", "split payment", 
    "simples", "lucro real", "imposto seletivo", "pis", "cofins", "icms", "alíquota", 
    "aliquota", "senado", "tcu", "receita federal", "cgibs", "dou", "ato técnico", 
    "documento fiscal", "crédito", "credito"
]

MAX_NOTICIAS = 12

URLS_MONITORADAS = [
    {"id": "portal_rt", "url": "https://www.reformatributaria.com/", "label": "Portal da Reforma Tributária"},
    {"id": "rfb_curso_rtc", "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria-do-consumo/curso", "label": "RFB — Curso RTC"},
    {"id": "mf_portal", "url": "https://www.gov.br/fazenda/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria", "label": "Ministério da Fazenda"},
    {"id": "nfe_portal", "url": "https://www.nfe.fazenda.gov.br", "label": "Portal NF-e"},
    {"id": "cfc", "url": "https://cfc.org.br", "label": "CFC"},
]

def agora_brt() -> datetime:
    return datetime.now(BRT)

def fetch_text(url: str, timeout: int = 20) -> tuple[bool, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
    except Exception as exc:
        return False, str(exc), ""

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        title = re.sub(r"&[#\w]+;", " ", title)
    return True, title or "(sem título)", html[:2000]

def checar_fontes() -> list[dict]:
    resultados = []
    for item in URLS_MONITORADAS:
        ok, info, _ = fetch_text(item["url"])
        resultados.append({
            "id": item["id"],
            "label": item["label"],
            "url": item["url"],
            "ok": ok,
            "titulo_pagina": info if ok else None,
            "erro": None if ok else info,
            "verificado_em": agora_brt().isoformat(timespec="seconds"),
        })
    return resultados

def _titulo_relevante(titulo: str) -> bool:
    t = (titulo or "").lower()
    return any(k in t for k in RSS_PALAVRAS_CHAVE)

def _data_br_from_pubdate(pub: str) -> str:
    if not pub:
        return agora_brt().strftime("%d/%m/%Y")
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BRT).strftime("%d/%m/%Y")
    except Exception:
        return agora_brt().strftime("%d/%m/%Y")

def buscar_rss() -> list[dict]:
    coletadas = []
    vistos = set()

    for feed in RSS_FEEDS:
        req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                xml_bytes = resp.read()
        except Exception:
            continue

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            continue

        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()

            if not title or not link or link in vistos:
                continue
            if not _titulo_relevante(title):
                continue
            
            vistos.add(link)
            coletadas.append({
                "data": _data_br_from_pubdate(pub),
                "titulo": title,
                "fonte": feed["fonte"] + " (RSS)",
                "url": link,
            })
    return coletadas

def mesclar_noticias(rss_itens: list[dict]) -> list[dict]:
    out = []
    vistos = set()
    urls_antigas = set()
    
    if DEFAULT_JSON.exists():
        try:
            dados_antigos = json.loads(DEFAULT_JSON.read_text(encoding="utf-8"))
            for n in dados_antigos.get("noticias", []):
                if n.get("url"): urls_antigas.add(n["url"])
        except Exception:
            pass

    for n in list(rss_itens) + list(FONTES_NOTICIAS):
        url = (n.get("url") or "").strip()
        if not url or url in vistos:
            continue
        vistos.add(url)
        
        if url not in urls_antigas:
            n["novo"] = True
            
        out.append(n)
        if len(out) >= MAX_NOTICIAS:
            break
    return out

def gerar_briefing_com_ia(noticias):
    api_key = os.environ.get("GEMINI_API_KEY")
    fallback = {"briefing": BRIEFING_PADRAO, "alertas": ALERTAS_PADRAO, "prazos": PRAZOS_PADRAO}
    
    if not api_key:
        print("  [Aviso] GEMINI_API_KEY não configurada. Usando dados padrão.")
        return fallback

    titulos = "\n".join([f"- {n.get('titulo', '')}" for n in noticias[:8]])
    
    prompt = f"""
    Você é um analista contábil sênior especialista em Lucro Real e Reforma Tributária.
    Leia as notícias de hoje:
    {titulos}
    
    Gere um JSON estrito (sem marcações markdown) com a estrutura exata abaixo:
    {{
      "briefing": {{
        "titulo": "Briefing do dia — Resumo por IA",
        "bullets": [
          "Resumo focado em impacto prático (máximo 2 linhas)",
          "Resumo focado em prazos ou legislação (máximo 2 linhas)",
          "Resumo focado em ação para a empresa (máximo 2 linhas)"
        ],
        "acao_prioritaria": "Uma frase direta do que o time contábil deve fazer hoje."
      }},
      "alertas": [
        {{
          "tipo": "critico", 
          "titulo": "Título curto do alerta",
          "texto": "Explicação do alerta baseada na notícia",
          "prazo": "Data do prazo ou 'Já em vigor'"
        }}
      ],
      "prazos": [
        {{
          "quando": "Ex: 15/12/2026 ou Contínuo",
          "urgencia": "urgente",
          "titulo": "Título do prazo",
          "detalhe": "O que deve ser feito"
        }}
      ]
    }}
    Regras para os Alertas e Prazos:
    - Gere exatamente 3 alertas (use tipo: 'critico', 'atencao' ou 'info').
    - Gere de 4 a 6 prazos (use urgencia: 'urgente', 'atencao' ou 'normal').
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode('utf-8'), 
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            resultado = json.loads(resp.read().decode('utf-8'))
            texto_ia = resultado['candidates'][0]['content']['parts'][0]['text']
            texto_ia = texto_ia.replace('```json', '').replace('```', '').strip()
            dados_ia = json.loads(texto_ia)
            print("  [IA] Briefing, Alertas e Prazos gerados com sucesso pelo Gemini.")
            return {
                "briefing": dados_ia.get("briefing", BRIEFING_PADRAO),
                "alertas": dados_ia.get("alertas", ALERTAS_PADRAO),
                "prazos": dados_ia.get("prazos", PRAZOS_PADRAO)
            }
    except Exception as e:
        print(f"  [Erro IA] {e}. Usando dados padrão.")
        return fallback

def montar_payload(monitoramento: list[dict], noticias: list[dict] | None = None) -> dict:
    hoje = agora_brt()
    meses = {"jan": "jan", "feb": "fev", "mar": "mar", "apr": "abr", "may": "mai", "jun": "jun", "jul": "jul", "aug": "ago", "sep": "set", "oct": "out", "nov": "nov", "dec": "dez"}
    
    revisao = hoje.strftime("%d %b/%Y").lower().replace(".", "")
    for en, pt in meses.items():
        revisao = revisao.replace(en, pt)

    fontes_ok = sum(1 for m in monitoramento if m["ok"])
    fontes_total = len(monitoramento)
    noticias_finais = noticias if noticias is not None else FONTES_NOTICIAS

    dados_ia = gerar_briefing_com_ia(noticias_finais)

    return {
        "gerado_em": hoje.isoformat(timespec="seconds"),
        "versao": "1.2",
        "revisao_radar": revisao,
        "automacao": {
            "script": "atualizar_radar.py",
            "fontes_ok": fontes_ok,
            "fontes_total": fontes_total,
            "monitoramento": monitoramento,
        },
        "briefing": dados_ia["briefing"],
        "alertas": dados_ia["alertas"],
        "prazos": dados_ia["prazos"],
        "noticias": noticias_finais,
        "nts_criticas_lucro_real": NTS_CRITICAS,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza atualizacoes.json do Radar Fiscal")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--rss", action="store_true")
    args = parser.parse_args()

    print(f"Radar Fiscal Cerbras — Iniciando {agora_brt().strftime('%d/%m/%Y %H:%M:%S')}")

    monitoramento = []
    if not args.skip_fetch:
        monitoramento = checar_fontes()

    noticias = FONTES_NOTICIAS
    if args.rss:
        rss_itens = buscar_rss()
        noticias = mesclar_noticias(rss_itens)
        print(f"Notícias no JSON: {len(noticias)}")

    payload = montar_payload(monitoramento, noticias=noticias)
    if args.rss:
        payload["automacao"]["rss"] = True
        payload["automacao"]["rss_feeds"] = [f["url"] for f in RSS_FEEDS]

    texto = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(texto[:1000])
        return 0

    args.json.write_text(texto + "\n", encoding="utf-8")
    print(f"Gravado: {args.json}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Fase D — Automação do Radar Fiscal Cerbras
------------------------------------------
Atualiza o arquivo atualizacoes.json usado pelo dashboard HTML.
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
# Base curada — edite aqui o que é estável / prioritário para Lucro Real
# ---------------------------------------------------------------------------

BRIEFING_PADRAO = {
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
}

ALERTAS_PADRAO = [
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
]

PRAZOS_PADRAO = [
    {
        "quando": "Contínuo",
        "urgencia": "urgente",
        "titulo": "Validação diária de NF-e / NFC-e / CT-e",
        "detalhe": "CST, cClassTrib e destaque de CBS/IBS — rejeição ou crédito incorreto se estiver errado",
    },
    {
        "quando": "Nov/2026",
        "urgencia": "atencao",
        "titulo": "Homologação do emissor e ERP",
        "detalhe": "Confirmar versão das NTs 2025.002 e 2026.008 no software fiscal da Cerbras",
    },
    {
        "quando": "Nov–Dez",
        "urgencia": "atencao",
        "titulo": "Simulação de margem com CBS 11,3%",
        "detalhe": "Preço, crédito e fluxo de caixa — base para precificação 2027",
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
        "detalhe": "Inventário e análise de crédito presumido de CBS sobre estoque (quando regulamentado)",
    },
    {
        "quando": "01/01/2027",
        "urgencia": "normal",
        "titulo": "Início da CBS integral",
        "detalhe": "PIS/Cofins extintos; CBS com alíquota de referência; IBS permanece em 0,1%",
    },
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
    },
    {
        "data": "28/10/2026",
        "titulo": "TCU propõe CBS de 11,3% em 2027 (11,3468%)",
        "fonte": "Portal da Reforma Tributária",
        "url": "https://www.reformatributaria.com/iva/urgente-novo-tributo-cbs-sera-de-1123-em-2027-propoe-tcu/",
    }
]

RSS_FEEDS = [
    {
        "id": "portal_rt",
        "url": "https://www.reformatributaria.com/feed/",
        "fonte": "Portal da Reforma Tributária",
    },
]

RSS_PALAVRAS_CHAVE = [
    "cbs", "ibs", "iva", "reforma", "tributár", "tributar",
    "nota técnica", "nt 20", "nf-e", "nfe", "nfc-e", "ct-e", "nfs-e",
    "cclastrib", "split payment", "simples", "lucro real",
    "imposto seletivo", "pis", "cofins", "icms", "alíquota", "aliquota",
    "senado", "tcu", "receita federal", "cgibs", "dou", "ato técnico",
    "documento fiscal", "crédito", "credito",
]

MAX_NOTICIAS = 12

URLS_MONITORADAS = [
    {
        "id": "portal_rt",
        "url": "https://www.reformatributaria.com/",
        "label": "Portal da Reforma Tributária",
    },
    {
        "id": "rfb_curso_rtc",
        "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria-do-consumo/curso",
        "label": "RFB — Curso Reforma Tributária do Consumo",
    },
    {
        "id": "mf_portal",
        "url": "https://www.gov.br/fazenda/pt-br/acesso-a-informacao/acoes-e-programas/reforma-tributaria",
        "label": "Ministério da Fazenda — Reforma Tributária",
    },
    {
        "id": "nfe_portal",
        "url": "https://www.nfe.fazenda.gov.br",
        "label": "Portal NF-e",
    },
    {
        "id": "cfc",
        "url": "https://cfc.org.br",
        "label": "CFC — Conselho Federal de Contabilidade",
    },
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
        resultados.append(
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
    coletadas: list[dict] = []
    vistos: set[str] = set()

    for feed in RSS_FEEDS:
        req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                xml_bytes = resp.read()
        except Exception as exc:
            continue

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            continue

        items = root.findall(".//item")
        for it in items:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()

            if not title or not link or link in vistos:
                continue
            if not _titulo_relevante(title):
                continue
            
            vistos.add(link)
            coletadas.append(
                {
                    "data": _data_br_from_pubdate(pub),
                    "titulo": title,
                    "fonte": feed["fonte"] + " (RSS)",
                    "url": link,
                }
            )
    return coletadas

def mesclar_noticias(rss_itens: list[dict]) -> list[dict]:
    out: list[dict] = []
    vistos: set[str] = set()
    
    # Lê o JSON antigo para saber o que já existia (marcar como NOVO)
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
        
        # Se a URL não estava no JSON antigo, marca como NOVO
        if url not in urls_antigas:
            n["novo"] = True
            
        out.append(n)
        if len(out) >= MAX_NOTICIAS:
            break
    return out

# =========================================================================
# NOVA FUNÇÃO: Integração com a Inteligência Artificial (Google Gemini)
# =========================================================================
def gerar_briefing_com_ia(noticias):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    # Se a chave não estiver configurada no GitHub, usa o briefing padrão
    if not api_key:
        print("  [Aviso] GEMINI_API_KEY não configurada. Usando briefing padrão.")
        return BRIEFING_PADRAO

    # Pega as 5 notícias mais recentes para a IA ler
    titulos = "\n".join([f"- {n.get('titulo', '')}" for n in noticias[:5]])
    
    prompt = f"""
    Você é um analista contábil sênior especialista em Lucro Real e Reforma Tributária.
    Com base nestas notícias de hoje:
    {titulos}
    
    Gere um JSON exato com a seguinte estrutura (sem marcações markdown, apenas o JSON puro):
    {{
      "bullets": [
        "Resumo curto 1 (foco em impacto prático)",
        "Resumo curto 2 (foco em prazos ou legislação)",
        "Resumo curto 3 (foco no que a empresa deve fazer)"
      ],
      "acao_prioritaria": "Uma frase direta do que o time fiscal/contábil deve fazer hoje."
    }}
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            resultado = json.loads(resp.read().decode('utf-8'))
            texto_ia = resultado['candidates'][0]['content']['parts'][0]['text']
            
            # Limpeza de segurança caso a IA retorne markdown
            texto_ia = texto_ia.replace('```json', '').replace('```', '').strip()
            
            dados_ia = json.loads(texto_ia)
            
            print("  [IA] Briefing gerado com sucesso pelo Gemini.")
            return {
                "titulo": "Briefing do dia — Resumo por IA",
                "bullets": dados_ia.get("bullets", []),
                "acao_prioritaria": dados_ia.get("acao_prioritaria", "")
            }
    except Exception as e:
        print(f"  [Erro IA] {e}. Usando briefing padrão.")
        return BRIEFING_PADRAO

def montar_payload(monitoramento: list[dict], noticias: list[dict] | None = None) -> dict:
    hoje = agora_brt()
    meses = {"jan": "jan", "feb": "fev", "mar": "mar", "apr": "abr", "may": "mai", "jun": "jun", "jul": "jul", "aug": "ago", "sep": "set", "oct": "out", "nov": "nov", "dec": "dez"}
    
    revisao = hoje.strftime("%d %b/%Y").lower().replace(".", "")
    for en, pt in meses.items():
        revisao = revisao.replace(en, pt)

    fontes_ok = sum(1 for m in monitoramento if m["ok"])
    fontes_total = len(monitoramento)

    noticias_finais = noticias if noticias is not None else FONTES_NOTICIAS

    return {
        "gerado_em": hoje.isoformat(timespec="seconds"),
        "versao": "1.1",
        "revisao_radar": revisao,
        "automacao": {
            "script": "atualizar_radar.py",
            "fontes_ok": fontes_ok,
            "fontes_total": fontes_total,
            "monitoramento": monitoramento,
        },
        # AQUI chamamos a IA em vez do texto fixo
        "briefing": gerar_briefing_com_ia(noticias_finais),
        "alertas": ALERTAS_PADRAO,
        "prazos": PRAZOS_PADRAO,
        "noticias": noticias_finais,
        "nts_criticas_lucro_real": NTS_CRITICAS,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza atualizacoes.json do Radar Fiscal")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="Caminho do JSON de saída")
    parser.add_argument("--dry-run", action="store_true", help="Não grava arquivo")
    parser.add_argument("--skip-fetch", action="store_true", help="Não consulta URLs oficiais")
    parser.add_argument("--rss", action="store_true", help="Busca o feed RSS e mescla notícias")
    args = parser.parse_args()

    print("Radar Fiscal Cerbras — atualização automática")
    print(f"Horário: {agora_brt().strftime('%d/%m/%Y %H:%M:%S')} (BRT)")

    monitoramento: list[dict] = []
    if not args.skip_fetch:
        print("Checando fontes oficiais...")
        monitoramento = checar_fontes()

    noticias = FONTES_NOTICIAS
    if args.rss:
        print("Buscando RSS...")
        rss_itens = buscar_rss()
        noticias = mesclar_noticias(rss_itens)
        print(f"Notícias no JSON: {len(noticias)}")

    payload = montar_payload(monitoramento, noticias=noticias)
    if args.rss:
        payload.setdefault("automacao", {})
        payload["automacao"]["rss"] = True
        payload["automacao"]["rss_feeds"] = [f["url"] for f in RSS_FEEDS]

    texto = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.dry_run:
        print("\n--- dry-run (não gravado) ---")
        print(texto[:1000] + "\n...")
        return 0

    args.json.write_text(texto + "\n", encoding="utf-8")
    print(f"\nGravado: {args.json}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

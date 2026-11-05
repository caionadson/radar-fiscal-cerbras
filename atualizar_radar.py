#!/usr/bin/env python3
import argparse, json, re, sys, urllib.error, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import os

BRT = timezone(timedelta(hours=-3))
DIR = Path(__file__).resolve().parent
DEFAULT_JSON = DIR / "atualizacoes.json"
USER_AGENT = "Mozilla/5.0 (compatible; RadarFiscalCerbras/1.0; +interno-cerbras)"

BRIEFING_PADRAO = {"titulo": "Briefing do dia — Fallback", "bullets": ["Atenção: Não foi possível gerar o resumo por IA hoje."], "acao_prioritaria": "Validar notas fiscais."}
ALERTAS_PADRAO = [{"tipo": "critico", "titulo": "Validação de Notas", "texto": "NF-e e CT-e exigem destaque CBS/IBS.", "prazo": "Já em vigor"}]
PRAZOS_PADRAO = [{"quando": "Contínuo", "urgencia": "urgente", "titulo": "Validação de Notas", "detalhe": "CST, cClassTrib e CBS/IBS."}]
NTS_CRITICAS = ["NT 2025.002", "NT 2026.008", "Tabela cClassTrib v1.71"]

FONTES_NOTICIAS = [
    {
        "data": "04/11/2026",
        "titulo": "Governo publica Medida Provisória regulamentando o Imposto Seletivo e regras de transição do IPI",
        "fonte": "Ministério da Fazenda",
        "url": "https://www.in.gov.br/web/dou/-/medida-provisoria-no-1.303-de-4-de-novembro-de-2026-583984023",
    },
    {
        "data": "02/11/2026",
        "titulo": "CBS de 11,3% surpreende empresas e pressiona preços, dizem especialistas",
        "fonte": "Portal da Reforma Tributária",
        "url": "https://www.reformatributaria.com/iva/aliquota-de-113-da-cbs-surpreende-empresas-e-pressiona-precos-dizem-especialistas/",
    }
]

RSS_FEEDS = [
    {"id": "portal_rt", "url": "https://www.reformatributaria.com/feed/", "fonte": "Portal da Reforma Tributária"},
    {"id": "rfb", "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/programas-e-atividades/reforma-tributaria-do-consumo/ultimas-noticias/noticias/RSS", "fonte": "Receita Federal"},
    {"id": "cfc", "url": "https://cfc.org.br/feed/", "fonte": "CFC"}
]

RSS_PALAVRAS_CHAVE = ["cbs", "ibs", "iva", "reforma", "tributár", "nota técnica", "nt 20", "nf-e", "cclastrib", "split payment", "lucro real", "senado", "tcu", "receita federal", "cfc", "mp", "medida provisória", "decreto", "projeto de lei", "imposto seletivo", "ato conjunto"]
MAX_NOTICIAS = 15
URLS_MONITORADAS = [{"id": "rfb", "url": "https://www.gov.br/receitafederal/", "label": "Receita Federal"}, {"id": "cfc", "url": "https://cfc.org.br", "label": "CFC"}]

def agora_brt(): return datetime.now(BRT)

def fetch_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else "(sem título)"
        return True, title, html[:2000]
    except Exception as exc: return False, str(exc), ""

def checar_fontes():
    return [{"id": i["id"], "label": i["label"], "url": i["url"], "ok": fetch_text(i["url"])[0], "verificado_em": agora_brt().isoformat()} for i in URLS_MONITORADAS]

def _titulo_relevante(t): return any(k in (t or "").lower() for k in RSS_PALAVRAS_CHAVE)

def _data_br(pub):
    if not pub: return agora_brt().strftime("%d/%m/%Y")
    try: return parsedate_to_datetime(pub).astimezone(BRT).strftime("%d/%m/%Y")
    except: return agora_brt().strftime("%d/%m/%Y")

def buscar_rss():
    coletadas, vistos = [], set()
    for feed in RSS_FEEDS:
        try:
            with urllib.request.urlopen(urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT}), timeout=15) as resp:
                root = ET.fromstring(resp.read())
            for it in root.findall(".//item"):
                title, link, pub = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip(), (it.findtext("pubDate") or "").strip()
                if title and link and link not in vistos and _titulo_relevante(title):
                    vistos.add(link)
                    coletadas.append({"data": _data_br(pub), "titulo": title, "fonte": feed["fonte"], "url": link})
        except: continue
    return coletadas

def ordenar_noticias(noticias):
    def parse_data(n):
        try: return datetime.strptime(n["data"], "%d/%m/%Y")
        except: return datetime.min
    return sorted(noticias, key=parse_data, reverse=True)

def mesclar_noticias(rss_itens):
    out, vistos, urls_antigas = [], set(), set()
    if DEFAULT_JSON.exists():
        try:
            for n in json.loads(DEFAULT_JSON.read_text(encoding="utf-8")).get("noticias", []):
                if n.get("url"): urls_antigas.add(n["url"])
        except: pass
    for n in list(rss_itens) + list(FONTES_NOTICIAS):
        url = n.get("url", "").strip()
        if url and url not in vistos:
            vistos.add(url)
            if url not in urls_antigas: n["novo"] = True
            out.append(n)
    return ordenar_noticias(out)[:MAX_NOTICIAS]

def gerar_briefing_com_ia(noticias):
    api_key = os.environ.get("GEMINI_API_KEY")
    fallback = {"briefing": BRIEFING_PADRAO, "alertas": ALERTAS_PADRAO, "prazos": PRAZOS_PADRAO, "novas_normas": []}
    if not api_key: return fallback

    noticias_info = "\n".join([f"- TÍTULO: {n.get('titulo', '')} | DATA: {n.get('data', '')} | LINK: {n.get('url', '')}" for n in noticias[:15]])
    
    prompt = f"""
    Analise estas notícias: \n{noticias_info}\n
    Gere um JSON estrito com:
    {{
      "briefing": {{ "titulo": "Briefing do dia — Resumo por IA", "bullets": ["...", "...", "..."], "acao_prioritaria": "..." }},
      "alertas": [ {{ "tipo": "critico", "titulo": "...", "texto": "...", "prazo": "..." }} ],
      "prazos": [ {{ "quando": "...", "urgencia": "urgente", "titulo": "...", "detalhe": "..." }} ],
      "novas_normas": [ 
        {{ 
          "tipo": "MP", 
          "titulo": "Nome exato da Norma", 
          "descricao": "Resumo do que trata", 
          "link": "URL do documento oficial", 
          "instituicao": "Ex: RFB, Congresso" 
        }} 
      ]
    }}
    
    REGRAS CRÍTICAS:
    1. Se qualquer notícia citar a publicação de uma nova Lei, Medida Provisória (MP), Decreto, Nota Técnica (NT), Instrução Normativa (IN) ou Ato, você DEVE adicioná-la no array 'novas_normas'.
    2. Na propriedade "tipo", classifique estritamente como: "MP", "Lei", "Decreto", "NT", "IN" ou "Ato".
    3. Para o campo "link", analise o texto/links da notícia. Se houver um link direto para o Diário Oficial (in.gov.br) ou para o PDF do documento, use esse link. Se não houver, use o link da própria notícia.
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}), timeout=40) as resp:
            texto_ia = json.loads(resp.read().decode('utf-8'))['candidates'][0]['content']['parts'][0]['text'].replace('```json', '').replace('```', '').strip()
            dados = json.loads(texto_ia)
            return {"briefing": dados.get("briefing", BRIEFING_PADRAO), "alertas": dados.get("alertas", ALERTAS_PADRAO), "prazos": dados.get("prazos", PRAZOS_PADRAO), "novas_normas": dados.get("novas_normas", [])}
    except Exception as e:
        print(f"Erro IA: {e}")
        return fallback

def montar_payload(monitoramento, noticias):
    hoje = agora_brt()
    dados_ia = gerar_briefing_com_ia(noticias)
    return {
        "gerado_em": hoje.isoformat(), "versao": "1.5", "revisao_radar": hoje.strftime("%d %b/%Y").lower(),
        "automacao": {"fontes_ok": sum(1 for m in monitoramento if m["ok"]), "fontes_total": len(monitoramento)},
        "briefing": dados_ia["briefing"], "alertas": dados_ia["alertas"], "prazos": dados_ia["prazos"],
        "novas_normas": dados_ia["novas_normas"], "noticias": noticias, "nts_criticas_lucro_real": NTS_CRITICAS,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--rss", action="store_true")
    args = parser.parse_args()
    monitoramento = checar_fontes()
    noticias = mesclar_noticias(buscar_rss()) if args.rss else ordenar_noticias(FONTES_NOTICIAS)
    args.json.write_text(json.dumps(montar_payload(monitoramento, noticias), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Gravado com sucesso.")

if __name__ == "__main__": main()

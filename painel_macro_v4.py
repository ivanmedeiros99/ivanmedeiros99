"""
Painel Macroeconômico — v4
==========================
Dashboard Streamlit com termômetro composto e indicadores macro para
apoio à decisão de importação (foco Brasil × China).

Novidades da v4
---------------
- Indicador de FRETE ao vivo via Comex Stat (MDIC): frete declarado em US$
  sobre as importações da China (metric `metricFreight`) e a "intensidade de
  frete" (frete / valor FOB), que mede a pressão de custo logístico. O índice
  FBX global continua disponível como entrada manual (não há API pública
  gratuita do FBX), agora claramente rotulado e editável na barra lateral.
- Coleta de dados refatorada: sessão HTTP única com headers, função MDIC
  unificada, parsing robusto e correção do alinhamento rótulo × série.
- Visual repaginado: tema claro profissional por padrão, com alternância
  para modo noturno; cartões de KPI, cabeçalho e gráficos com identidade
  consistente.

Instalação:
    pip install streamlit pandas plotly requests

Execução:
    streamlit run painel_macro_v4.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ════════════════════════════════════════════════════════════════════
#  Configuração da página
# ════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Painel Macroeconômico",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

MAX_MONTHS = 18
REQUEST_TIMEOUT = 20
MDIC_URL = "https://api-comexstat.mdic.gov.br/general"
CHINA_COUNTRY_CODE = 160

# SH4 para mobilidade/ortopedia:
#   8713 — Cadeiras de rodas e outros veículos para pessoas com incapacidade
#   9021 — Artigos e aparelhos ortopédicos (inclui muletas)
#   9402 — Mobiliário para medicina/cirurgia (ex.: camas hospitalares)
#   9019 — Aparelhos de terapia respiratória / nebulizadores
SH4_MOB_ORTO = [8713, 9021, 9402, 9019]


# ════════════════════════════════════════════════════════════════════
#  Tema (claro / noturno)
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Theme:
    """Paleta e tokens visuais usados em CSS e nos gráficos Plotly."""
    name: str
    bg: str
    surface: str
    surface_alt: str
    text: str
    text_muted: str
    border: str
    grid: str
    accent: str
    # cores semânticas do score (favorável / neutro / adverso)
    good: str
    warn: str
    bad: str
    good_soft: str
    warn_soft: str
    bad_soft: str
    plotly_template: str


LIGHT = Theme(
    name="light",
    bg="#F4F6FA",
    surface="#FFFFFF",
    surface_alt="#FBFCFE",
    text="#1F2A37",
    text_muted="#6B7280",
    border="#E5E9F0",
    grid="#EDF0F5",
    accent="#2563EB",
    good="#15803D",
    warn="#B45309",
    bad="#B91C1C",
    good_soft="#E7F5EC",
    warn_soft="#FCF3E3",
    bad_soft="#FCEBEA",
    plotly_template="plotly_white",
)

DARK = Theme(
    name="dark",
    bg="#0E1320",
    surface="#171E2E",
    surface_alt="#1E2638",
    text="#E6EAF2",
    text_muted="#94A3B8",
    border="#2A3346",
    grid="rgba(148,163,184,0.15)",
    accent="#60A5FA",
    good="#34D399",
    warn="#FBBF24",
    bad="#F87171",
    good_soft="#10311F",
    warn_soft="#3A2E12",
    bad_soft="#3A1A1A",
    plotly_template="plotly_dark",
)


def inject_css(t: Theme) -> None:
    """Aplica o tema via CSS sobre a aplicação Streamlit."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class*="css"], .stApp {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }}
        .stApp {{
            background: {t.bg};
            color: {t.text};
        }}
        section[data-testid="stSidebar"] {{
            background: {t.surface};
            border-right: 1px solid {t.border};
        }}
        section[data-testid="stSidebar"] * {{ color: {t.text}; }}

        /* Cabeçalho */
        .pm-header {{
            display: flex; align-items: center; justify-content: space-between;
            gap: 1rem; padding: 4px 2px 2px 2px;
        }}
        .pm-title {{ font-size: 1.7rem; font-weight: 700; color: {t.text}; line-height: 1.1; }}
        .pm-sub {{ font-size: .9rem; color: {t.text_muted}; margin-top: 2px; }}
        .pm-stamp {{
            font-size: .78rem; color: {t.text_muted}; text-align: right;
            background: {t.surface}; border: 1px solid {t.border};
            border-radius: 10px; padding: 8px 14px; white-space: nowrap;
        }}

        /* Cartões */
        .pm-card {{
            background: {t.surface}; border: 1px solid {t.border};
            border-radius: 14px; padding: 16px 18px;
            box-shadow: 0 1px 2px rgba(16,24,40,.04);
        }}

        /* Métricas nativas como cartões */
        div[data-testid="stMetric"] {{
            background: {t.surface}; border: 1px solid {t.border};
            border-radius: 14px; padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(16,24,40,.04);
        }}
        div[data-testid="stMetric"] label p {{ color: {t.text_muted} !important; font-weight: 500; }}
        div[data-testid="stMetricValue"] {{ color: {t.text} !important; }}

        /* Títulos de seção */
        .pm-section {{
            font-size: 1.1rem; font-weight: 700; color: {t.text};
            margin: 6px 0 2px 0; display: flex; align-items: center; gap: 8px;
        }}
        .pm-section::before {{
            content: ""; width: 4px; height: 18px; border-radius: 3px;
            background: {t.accent}; display: inline-block;
        }}
        .pm-divider {{ border: none; border-top: 1px solid {t.border}; margin: 18px 0 10px 0; }}

        .pm-badge {{
            display: inline-block; color: #fff; padding: 5px 18px;
            border-radius: 999px; font-size: .85rem; font-weight: 600;
            letter-spacing: .2px;
        }}
        .pm-caption {{ color: {t.text_muted}; font-size: .8rem; }}

        /* Esconde o cabeçalho/rodapé padrão do Streamlit para visual mais limpo */
        #MainMenu, footer {{ visibility: hidden; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ════════════════════════════════════════════════════════════════════
#  Camada de coleta de dados
# ════════════════════════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": "PainelMacro/4.0 (Streamlit dashboard)",
    "Accept": "application/json",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_bcb_series(code: int, n: int = 12) -> Optional[pd.DataFrame]:
    """Série temporal do Banco Central (SGS), últimos n pontos."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/{n}?formato=json"
    try:
        r = _session().get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 500:
            return None
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        df["data"] = pd.to_datetime(df["data"], dayfirst=True)
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        return df.dropna(subset=["valor"])
    except Exception as e:  # noqa: BLE001
        st.warning(f"BCB série {code}: {e}")
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_bcb_series_daterange(code: int, months_back: int) -> Optional[pd.DataFrame]:
    """Série BCB por intervalo de datas (evita limite do endpoint ultimos/)."""
    end = datetime.now()
    start = end.replace(day=1) - pd.DateOffset(months=months_back)
    url = (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
        f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
    )
    try:
        r = _session().get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        df["data"] = pd.to_datetime(df["data"], dayfirst=True)
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        return df.dropna(subset=["valor"])
    except Exception as e:  # noqa: BLE001
        st.warning(f"BCB série {code} (intervalo): {e}")
        return None


# ── Comex Stat / MDIC ────────────────────────────────────────────────

def _mdic_items(resp) -> list:
    """Normaliza as diferentes formas de envelope da resposta do MDIC."""
    if isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict):
            return data.get("list") or []
        if isinstance(data, list):
            return data
    if isinstance(resp, list):
        return resp
    return []


def _coerce_year_month(item: dict):
    """Extrai (ano, mês) de um item, tolerando vários nomes de chave/formatos."""
    year = item.get("year") or item.get("coYear") or item.get("coAno")
    month = (item.get("monthNumber") or item.get("month")
             or item.get("coMonth") or item.get("coMes"))
    if year and month:
        return int(year), int(month)
    for value in item.values():
        if isinstance(value, str):
            txt = value.strip()
            if len(txt) == 7 and txt[4] == "-" and txt[:4].isdigit() and txt[5:7].isdigit():
                return int(txt[:4]), int(txt[5:7])  # AAAA-MM
            if len(txt) == 7 and txt[2] == "/" and txt[:2].isdigit() and txt[3:7].isdigit():
                return int(txt[3:7]), int(txt[:2])  # MM/AAAA
    return None, None


def _coerce_metric(item: dict, *keys) -> Optional[float]:
    """Lê uma métrica numérica do item testando uma lista de chaves candidatas."""
    for key in keys:
        val = item.get(key)
        if val not in (None, ""):
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_mdic_general(
    months_back: int,
    metrics: tuple,
    sh4_codes: Optional[tuple] = None,
) -> dict:
    """Consulta unificada ao endpoint /general do Comex Stat (fluxo de importação).

    Retorna {"df": DataFrame|None, "debug": [tentativas]}.
    O DataFrame tem coluna 'data' e uma coluna por métrica solicitada
    (fob, freight em USD milhões). País fixo = China (160).
    """
    end = datetime.now()
    start = (end.replace(day=1) - pd.DateOffset(months=months_back)).replace(day=1)

    filters = [{"filter": "country", "values": [CHINA_COUNTRY_CODE]}]
    if sh4_codes:
        filters.append({"filter": "sh4", "values": [int(c) for c in sh4_codes]})

    payload = {
        "flow": "import",
        "monthDetail": True,
        "period": {"from": start.strftime("%Y-%m"), "to": end.strftime("%Y-%m")},
        "filters": filters,
        "metrics": list(metrics),
    }

    metric_keys = {
        "metricFOB": ("metricFOB", "vlFOB", "vl_fob", "fob"),
        "metricFreight": ("metricFreight", "vlFrete", "vl_frete", "freight", "frete"),
        "metricCIF": ("metricCIF", "vlCIF", "vl_cif", "cif"),
    }

    debug: list[str] = []
    try:
        r = None
        for attempt in range(4):
            r = _session().post(MDIC_URL, json=payload, timeout=40)
            if r.status_code == 429:
                time.sleep(5 * (2 ** attempt))
                continue
            break
        if r is None or r.status_code >= 400:
            debug.append(f"HTTP {getattr(r, 'status_code', '—')}")
            return {"df": None, "debug": debug}

        items = _mdic_items(r.json())
        if not items:
            debug.append("0 registros")
            return {"df": None, "debug": debug}

        rows = []
        for item in items:
            year, month = _coerce_year_month(item)
            if not (year and month):
                continue
            row = {"data": pd.Timestamp(year=year, month=month, day=1)}
            for m in metrics:
                val = _coerce_metric(item, *metric_keys.get(m, (m,)))
                row[m] = (val / 1_000_000) if val is not None else None
            rows.append(row)

        if not rows:
            debug.append(f"sem linhas parseáveis | chaves={list(items[0].keys())[:8]}")
            return {"df": None, "debug": debug}

        df = (pd.DataFrame(rows)
              .groupby("data", as_index=False).sum(min_count=1)
              .sort_values("data").reset_index(drop=True))
        debug.append(f"OK ({len(df)} meses)")
        return {"df": df, "debug": debug}

    except Exception as e:  # noqa: BLE001
        debug.append(f"erro {type(e).__name__}: {e}")
        return {"df": None, "debug": debug}


# ════════════════════════════════════════════════════════════════════
#  Fallback manual (atualize conforme necessário)
#  Cobre 18 meses encerrando no mês corrente. Selic, IPCA, USD/BRL e os
#  dados do MDIC vêm de API; CNY/BRL e FBX são manuais (sem API gratuita).
# ════════════════════════════════════════════════════════════════════

FALLBACK = {
    "USD/BRL":   [5.67, 5.55, 5.53, 5.45, 5.37, 5.39, 5.34, 5.46,
                  5.35, 5.20, 5.16, 5.09, 5.12, 5.18, 5.30, 5.41, 5.55, 5.49],
    "CNY/BRL":   [0.78, 0.76, 0.76, 0.75, 0.74, 0.74, 0.74, 0.76,
                  0.74, 0.72, 0.71, 0.70, 0.71, 0.72, 0.74, 0.75, 0.77, 0.76],
    "Selic":     [14.75, 14.75, 14.75, 14.75, 14.75, 14.75, 15.00, 15.00,
                  15.00, 15.00, 14.75, 14.75, 14.75, 14.50, 14.25, 14.25, 14.00, 14.00],
    "IPCA 12m":  [5.53, 5.48, 5.35, 5.32, 5.23, 5.13, 5.17, 4.68,
                  4.44, 3.81, 3.81, 3.81, 3.90, 4.05, 4.10, 4.20, 4.15, 4.10],
    "IPCA mês":  [0.46, 0.21, 0.38, 0.38, 0.44, 0.44, 0.39, 0.52,
                  0.33, 0.70, 0.36, 0.36, 0.30, 0.28, 0.32, 0.40, 0.35, 0.33],
    # FBX — Freightos Baltic Global Container Index (USD/FEU). Entrada manual:
    # não há API pública gratuita. Atualize com o valor semanal mais recente.
    "FBX frete": [3200, 2900, 2700, 2500, 2350, 2200, 2100, 2300,
                  2100, 1946, 1900, 1900, 2050, 2200, 2150, 2000, 1950, 1900],
    # Importações totais do Brasil vindas da China (USD milhões FOB/mês)
    "Imp. China": [4950, 4600, 4500, 4700, 4400, 4600, 5000, 4800,
                   4700, 4900, 4600, 4800, 4750, 4850, 4900, 5050, 4950, 5000],
    # Importações China — mobilidade/ortopedia (USD MM FOB/mês)
    "Imp. Mob/Orto": [460, 420, 400, 430, 410, 430, 470, 450,
                      440, 460, 430, 450, 445, 455, 460, 470, 460, 465],
    # Intensidade de frete = frete declarado / valor FOB (%), importações China
    "Frete imp.": [12.8, 13.1, 13.5, 13.2, 12.9, 12.6, 12.4, 12.1,
                   11.8, 11.5, 11.6, 11.7, 11.9, 12.2, 12.0, 11.8, 11.6, 11.5],
}


# ════════════════════════════════════════════════════════════════════
#  Rótulos e carga consolidada
# ════════════════════════════════════════════════════════════════════

def make_labels(n_months: int) -> list[str]:
    """Rótulos mensais retroativos terminando no mês corrente."""
    now = datetime.now()
    base = now.year * 12 + (now.month - 1)  # índice absoluto do mês atual
    labels = []
    for i in range(n_months - 1, -1, -1):
        idx = base - i
        labels.append(f"{MONTH_NAMES[idx % 12]}/{str(idx // 12)[2:]}")
    return labels


def _tail_pad(series: list, n: int) -> list:
    """Garante exatamente n pontos (repete o 1º valor à esquerda se faltar)."""
    series = list(series)
    while len(series) < n:
        series.insert(0, series[0])
    return series[-n:]


def load_data(n: int = 12) -> dict:
    """Carrega séries de APIs (BCB + MDIC) com fallback manual."""
    data: dict = {}

    # Selic meta — BCB 432
    selic_raw = fetch_bcb_series_daterange(432, n + 6)
    if selic_raw is not None and len(selic_raw) >= 2:
        monthly = selic_raw.set_index("data")["valor"].resample("MS").last().ffill()
        data["Selic"] = monthly.tolist()[-n:]
    else:
        data["Selic"] = FALLBACK["Selic"]

    # IPCA mensal — BCB 433
    ipca = fetch_bcb_series(433, n)
    data["IPCA mês"] = (ipca["valor"].tolist()[-n:]
                        if ipca is not None and len(ipca) >= min(10, n)
                        else FALLBACK["IPCA mês"])

    # IPCA acumulado 12m — BCB 13522
    ipca12 = fetch_bcb_series(13522, n)
    data["IPCA 12m"] = (ipca12["valor"].tolist()[-n:]
                        if ipca12 is not None and len(ipca12) >= min(10, n)
                        else FALLBACK["IPCA 12m"])

    # USD/BRL — BCB 3697 (PTAX média mensal), completado com o dia mais recente
    usd = fetch_bcb_series(3697, n + 1)
    if usd is not None and len(usd) >= 1:
        cur = pd.Timestamp(datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        if usd["data"].max() < cur:
            daily = fetch_bcb_series(1, 1)
            if daily is not None and len(daily) > 0:
                latest = daily.iloc[-1:].copy()
                latest["data"] = cur
                usd = pd.concat([usd, latest], ignore_index=True)
    data["USD/BRL"] = (usd["valor"].tolist()[-n:]
                       if usd is not None and len(usd) >= min(10, n)
                       else FALLBACK["USD/BRL"])

    # CNY/BRL e FBX — manuais (sem API pública gratuita confiável)
    data["CNY/BRL"] = FALLBACK["CNY/BRL"]
    data["FBX frete"] = FALLBACK["FBX frete"]

    # ── MDIC: importações da China — FOB + FRETE (uma só consulta) ──
    china = fetch_mdic_general(n + 1, metrics=("metricFOB", "metricFreight"))
    cdf = china.get("df")
    data["Imp. China debug"] = china.get("debug", [])

    if cdf is not None and "metricFOB" in cdf and cdf["metricFOB"].notna().sum() >= min(6, n):
        data["Imp. China"] = cdf["metricFOB"].tolist()[-n:]
        data["Imp. China fonte"] = "MDIC API (metricFOB)"
        # Intensidade de frete (%) = frete / FOB · 100
        if "metricFreight" in cdf and cdf["metricFreight"].notna().sum() >= min(6, n):
            inten = (cdf["metricFreight"] / cdf["metricFOB"] * 100).round(2)
            data["Frete imp."] = inten.tolist()[-n:]
            data["Frete imp. fonte"] = "MDIC API (metricFreight / metricFOB)"
        else:
            data["Frete imp."] = FALLBACK["Frete imp."]
            data["Frete imp. fonte"] = "fallback manual"
    else:
        data["Imp. China"] = FALLBACK["Imp. China"]
        data["Imp. China fonte"] = "fallback manual"
        data["Frete imp."] = FALLBACK["Frete imp."]
        data["Frete imp. fonte"] = "fallback manual"

    time.sleep(2)  # respeita rate limit do MDIC entre consultas

    # MDIC: China — mobilidade/ortopedia (SH4)
    orto = fetch_mdic_general(n + 1, metrics=("metricFOB",), sh4_codes=tuple(SH4_MOB_ORTO))
    odf = orto.get("df")
    data["Imp. Mob/Orto debug"] = orto.get("debug", [])
    if odf is not None and "metricFOB" in odf and odf["metricFOB"].notna().sum() >= min(6, n):
        data["Imp. Mob/Orto"] = odf["metricFOB"].tolist()[-n:]
        data["Imp. Mob/Orto fonte"] = "MDIC API (SH4 8713/9021/9402/9019)"
    else:
        data["Imp. Mob/Orto"] = FALLBACK["Imp. Mob/Orto"]
        data["Imp. Mob/Orto fonte"] = "fallback manual"

    # Normaliza tamanho de todas as séries numéricas para n pontos
    for k, serie in list(data.items()):
        if isinstance(serie, list) and serie and all(isinstance(x, (int, float)) for x in serie):
            data[k] = _tail_pad(serie, n)

    return data


# ════════════════════════════════════════════════════════════════════
#  Score composto
# ════════════════════════════════════════════════════════════════════

@dataclass
class Indicator:
    name: str
    weight: float
    vmin: float
    vmax: float
    unit: str = ""
    live: bool = False  # True = puxado de API; False = manual
    help: str = ""


INDICATORS: list[Indicator] = [
    Indicator("USD/BRL", 0.30, 4.80, 6.40, "R$", live=True,
              help="Dólar comercial — PTAX média mensal (BCB 3697)."),
    Indicator("CNY/BRL", 0.10, 0.65, 0.90, "R$",
              help="Yuan por real — entrada manual."),
    Indicator("Selic", 0.10, 8.0, 15.0, "% a.a.", live=True,
              help="Selic meta em vigor (BCB 432)."),
    Indicator("IPCA 12m", 0.10, 2.5, 6.5, "%", live=True,
              help="Inflação acumulada 12 meses (BCB 13522)."),
    Indicator("IPCA mês", 0.05, 0.0, 1.0, "%", live=True,
              help="Variação mensal do IPCA (BCB 433)."),
    Indicator("FBX frete", 0.25, 1000, 5000, "/FEU",
              help="Freightos Baltic Index global — entrada manual (sem API gratuita)."),
    Indicator("Imp. China", 0.00, 3000, 7000, "M USD", live=True,
              help="Importações totais do Brasil vindas da China (MDIC, FOB)."),
    Indicator("Imp. Mob/Orto", 0.10, 100, 600, "M USD", live=True,
              help="Importações China — SH4 mobilidade/ortopedia (MDIC, FOB)."),
    Indicator("Frete imp.", 0.00, 8.0, 18.0, "%", live=True,
              help="Intensidade de frete = frete declarado / FOB nas importações da China (MDIC)."),
]


def normalize(value: float, vmin: float, vmax: float) -> float:
    return max(0.0, min(100.0, (value - vmin) / (vmax - vmin) * 100))


def compute_scores(data: dict) -> list[int]:
    n = len(next(v for v in data.values()
                 if isinstance(v, list) and v and isinstance(v[0], (int, float))))
    scores = []
    for i in range(n):
        s = sum(ind.weight * normalize(data[ind.name][i], ind.vmin, ind.vmax)
                for ind in INDICATORS)
        scores.append(round(s))
    return scores


def score_color(s: float, t: Theme) -> str:
    return t.good if s <= 40 else (t.warn if s <= 60 else t.bad)


def score_soft(s: float, t: Theme) -> str:
    return t.good_soft if s <= 40 else (t.warn_soft if s <= 60 else t.bad_soft)


def score_label(s: float) -> str:
    return "Favorável" if s <= 40 else ("Neutro" if s <= 60 else "Adverso")


# ════════════════════════════════════════════════════════════════════
#  Gráficos (todos recebem o tema)
# ════════════════════════════════════════════════════════════════════

def _base_layout(t: Theme, height: int, **kw) -> dict:
    layout = dict(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": 12, "color": t.text},
        template=t.plotly_template,
        hoverlabel={"bgcolor": t.surface, "font_size": 12,
                    "font_family": "Inter", "bordercolor": t.border},
    )
    layout.update(kw)
    return layout


def make_gauge(score: int, t: Theme) -> go.Figure:
    col = score_color(score, t)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 46, "color": col}, "suffix": "/100"},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": t.border,
                     "tickvals": [0, 20, 40, 60, 80, 100],
                     "tickfont": {"color": t.text_muted, "size": 10}},
            "bar": {"color": col, "thickness": 0.28},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 40], "color": t.good_soft},
                {"range": [40, 60], "color": t.warn_soft},
                {"range": [60, 100], "color": t.bad_soft},
            ],
            "threshold": {"line": {"color": col, "width": 3},
                          "thickness": 0.85, "value": score},
        },
    ))
    fig.update_layout(**_base_layout(t, 240, margin=dict(t=30, b=0, l=30, r=30)))
    return fig


def make_breakdown(data: dict, t: Theme) -> go.Figure:
    names, vals, colors = [], [], []
    for ind in INDICATORS:
        v = round(normalize(data[ind.name][-1], ind.vmin, ind.vmax))
        names.append(ind.name)
        vals.append(v)
        colors.append(score_color(v, t))
    fig = go.Figure(go.Bar(
        x=vals, y=names, orientation="h", marker_color=colors,
        text=[str(v) for v in vals], textposition="outside",
        textfont={"size": 12, "color": t.text},
        hovertemplate="%{y}: %{x}/100<extra></extra>",
    ))
    fig.update_layout(**_base_layout(
        t, 360, margin=dict(t=10, b=10, l=120, r=40),
        xaxis=dict(range=[0, 108], gridcolor=t.grid, zeroline=False,
                   title=dict(text="Score individual (0 = ótimo · 100 = crítico)",
                              font=dict(size=11, color=t.text_muted))),
        yaxis=dict(autorange="reversed", gridcolor=t.grid),
    ))
    return fig


def make_score_history(scores: list[int], labels: list[str], t: Theme) -> go.Figure:
    colors = [score_color(s, t) for s in scores]
    fig = go.Figure()
    fig.add_hrect(y0=0, y1=40, fillcolor=t.good_soft, opacity=0.35, line_width=0)
    fig.add_hrect(y0=40, y1=60, fillcolor=t.warn_soft, opacity=0.35, line_width=0)
    fig.add_hrect(y0=60, y1=100, fillcolor=t.bad_soft, opacity=0.35, line_width=0)
    fig.add_trace(go.Scatter(
        x=labels, y=scores, mode="lines+markers",
        line=dict(color=t.accent, width=2.5),
        marker=dict(size=8, color=colors, line=dict(width=1.5, color=t.surface)),
        hovertemplate="%{x}: %{y}/100<extra></extra>",
    ))
    fig.add_hline(y=40, line_dash="dash", line_color=t.good, line_width=1,
                  annotation_text="Favorável < 40", annotation_position="bottom left",
                  annotation_font_size=10, annotation_font_color=t.good)
    fig.add_hline(y=60, line_dash="dash", line_color=t.bad, line_width=1,
                  annotation_text="Adverso > 60", annotation_position="top left",
                  annotation_font_size=10, annotation_font_color=t.bad)
    fig.update_layout(**_base_layout(
        t, 300, margin=dict(t=20, b=40, l=40, r=20), showlegend=False,
        xaxis=dict(gridcolor=t.grid),
        yaxis=dict(range=[0, 100], dtick=20, gridcolor=t.grid),
    ))
    return fig


def _to_rgba(color: str, alpha: float = 0.10) -> str:
    """Converte hex/rgb para rgba com transparência (preenchimento de área)."""
    c = color.lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    if color.startswith("rgb("):
        return f"rgba({color[4:-1]},{alpha})"
    return color


def make_line_chart(labels, values, title, color, t: Theme,
                    y_fmt="", y_range=None, ref_line=None) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=values, mode="lines+markers",
        line=dict(color=color, width=2.5, shape="spline", smoothing=0.5),
        marker=dict(size=5, color=color),
        fill="tozeroy", fillcolor=_to_rgba(color),
        hovertemplate="%{x}: %{y}" + y_fmt + "<extra></extra>",
    ))
    if ref_line is not None:
        fig.add_hline(y=ref_line["y"], line_dash="dash",
                      line_color=ref_line.get("color", t.text_muted), line_width=1,
                      annotation_text=ref_line.get("label", ""),
                      annotation_position="top right", annotation_font_size=9,
                      annotation_font_color=ref_line.get("color", t.text_muted))
    layout = _base_layout(
        t, 230, margin=dict(t=34, b=30, l=58, r=20), showlegend=False,
        title=dict(text=title, font=dict(size=13, color=t.text), x=0, xanchor="left"),
        xaxis=dict(gridcolor=t.grid),
        yaxis=dict(gridcolor=t.grid),
    )
    if y_range:
        layout["yaxis"]["range"] = y_range
    fig.update_layout(**layout)
    return fig


def make_bar_chart(labels, values, title, color, t: Theme, ref_line=None) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=color,
        hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
    ))
    if ref_line:
        fig.add_hline(y=ref_line["y"], line_dash="dash",
                      line_color=ref_line.get("color", t.text_muted), line_width=1,
                      annotation_text=ref_line.get("label", ""),
                      annotation_position="top right", annotation_font_size=9,
                      annotation_font_color=ref_line.get("color", t.text_muted))
    fig.update_layout(**_base_layout(
        t, 230, margin=dict(t=34, b=30, l=58, r=20),
        title=dict(text=title, font=dict(size=13, color=t.text), x=0, xanchor="left"),
        xaxis=dict(gridcolor=t.grid),
        yaxis=dict(range=[0, max(values) * 1.3], gridcolor=t.grid),
    ))
    return fig


# ════════════════════════════════════════════════════════════════════
#  Layout principal
# ════════════════════════════════════════════════════════════════════

def render_source_tag(label: str, t: Theme) -> None:
    live = "API" in label or "MDIC" in label
    color = t.good if live else t.text_muted
    icon = "● ao vivo" if live else "○ manual"
    st.markdown(
        f'<span class="pm-caption" style="color:{color}">{icon} · {label}</span>',
        unsafe_allow_html=True,
    )


def main() -> None:
    # ── Sidebar: tema, período, pesos ────────────────────────────────
    with st.sidebar:
        st.markdown("### ⚙️ Configurações")
        dark = st.toggle("🌙 Modo noturno", value=False)
        t = DARK if dark else LIGHT

        st.markdown("**Período da série histórica**")
        n_months = st.slider("Meses exibidos", 6, MAX_MONTHS, 12, 6, format="%d meses")

        st.markdown("---")
        st.markdown("**Entradas manuais**")
        st.caption("Sem API gratuita — ajuste o valor mais recente.")
        fbx_latest = st.number_input(
            "FBX frete (USD/FEU), mês atual",
            min_value=500, max_value=12000,
            value=int(FALLBACK["FBX frete"][-1]), step=50,
        )
        cny_latest = st.number_input(
            "CNY/BRL, mês atual",
            min_value=0.50, max_value=1.20,
            value=float(FALLBACK["CNY/BRL"][-1]), step=0.01, format="%.2f",
        )

        st.markdown("---")
        st.markdown("**Pesos do score** (somar 100%)")
        new_weights = {}
        for ind in INDICATORS:
            new_weights[ind.name] = st.slider(
                ind.name, 0, 50, int(ind.weight * 100), 5, format="%d%%",
                help=ind.help,
            )
        total = sum(new_weights.values())
        if total != 100:
            st.warning(f"Soma atual: {total}%. Ajuste para 100%.")
        else:
            for ind in INDICATORS:
                ind.weight = new_weights[ind.name] / 100
            st.success("Pesos aplicados.")

        st.markdown("---")
        if st.button("🔄 Forçar atualização", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    inject_css(t)

    # ── Carga de dados ───────────────────────────────────────────────
    labels = make_labels(n_months)
    with st.spinner("Buscando dados atualizados (BCB + MDIC)…"):
        data = load_data(n_months)

    # Aplica entradas manuais da barra lateral ao mês corrente
    data["FBX frete"][-1] = float(fbx_latest)
    data["CNY/BRL"][-1] = float(cny_latest)

    scores = compute_scores(data)
    current = scores[-1]

    # ── Cabeçalho ────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="pm-header">
          <div>
            <div class="pm-title">📊 Painel Macroeconômico</div>
            <div class="pm-sub">Brasil × China · BCB + Comex Stat (MDIC) · série mensal</div>
          </div>
          <div class="pm-stamp">Atualizado em<br><b>{datetime.now():%d/%m/%Y %H:%M}</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── KPIs rápidos ─────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        v = data["USD/BRL"]
        st.metric("USD / BRL", f"R$ {v[-1]:.2f}",
                  f"{(v[-1]/v[0]-1)*100:+.1f}%", delta_color="inverse")
    with k2:
        v = data["Selic"]
        st.metric("Selic", f"{v[-1]:.2f}%", f"{v[-1]-v[0]:+.2f}pp", delta_color="inverse")
    with k3:
        v = data["IPCA 12m"]
        st.metric("IPCA 12m", f"{v[-1]:.2f}%", f"{v[-1]-v[0]:+.2f}pp", delta_color="inverse")
    with k4:
        v = data["Frete imp."]
        st.metric("Frete imp. China", f"{v[-1]:.1f}%",
                  f"{v[-1]-v[0]:+.1f}pp", delta_color="inverse",
                  help="Frete declarado ÷ valor FOB das importações da China (MDIC).")

    # ── Seção 1: Termômetro ──────────────────────────────────────────
    st.markdown('<hr class="pm-divider">', unsafe_allow_html=True)
    st.markdown('<div class="pm-section">Termômetro macroeconômico</div>',
                unsafe_allow_html=True)

    col_gauge, col_bars = st.columns([1, 2])
    with col_gauge:
        st.plotly_chart(make_gauge(current, t), use_container_width=True,
                        config={"displayModeBar": False})
        st.markdown(
            f'<div style="text-align:center;margin-top:-12px;">'
            f'<span class="pm-badge" style="background:{score_color(current, t)}">'
            f'{score_label(current)} · {current}/100</span></div>',
            unsafe_allow_html=True,
        )
        weight_str = " · ".join(f"{i.name} {int(i.weight*100)}%"
                                for i in INDICATORS if i.weight > 0)
        st.markdown(
            f'<p class="pm-caption" style="text-align:center;margin-top:8px">'
            f'Score 0–100 (menor = melhor). Ponderação: {weight_str}</p>',
            unsafe_allow_html=True,
        )
    with col_bars:
        st.plotly_chart(make_breakdown(data, t), use_container_width=True,
                        config={"displayModeBar": False})

    st.markdown('<div class="pm-section">Evolução do score composto</div>',
                unsafe_allow_html=True)
    st.plotly_chart(make_score_history(scores, labels, t), use_container_width=True,
                    config={"displayModeBar": False})

    # ── Seção 2: Indicadores macro ───────────────────────────────────
    st.markdown('<hr class="pm-divider">', unsafe_allow_html=True)
    st.markdown('<div class="pm-section">Indicadores macro</div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        v = data["USD/BRL"]
        st.plotly_chart(make_line_chart(labels, v, "USD / BRL — câmbio médio mensal",
                                        t.accent, t), use_container_width=True,
                        config={"displayModeBar": False})
        render_source_tag("BCB API 3697", t)
    with c2:
        v = data["CNY/BRL"]
        st.plotly_chart(make_line_chart(labels, v, "CNY / BRL — yuan por real",
                                        "#7C3AED", t), use_container_width=True,
                        config={"displayModeBar": False})
        render_source_tag("entrada manual", t)

    c3, c4 = st.columns(2)
    with c3:
        v = data["Selic"]
        st.plotly_chart(make_line_chart(labels, v, "Selic meta em vigor (% a.a.)",
                                        "#D9480F", t), use_container_width=True,
                        config={"displayModeBar": False})
        render_source_tag("BCB API 432", t)
    with c4:
        v = data["FBX frete"]
        st.plotly_chart(make_line_chart(labels, v, "FBX — índice global de contêiner (USD/FEU)",
                                        "#0E7490", t), use_container_width=True,
                        config={"displayModeBar": False})
        render_source_tag("entrada manual (FBX global)", t)

    c5, c6 = st.columns(2)
    with c5:
        v = data["IPCA mês"]
        st.plotly_chart(make_bar_chart(labels, v, "IPCA — variação mensal (%)", t.accent, t,
                                       ref_line={"y": 0.25, "color": t.warn,
                                                 "label": "ref. ~0,25%/mês"}),
                        use_container_width=True, config={"displayModeBar": False})
        render_source_tag("BCB API 433", t)
    with c6:
        v = data["IPCA 12m"]
        st.plotly_chart(make_line_chart(labels, v, "IPCA acumulado 12 meses (%)",
                                        t.good, t,
                                        ref_line={"y": 4.5, "color": t.bad,
                                                  "label": "teto da meta (4,5%)"}),
                        use_container_width=True, config={"displayModeBar": False})
        render_source_tag("BCB API 13522", t)

    # ── Seção 3: Frete & Comércio China (MDIC) ───────────────────────
    st.markdown('<hr class="pm-divider">', unsafe_allow_html=True)
    st.markdown('<div class="pm-section">Frete & comércio com a China (Comex Stat / MDIC)</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<p class="pm-caption">Fonte: api-comexstat.mdic.gov.br · valores em USD milhões FOB · '
        'a intensidade de frete usa o frete declarado (metricFreight) sobre o valor FOB.</p>',
        unsafe_allow_html=True,
    )

    c7, c8 = st.columns(2)
    with c7:
        v = data["Frete imp."]
        st.plotly_chart(make_line_chart(
            labels, v, "Intensidade de frete — importações China (frete ÷ FOB, %)",
            "#C2410C", t, y_fmt="%"), use_container_width=True,
            config={"displayModeBar": False})
        render_source_tag(data.get("Frete imp. fonte", "n/d"), t)
    with c8:
        v = data["Imp. China"]
        st.plotly_chart(make_line_chart(
            labels, v, "Importações totais do Brasil — origem China (USD MM FOB)",
            t.bad, t, y_fmt=" MM"), use_container_width=True,
            config={"displayModeBar": False})
        render_source_tag(data.get("Imp. China fonte", "n/d"), t)

    c9, c10 = st.columns([1, 1])
    with c9:
        v = data["Imp. Mob/Orto"]
        st.plotly_chart(make_line_chart(
            labels, v, "Importações China — SH4 8713+9021+9402+9019 (USD MM FOB)",
            "#9333EA", t, y_fmt=" MM"), use_container_width=True,
            config={"displayModeBar": False})
        render_source_tag(data.get("Imp. Mob/Orto fonte", "n/d"), t)
    with c10:
        st.markdown('<div class="pm-card">', unsafe_allow_html=True)
        st.markdown("**Diagnóstico das consultas MDIC**")
        for key, lbl in [("Imp. China debug", "China (FOB + frete)"),
                         ("Imp. Mob/Orto debug", "SH4 mobilidade/ortopedia")]:
            lines = data.get(key, [])
            if lines:
                st.caption(f"{lbl}: {' | '.join(lines)}")
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Rodapé ───────────────────────────────────────────────────────
    st.markdown('<hr class="pm-divider">', unsafe_allow_html=True)
    st.markdown(
        '<p class="pm-caption"><b>Fontes:</b> Banco Central do Brasil (SGS) · '
        'MDIC Comex Stat · Freightos Baltic Exchange (manual) · IBGE. '
        'CNY/BRL e FBX são entradas manuais (sem API pública gratuita). '
        '<b>Faixas de normalização:</b> '
        'USD/BRL 4,80–6,40 · CNY/BRL 0,65–0,90 · Selic 8–15% · IPCA 12m 2,5–6,5% · '
        'IPCA mês 0–1% · FBX $1.000–$5.000 · Imp. China $3.000–$7.000M · '
        'Imp. Mob/Orto $100–$600M · Frete imp. 8–18%.</p>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

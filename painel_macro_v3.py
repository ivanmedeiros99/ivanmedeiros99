"""
Painel Macroeconômico
Dashboard Streamlit com termômetro composto e 8 indicadores macro,
incluindo importações da China via Comex Stat (MDIC).

Instalação:
    pip install streamlit pandas plotly requests

Execução:
    streamlit run painel_macro_v3.py
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
from datetime import datetime

# ── Configuração da página ──────────────────────────────────────────

st.set_page_config(
    page_title="Painel Macroeconômico",
    page_icon="📊",
    layout="wide",
)

# ── Funções de coleta de dados ──────────────────────────────────────

@st.cache_data(ttl=3600)
def fetch_bcb_series(code, n=12):
    """Busca série temporal do Banco Central do Brasil (SGS)."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/{n}?formato=json"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code >= 500:
            return None  # falha do servidor — cai silenciosamente no fallback
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data)
        df["data"] = pd.to_datetime(df["data"], dayfirst=True)
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        return df
    except Exception as e:
        st.warning(f"Erro ao buscar série BCB {code}: {e}")
        return None


@st.cache_data(ttl=3600)
def fetch_bcb_series_daterange(code, months_back):
    """Busca série BCB por intervalo de datas (evita limite do endpoint ultimos/)."""
    end = datetime.now()
    start = end.replace(day=1) - pd.DateOffset(months=months_back)
    url = (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
        f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data)
        df["data"] = pd.to_datetime(df["data"], dayfirst=True)
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        return df
    except Exception as e:
        st.warning(f"Erro ao buscar série BCB {code} por data: {e}")
        return None


@st.cache_data(ttl=3600)
def fetch_mdic_imports(months_back, chapters=None):
    """Busca importações mensais do Brasil via Comex Stat (MDIC).

    country=160 (China). Retorna DataFrame com colunas 'data' e 'valor' (USD milhões FOB).
    chapters: lista de ints com capítulos NCM a filtrar (ex: [87, 90, 94]).
              Se None, busca total de todas as importações da China.
    """
    end = datetime.now()
    start = (end.replace(day=1) - pd.DateOffset(months=months_back)).replace(day=1)

    filters = [{"filter": "country", "values": [160]}]
    if chapters:
        filters.append({"filter": "chapter", "values": [str(c) for c in chapters]})

    payload = {
        "flow": "import",
        "monthDetail": True,
        "period": {
            "from": start.strftime("%Y-%m"),
            "to": end.strftime("%Y-%m"),
        },
        "filters": filters,
        "metrics": ["metricFOB"],
    }

    url = "https://api-comexstat.mdic.gov.br/general"
    try:
        for attempt in range(4):
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            r.raise_for_status()
            break
        else:
            st.warning("Comex Stat MDIC: limite de requisições atingido. Usando dados de fallback.")
            return None
        resp = r.json()

        # Normaliza diferentes formatos de resposta possíveis
        if isinstance(resp, dict):
            items = (resp.get("data") or {})
            if isinstance(items, dict):
                items = items.get("list", [])
        elif isinstance(resp, list):
            items = resp
        else:
            items = []

        if not items:
            return None

        rows = []
        for item in items:
            year  = item.get("year")  or item.get("coYear")  or item.get("co_year")
            month = item.get("monthNumber") or item.get("coMonth") or item.get("co_month") or item.get("month")
            fob   = item.get("metricFOB") or item.get("vlFOB") or item.get("vl_fob") or 0
            if year and month:
                rows.append({
                    "data": pd.Timestamp(year=int(year), month=int(month), day=1),
                    "valor": float(fob) / 1_000_000,  # USD → USD milhões
                })

        if not rows:
            return None

        df = pd.DataFrame(rows).sort_values("data").reset_index(drop=True)
        return df

    except Exception as e:
        st.warning(f"Erro ao buscar Comex Stat MDIC: {e}")
        return None


# ── Dados (APIs com fallback manual) ────────────────────────────────

MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

MAX_MONTHS = 18

# Capítulos NCM para mobilidade/ortopedia:
#   87 — Veículos (cadeiras de rodas, andadores — NCM 8713/8714/8715)
#   90 — Instrumentos médicos e ortopédicos (NCM 9021: próteses, órteses, muletas)
#   94 — Mobiliário hospitalar (NCM 9402: camas hospitalares, macas)
CHAPTERS_MOB_ORTO = [87, 90, 94]


def make_labels(n_months):
    """Gera lista de rótulos mensais retroativos a partir do mês atual."""
    now = datetime.now()
    labels = []
    for i in range(n_months - 1, -1, -1):
        m = now.month - 1 - i
        year = now.year + m // 12
        month = m % 12
        labels.append(f"{MONTH_NAMES[month]}/{str(year)[2:]}")
    return labels


# Fallback cobre 18 meses: Nov/24 → Abr/26
# Selic, IPCA e USD/BRL são buscados via API do BCB.
# CNY/BRL, FBX e dados MDIC são manuais — atualize conforme necessário.
FALLBACK = {
    "USD/BRL":   [5.70, 5.82, 5.91, 5.86, 5.76, 5.71,  # Nov/24–Abr/25
                  5.67, 5.55, 5.53, 5.45, 5.37, 5.39, 5.34, 5.46, 5.35, 5.20, 5.16, 5.09],
    "CNY/BRL":   [0.79, 0.80, 0.82, 0.81, 0.80, 0.79,  # Nov/24–Abr/25
                  0.78, 0.76, 0.76, 0.75, 0.74, 0.74, 0.74, 0.76, 0.74, 0.72, 0.71, 0.70],
    "Selic":     [11.25, 11.25, 12.25, 13.25, 13.25, 14.25,  # Nov/24–Abr/25
                  14.75, 14.75, 14.75, 14.75, 14.75, 14.75, 15.00, 15.00, 15.00, 15.00, 14.75, 14.75],
    "IPCA 12m":  [4.77, 4.83, 4.87, 5.06, 5.48, 5.48,  # Nov/24–Abr/25
                  5.53, 5.48, 5.35, 5.32, 5.23, 5.13, 5.17, 4.68, 4.44, 3.81, 3.81, 3.81],
    "IPCA mês":  [0.39, 0.52, 0.48, 1.31, 1.31, 0.43,  # Nov/24–Abr/25
                  0.46, 0.21, 0.38, 0.38, 0.44, 0.44, 0.39, 0.52, 0.33, 0.70, 0.36, 0.36],
    "FBX frete": [3800, 3500, 4200, 3800, 3500, 3300,   # Nov/24–Abr/25
                  3200, 2900, 2700, 2500, 2350, 2200, 2100, 2300, 2100, 1946, 1900, 1900],
    # Importações totais do Brasil vindas da China (USD milhões FOB/mês)
    "Imp. China":    [4850, 5100, 5300, 4900, 4600, 4750,  # Nov/24–Abr/25
                      4950, 4600, 4500, 4700, 4400, 4600, 5000, 4800, 4700, 4900, 4600, 4800],
    # Importações da China — capítulos 87 + 90 + 94 (mobilidade/ortopedia, USD MM FOB/mês)
    "Imp. Mob/Orto": [420, 450, 490, 460, 430, 440,        # Nov/24–Abr/25
                      460, 420, 400, 430, 410, 430, 470, 450, 440, 460, 430, 450],
}


def load_data(n=12):
    """Tenta carregar dados de APIs; usa fallback se falhar."""
    data = {}

    # Selic meta em vigor — BCB série 432
    selic_raw = fetch_bcb_series_daterange(432, n + 6)
    if selic_raw is not None and len(selic_raw) >= 2:
        selic_monthly = (
            selic_raw.set_index("data")["valor"]
            .resample("MS").last()
            .ffill()
        )
        data["Selic"] = selic_monthly.tolist()[-n:]
    else:
        data["Selic"] = FALLBACK["Selic"]

    # IPCA mensal — BCB série 433
    ipca_df = fetch_bcb_series(433, n)
    if ipca_df is not None and len(ipca_df) >= min(10, n):
        data["IPCA mês"] = ipca_df["valor"].tolist()[-n:]
    else:
        data["IPCA mês"] = FALLBACK["IPCA mês"]

    # IPCA acumulado 12m — BCB série 13522
    ipca12_df = fetch_bcb_series(13522, n)
    if ipca12_df is not None and len(ipca12_df) >= min(10, n):
        data["IPCA 12m"] = ipca12_df["valor"].tolist()[-n:]
    else:
        data["IPCA 12m"] = FALLBACK["IPCA 12m"]

    # USD/BRL — BCB série 3697 (PTAX mensal média)
    usd_df = fetch_bcb_series(3697, n + 1)
    if usd_df is not None and len(usd_df) >= 1:
        current_month = pd.Timestamp(
            datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        )
        if usd_df["data"].max() < current_month:
            daily_df = fetch_bcb_series(1, 1)
            if daily_df is not None and len(daily_df) > 0:
                latest = daily_df.iloc[-1:].copy()
                latest["data"] = current_month
                usd_df = pd.concat([usd_df, latest], ignore_index=True)
    if usd_df is not None and len(usd_df) >= min(10, n):
        data["USD/BRL"] = usd_df["valor"].tolist()[-n:]
    else:
        data["USD/BRL"] = FALLBACK["USD/BRL"]

    # CNY/BRL e FBX — sem API pública gratuita confiável, usa fallback
    data["CNY/BRL"] = FALLBACK["CNY/BRL"]
    data["FBX frete"] = FALLBACK["FBX frete"]

    # Importações totais da China — Comex Stat MDIC
    imp_china_df = fetch_mdic_imports(n + 1)
    if imp_china_df is not None and len(imp_china_df) >= min(6, n):
        data["Imp. China"] = imp_china_df["valor"].tolist()[-n:]
    else:
        data["Imp. China"] = FALLBACK["Imp. China"]

    # Importações da China — capítulos de mobilidade/ortopedia
    imp_orto_df = fetch_mdic_imports(n + 1, chapters=CHAPTERS_MOB_ORTO)
    if imp_orto_df is not None and len(imp_orto_df) >= min(6, n):
        data["Imp. Mob/Orto"] = imp_orto_df["valor"].tolist()[-n:]
    else:
        data["Imp. Mob/Orto"] = FALLBACK["Imp. Mob/Orto"]

    # Garantir que todas as séries tenham exatamente n pontos
    for k in data:
        while len(data[k]) < n:
            data[k].insert(0, data[k][0])
        data[k] = data[k][-n:]

    return data


# ── Parâmetros do score composto ────────────────────────────────────

INDICATORS = [
    {"name": "USD/BRL",      "weight": 0.30, "min": 4.80, "max": 6.40,  "fmt": "R$ {:.2f}", "suffix": ""},
    {"name": "CNY/BRL",      "weight": 0.10, "min": 0.65, "max": 0.90,  "fmt": "R$ {:.2f}", "suffix": ""},
    {"name": "Selic",        "weight": 0.10, "min": 8.0,  "max": 15.0,  "fmt": "{:.2f}",    "suffix": "% a.a."},
    {"name": "IPCA 12m",     "weight": 0.10, "min": 2.5,  "max": 6.5,   "fmt": "{:.2f}",    "suffix": "%"},
    {"name": "IPCA mês",     "weight": 0.05, "min": 0.0,  "max": 1.0,   "fmt": "{:.2f}",    "suffix": "%"},
    {"name": "FBX frete",    "weight": 0.25, "min": 1000, "max": 5000,  "fmt": "${:,.0f}",  "suffix": "/FEU"},
    {"name": "Imp. China",   "weight": 0.00, "min": 3000, "max": 7000,  "fmt": "${:,.0f}M", "suffix": ""},
    {"name": "Imp. Mob/Orto","weight": 0.10, "min": 100,  "max": 600,   "fmt": "${:,.0f}M", "suffix": ""},
]


def normalize(value, vmin, vmax):
    return max(0, min(100, (value - vmin) / (vmax - vmin) * 100))


def compute_scores(data):
    n = len(next(iter(data.values())))
    scores = []
    for i in range(n):
        s = 0
        for ind in INDICATORS:
            val = data[ind["name"]][i]
            s += ind["weight"] * normalize(val, ind["min"], ind["max"])
        scores.append(round(s))
    return scores


def score_color(s):
    if s <= 40:
        return "#2E7D32"
    elif s <= 60:
        return "#F57F17"
    else:
        return "#C62828"


def score_label(s):
    if s <= 40:
        return "Favorável"
    elif s <= 60:
        return "Neutro"
    else:
        return "Adverso"


# ── Gráfico do termômetro (gauge) ──────────────────────────────────

def make_gauge(score):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 48, "color": score_color(score)}, "suffix": "/100"},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#ccc",
                     "tickvals": [0, 20, 40, 60, 80, 100]},
            "bar": {"color": score_color(score), "thickness": 0.25},
            "bgcolor": "rgba(0,0,0,0)",
            "steps": [
                {"range": [0, 40], "color": "#E8F5E9"},
                {"range": [40, 60], "color": "#FFF8E1"},
                {"range": [60, 100], "color": "#FFEBEE"},
            ],
            "threshold": {
                "line": {"color": score_color(score), "width": 3},
                "thickness": 0.8,
                "value": score,
            },
        },
    ))
    fig.update_layout(
        height=260, margin=dict(t=40, b=0, l=30, r=30),
        paper_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif"},
    )
    return fig


# ── Gráfico de barras horizontais (decomposição) ───────────────────

def make_breakdown(data):
    names, vals, colors = [], [], []
    for ind in INDICATORS:
        v = round(normalize(data[ind["name"]][-1], ind["min"], ind["max"]))
        names.append(ind["name"])
        vals.append(v)
        colors.append(score_color(v))

    fig = go.Figure(go.Bar(
        x=vals, y=names, orientation="h",
        marker_color=colors,
        text=[f"{v}" for v in vals],
        textposition="outside",
        textfont={"size": 12},
    ))
    fig.update_layout(
        height=320, margin=dict(t=10, b=10, l=110, r=40),
        xaxis=dict(range=[0, 105], title=dict(
            text="Score individual (0=ótimo, 100=crítico)", font=dict(size=11))),
        yaxis=dict(autorange="reversed"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": 12},
    )
    return fig


# ── Série histórica do score composto ───────────────────────────────

def make_score_history(scores, labels):
    colors = [score_color(s) for s in scores]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=scores, mode="lines+markers",
        line=dict(color="#534AB7", width=2.5),
        marker=dict(size=8, color=colors, line=dict(width=1, color="#fff")),
        name="Score composto", hovertemplate="%{x}: %{y}/100<extra></extra>",
    ))
    fig.add_hline(y=40, line_dash="dash", line_color="#2E7D32", line_width=1,
                  annotation_text="Favorável < 40", annotation_position="bottom left",
                  annotation_font_size=10, annotation_font_color="#2E7D32")
    fig.add_hline(y=60, line_dash="dash", line_color="#C62828", line_width=1,
                  annotation_text="Adverso > 60", annotation_position="top left",
                  annotation_font_size=10, annotation_font_color="#C62828")
    fig.add_hrect(y0=0,  y1=40,  fillcolor="#E8F5E9", opacity=0.3, line_width=0)
    fig.add_hrect(y0=40, y1=60,  fillcolor="#FFF8E1", opacity=0.3, line_width=0)
    fig.add_hrect(y0=60, y1=100, fillcolor="#FFEBEE", opacity=0.3, line_width=0)
    fig.update_layout(
        height=300, margin=dict(t=20, b=40, l=40, r=20),
        yaxis=dict(range=[0, 100], dtick=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        font={"family": "Inter, system-ui, sans-serif", "size": 12},
    )
    return fig


# ── Gráficos individuais dos indicadores ────────────────────────────

def _to_rgba(color: str, alpha: float = 0.08) -> str:
    if color.startswith("rgb("):
        return f"rgba({color[4:-1]},{alpha})"
    if color.startswith("rgba("):
        parts = color[5:-1].rsplit(",", 1)
        return f"rgba({parts[0]},{alpha})"
    return color


def make_line_chart(labels, values, title, color, y_fmt="", y_range=None, ref_line=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=values, mode="lines+markers",
        line=dict(color=color, width=2),
        marker=dict(size=5, color=color),
        fill="tozeroy", fillcolor=_to_rgba(color),
        hovertemplate="%{x}: %{y}" + y_fmt + "<extra></extra>",
    ))
    if ref_line is not None:
        fig.add_hline(y=ref_line["y"], line_dash="dash",
                      line_color=ref_line.get("color", "#999"), line_width=1,
                      annotation_text=ref_line.get("label", ""),
                      annotation_position="top right",
                      annotation_font_size=9,
                      annotation_font_color=ref_line.get("color", "#999"))
    layout_kw = dict(
        height=220, margin=dict(t=30, b=30, l=60, r=20),
        title=dict(text=title, font=dict(size=13), x=0, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": 11},
        showlegend=False,
    )
    if y_range:
        layout_kw["yaxis"] = dict(range=y_range)
    fig.update_layout(**layout_kw)
    return fig


def make_bar_chart(labels, values, title, color, ref_line=None):
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker_color=color,
        hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
    ))
    if ref_line:
        fig.add_hline(y=ref_line["y"], line_dash="dash",
                      line_color=ref_line.get("color", "#999"), line_width=1,
                      annotation_text=ref_line.get("label", ""),
                      annotation_position="top right",
                      annotation_font_size=9,
                      annotation_font_color=ref_line.get("color", "#999"))
    fig.update_layout(
        height=220, margin=dict(t=30, b=30, l=60, r=20),
        title=dict(text=title, font=dict(size=13), x=0, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": 11},
        yaxis=dict(range=[0, max(values) * 1.3]),
    )
    return fig


# ── Layout principal ────────────────────────────────────────────────

def main():
    st.markdown("## Painel macroeconômico")
    st.caption("Série histórica mensal · BCB + Comex Stat MDIC · Atualizado automaticamente")

    # ── Sidebar ──────────────────────────────────────────────────────

    with st.sidebar:
        st.markdown("### Configurações")

        st.markdown("**Período da série histórica**")
        n_months = st.slider(
            "Meses exibidos", min_value=6, max_value=MAX_MONTHS,
            value=12, step=6, format="%d meses",
        )

        st.markdown("---")
        st.markdown("**Ajuste de pesos do score**")
        st.caption("Os pesos devem somar 100%.")

        new_weights = {}
        for ind in INDICATORS:
            new_weights[ind["name"]] = st.slider(
                ind["name"],
                min_value=0, max_value=50,
                value=int(ind["weight"] * 100),
                step=5,
                format="%d%%",
            )

        total = sum(new_weights.values())
        if total != 100:
            st.warning(f"Soma atual: {total}%. Ajuste para 100%.")
        else:
            for ind in INDICATORS:
                ind["weight"] = new_weights[ind["name"]] / 100
            st.success("Pesos aplicados.")

        st.markdown("---")
        st.markdown("**Atualização de dados**")
        st.caption(
            "BCB: Selic, IPCA e USD/BRL. "
            "MDIC Comex Stat: importações da China (total e mobilidade/ortopedia). "
            "CNY/BRL e FBX: dados manuais (edite FALLBACK no código)."
        )

        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()

    # ── Carga de dados ───────────────────────────────────────────────

    labels = make_labels(n_months)
    with st.spinner("Buscando dados atualizados..."):
        data = load_data(n_months)
    scores = compute_scores(data)
    current_score = scores[-1]

    # ── Seção 1: Termômetro ──────────────────────────────────────────

    st.markdown("---")
    st.markdown("### Termômetro macroeconômico")

    col_gauge, col_bars = st.columns([1, 2])

    with col_gauge:
        st.plotly_chart(make_gauge(current_score), use_container_width=True)
        badge_color = score_color(current_score)
        st.markdown(
            f'<div style="text-align:center;margin-top:-10px;">'
            f'<span style="background:{badge_color};color:white;padding:4px 16px;'
            f'border-radius:12px;font-size:14px;font-weight:600;">'
            f'{score_label(current_score)}</span></div>',
            unsafe_allow_html=True,
        )
        weight_str = " · ".join(
            f"{ind['name']} {int(ind['weight']*100)}%" for ind in INDICATORS
        )
        st.caption(f"Score 0–100 (menor = melhor). Ponderação: {weight_str}")

    with col_bars:
        st.plotly_chart(make_breakdown(data), use_container_width=True)

    st.markdown("#### Evolução do score composto")
    st.plotly_chart(make_score_history(scores, labels), use_container_width=True)

    # ── Seção 2: Indicadores macro ───────────────────────────────────

    st.markdown("---")
    st.markdown("### Indicadores macro")

    col1, col2 = st.columns(2)

    with col1:
        v = data["USD/BRL"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("USD / BRL", f"R$ {v[-1]:.2f}", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v, "USD / BRL — câmbio médio mensal",
                            "rgb(50,102,173)"),
            use_container_width=True,
        )

    with col2:
        v = data["CNY/BRL"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("CNY / BRL", f"R$ {v[-1]:.2f}", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v, "CNY / BRL — yuan por real",
                            "rgb(83,74,183)"),
            use_container_width=True,
        )

    col3, col4 = st.columns(2)

    with col3:
        v = data["Selic"]
        delta = v[-1] - v[0]
        st.metric("Taxa Selic em vigor", f"{v[-1]:.2f}% a.a.", f"{delta:+.2f}pp",
                  delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v, "Selic meta em vigor (% a.a.)",
                            "rgb(216,90,48)"),
            use_container_width=True,
        )

    with col4:
        v = data["FBX frete"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("FBX frete global", f"${v[-1]:,.0f}/FEU", f"{delta:+.1f}%",
                  delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v, "FBX global container index (USD/FEU)",
                            "rgb(216,90,48)"),
            use_container_width=True,
        )

    col5, col6 = st.columns(2)

    with col5:
        v = data["IPCA mês"]
        st.metric("IPCA mensal", f"{v[-1]:.2f}%", "(último mês disponível)")
        st.plotly_chart(
            make_bar_chart(labels, v, "IPCA — variação mensal (%)",
                           "rgb(50,102,173)",
                           ref_line={"y": 0.25, "color": "#F57F17",
                                     "label": "Meta BC ref. ~0,25%/mês"}),
            use_container_width=True,
        )

    with col6:
        v = data["IPCA 12m"]
        delta = v[-1] - v[0]
        st.metric("IPCA acumulado 12m", f"{v[-1]:.2f}%", f"{delta:+.2f}pp",
                  delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v, "IPCA acumulado 12 meses (%)",
                            "rgb(29,158,117)",
                            ref_line={"y": 4.5, "color": "#C62828",
                                      "label": "Teto da meta (4,5%)"}),
            use_container_width=True,
        )

    # ── Seção 3: Importações da China (Comex Stat MDIC) ──────────────

    st.markdown("---")
    st.markdown("### Importações do Brasil — origem China (Comex Stat MDIC)")
    st.caption(
        "Fonte: api-comexstat.mdic.gov.br · Valores em USD milhões FOB · "
        "Mobilidade/ortopedia: capítulos NCM 87 (veículos/cadeiras de rodas), "
        "90 (instrumentos médicos e órteses) e 94 (mobiliário hospitalar)."
    )

    col7, col8 = st.columns(2)

    with col7:
        v = data["Imp. China"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("Importações totais — China",
                  f"USD {v[-1]:,.0f}M", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v,
                            "Importações totais do Brasil — origem China (USD MM FOB)",
                            "rgb(180,40,40)"),
            use_container_width=True,
        )

    with col8:
        v = data["Imp. Mob/Orto"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("Importações mobilidade/ortopedia — China",
                  f"USD {v[-1]:,.0f}M", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(labels, v,
                            "Importações China — caps. 87 + 90 + 94 (USD MM FOB)",
                            "rgb(180,40,40)"),
            use_container_width=True,
        )

    # ── Rodapé ───────────────────────────────────────────────────────

    st.markdown("---")
    st.caption(
        "**Fontes:** Banco Central do Brasil (SGS) · MDIC Comex Stat · "
        "Freightos Baltic Exchange · IBGE. "
        "CNY/BRL e FBX utilizam dados manuais (atualize FALLBACK no código). "
        "**Faixas de normalização:** USD/BRL 4,80–6,40 · CNY/BRL 0,65–0,90 · "
        "Selic 8–15% · IPCA 12m 2,5–6,5% · IPCA mês 0–1% · FBX $1.000–$5.000 · "
        "Imp. China $3.000–$7.000M · Imp. Mob/Orto $100–$600M."
    )


if __name__ == "__main__":
    main()

"""
Painel Macroeconômico
Dashboard Streamlit com termômetro composto e 6 indicadores macro.

Instalação:
    pip install streamlit pandas plotly requests

Execução:
    streamlit run dashboard_dellamed.py

Deploy gratuito:
    1. Suba este arquivo + requirements.txt no GitHub
    2. Acesse share.streamlit.io e conecte o repo
    3. Compartilhe o link com os acionistas
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timedelta
import json
import math

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
def fetch_exchange_rate(base, target, months=12):
    """Busca taxa de câmbio via exchangerate.host (fallback: dados manuais)."""
    end = datetime.now()
    start = end - timedelta(days=months * 31)
    url = (
        f"https://api.exchangerate.host/timeseries"
        f"?start_date={start:%Y-%m-%d}&end_date={end:%Y-%m-%d}"
        f"&base={base}&symbols={target}"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("success"):
            rates = data["rates"]
            rows = [
                {"data": pd.to_datetime(d), "valor": v[target]}
                for d, v in sorted(rates.items())
                if target in v
            ]
            df = pd.DataFrame(rows)
            df = df.set_index("data").resample("MS").mean().reset_index()
            df.columns = ["data", "valor"]
            return df
    except Exception:
        pass
    return None


# ── Dados (APIs com fallback manual) ────────────────────────────────

LABELS = [
    "Mai/25", "Jun/25", "Jul/25", "Ago/25", "Set/25", "Out/25",
    "Nov/25", "Dez/25", "Jan/26", "Fev/26", "Mar/26", "Abr/26",
]

FALLBACK = {
    "USD/BRL":   [5.67, 5.55, 5.53, 5.45, 5.37, 5.39, 5.34, 5.46, 5.35, 5.20, 5.16, 5.09],
    "CNY/BRL":   [0.78, 0.76, 0.76, 0.75, 0.74, 0.74, 0.74, 0.76, 0.74, 0.72, 0.71, 0.70],
    "Selic":     [14.75, 14.75, 14.75, 14.75, 14.75, 14.75, 15.00, 15.00, 15.00, 15.00, 14.75, 14.75],
    "IPCA 12m":  [5.53, 5.48, 5.35, 5.32, 5.23, 5.13, 5.17, 4.68, 4.44, 3.81, 3.81, 3.81],
    "IPCA mês":  [0.46, 0.21, 0.38, 0.38, 0.44, 0.44, 0.39, 0.52, 0.33, 0.70, 0.36, 0.36],
    "FBX frete": [3200, 2900, 2700, 2500, 2350, 2200, 2100, 2300, 2100, 1946, 1900, 1900],
}


def load_data():
    """Tenta carregar dados de APIs; usa fallback se falhar."""
    data = {}

    # Selic meta — BCB série 432
    selic_df = fetch_bcb_series(432, 12)
    if selic_df is not None and len(selic_df) >= 10:
        data["Selic"] = selic_df["valor"].tolist()[-12:]
    else:
        data["Selic"] = FALLBACK["Selic"]

    # IPCA mensal — BCB série 433
    ipca_df = fetch_bcb_series(433, 12)
    if ipca_df is not None and len(ipca_df) >= 10:
        data["IPCA mês"] = ipca_df["valor"].tolist()[-12:]
    else:
        data["IPCA mês"] = FALLBACK["IPCA mês"]

    # IPCA acumulado 12m — BCB série 13522
    ipca12_df = fetch_bcb_series(13522, 12)
    if ipca12_df is not None and len(ipca12_df) >= 10:
        data["IPCA 12m"] = ipca12_df["valor"].tolist()[-12:]
    else:
        data["IPCA 12m"] = FALLBACK["IPCA 12m"]

    # USD/BRL — BCB série 3697 (PTAX mensal média)
    # Para o mês atual sem média fechada, complementa com a cotação diária mais recente (série 1)
    usd_df = fetch_bcb_series(3697, 13)
    if usd_df is not None and len(usd_df) >= 1:
        current_month = pd.Timestamp(datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        if usd_df["data"].max() < current_month:
            daily_df = fetch_bcb_series(1, 1)
            if daily_df is not None and len(daily_df) > 0:
                latest = daily_df.iloc[-1:].copy()
                latest["data"] = current_month
                usd_df = pd.concat([usd_df, latest], ignore_index=True)
    if usd_df is not None and len(usd_df) >= 10:
        data["USD/BRL"] = usd_df["valor"].tolist()[-12:]
    else:
        data["USD/BRL"] = FALLBACK["USD/BRL"]

    # CNY/BRL e FBX — sem API pública gratuita confiável, usa fallback
    data["CNY/BRL"] = FALLBACK["CNY/BRL"]
    data["FBX frete"] = FALLBACK["FBX frete"]

    # Garantir que todas as séries tenham 12 pontos
    for k in data:
        while len(data[k]) < 12:
            data[k].insert(0, data[k][0])
        data[k] = data[k][-12:]

    return data


# ── Parâmetros do score composto ────────────────────────────────────

INDICATORS = [
    {"name": "USD/BRL",   "weight": 0.30, "min": 4.80, "max": 6.40, "fmt": "R$ {:.2f}", "suffix": ""},
    {"name": "CNY/BRL",   "weight": 0.10, "min": 0.65, "max": 0.90, "fmt": "R$ {:.2f}", "suffix": ""},
    {"name": "Selic",     "weight": 0.10, "min": 8.0,  "max": 15.0, "fmt": "{:.2f}",    "suffix": "% a.a."},
    {"name": "IPCA 12m",  "weight": 0.15, "min": 2.5,  "max": 6.5,  "fmt": "{:.2f}",    "suffix": "%"},
    {"name": "IPCA mês",  "weight": 0.10, "min": 0.0,  "max": 1.0,  "fmt": "{:.2f}",    "suffix": "%"},
    {"name": "FBX frete", "weight": 0.25, "min": 1000, "max": 5000, "fmt": "${:,.0f}",   "suffix": "/FEU"},
]


def normalize(value, vmin, vmax):
    return max(0, min(100, (value - vmin) / (vmax - vmin) * 100))


def compute_scores(data):
    scores = []
    for i in range(12):
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
    names = []
    vals = []
    colors = []
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
        height=240, margin=dict(t=10, b=10, l=80, r=40),
        xaxis=dict(range=[0, 105], title=dict(text="Score individual (0=ótimo, 100=crítico)", font=dict(size=11))),
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
    fig.add_hrect(y0=0, y1=40, fillcolor="#E8F5E9", opacity=0.3, line_width=0)
    fig.add_hrect(y0=40, y1=60, fillcolor="#FFF8E1", opacity=0.3, line_width=0)
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
    """Converte rgb()/rgba() para rgba() com o alpha especificado."""
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
        height=220, margin=dict(t=30, b=30, l=50, r=20),
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
        height=220, margin=dict(t=30, b=30, l=50, r=20),
        title=dict(text=title, font=dict(size=13), x=0, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": 11},
        yaxis=dict(range=[0, max(values) * 1.3]),
    )
    return fig


# ── Layout principal ────────────────────────────────────────────────

def main():
    # Header
    st.markdown("## Painel macroeconômico")
    st.caption("Série histórica mensal · mai/2025 – abr/2026 · Atualizado automaticamente via APIs do BCB")

    # Carregar dados
    with st.spinner("Buscando dados atualizados..."):
        data = load_data()
    scores = compute_scores(data)
    current_score = scores[-1]

    # ── Seção 1: Termômetro ─────────────────────────────────────────

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
        st.caption(
            "Score 0–100 (menor = melhor). Ponderação: "
            "USD/BRL 25% · FBX 20% · Selic 20% · IPCA 12m 15% · CNY/BRL 10% · IPCA mês 10%"
        )

    with col_bars:
        st.plotly_chart(make_breakdown(data), use_container_width=True)

    # Série histórica do score
    st.markdown("#### Evolução do score composto")
    st.plotly_chart(make_score_history(scores, LABELS), use_container_width=True)

    # ── Seção 2: Indicadores individuais ────────────────────────────

    st.markdown("---")
    st.markdown("### Indicadores individuais")

    col1, col2 = st.columns(2)

    with col1:
        v = data["USD/BRL"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("USD / BRL", f"R$ {v[-1]:.2f}", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(LABELS, v, "USD / BRL — câmbio médio mensal",
                            "rgb(50,102,173)", y_range=[4.9, 5.8]),
            use_container_width=True,
        )

    with col2:
        v = data["CNY/BRL"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("CNY / BRL", f"R$ {v[-1]:.2f}", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(LABELS, v, "CNY / BRL — yuan por real",
                            "rgb(83,74,183)", y_range=[0.65, 0.82]),
            use_container_width=True,
        )

    col3, col4 = st.columns(2)

    with col3:
        v = data["Selic"]
        delta = v[-1] - v[0]
        st.metric("Taxa Selic meta", f"{v[-1]:.2f}% a.a.", f"{delta:+.2f}pp", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(LABELS, v, "Selic meta (% a.a.)",
                            "rgb(216,90,48)", y_range=[13.5, 15.5]),
            use_container_width=True,
        )

    with col4:
        v = data["FBX frete"]
        delta = (v[-1] / v[0] - 1) * 100
        st.metric("FBX frete global", f"${v[-1]:,.0f}/FEU", f"{delta:+.1f}%", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(LABELS, v, "FBX global container index (USD/FEU)",
                            "rgb(216,90,48)", y_range=[1500, 3500]),
            use_container_width=True,
        )

    col5, col6 = st.columns(2)

    with col5:
        v = data["IPCA mês"]
        st.metric("IPCA mensal", f"{v[-1]:.2f}%", f"(último mês disponível)")
        st.plotly_chart(
            make_bar_chart(LABELS, v, "IPCA — variação mensal (%)",
                           "rgb(50,102,173)",
                           ref_line={"y": 0.25, "color": "#F57F17", "label": "Meta BC ref. ~0,25%/mês"}),
            use_container_width=True,
        )

    with col6:
        v = data["IPCA 12m"]
        delta = v[-1] - v[0]
        st.metric("IPCA acumulado 12m", f"{v[-1]:.2f}%", f"{delta:+.2f}pp", delta_color="inverse")
        st.plotly_chart(
            make_line_chart(LABELS, v, "IPCA acumulado 12 meses (%)",
                            "rgb(29,158,117)", y_range=[3.0, 6.0],
                            ref_line={"y": 4.5, "color": "#C62828", "label": "Teto da meta (4,5%)"}),
            use_container_width=True,
        )

    # ── Rodapé ──────────────────────────────────────────────────────

    st.markdown("---")
    st.caption(
        "**Fontes:** Banco Central do Brasil (SGS), X-Rates, Freightos Baltic Exchange, IBGE. "
        "Dados de Selic, IPCA e USD/BRL são buscados automaticamente via API do BCB. "
        "CNY/BRL e FBX utilizam dados manuais (atualize na seção FALLBACK do código). "
        "Valores de mar–abr/2026 são estimativas. "
        "**Faixas de normalização:** USD/BRL 4,80–6,40 · CNY/BRL 0,65–0,90 · "
        "Selic 8–15% · IPCA 12m 2,5–6,5% · IPCA mês 0–1% · FBX $1.000–$5.000."
    )

    # ── Sidebar: configuração de pesos ──────────────────────────────

    with st.sidebar:
        st.markdown("### Configurações")
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
            "Selic, IPCA e USD/BRL são atualizados automaticamente via API do BCB. "
            "Para CNY/BRL e FBX, edite o dicionário FALLBACK no código-fonte."
        )

        if st.button("Forçar atualização"):
            st.cache_data.clear()
            st.rerun()


if __name__ == "__main__":
    main()

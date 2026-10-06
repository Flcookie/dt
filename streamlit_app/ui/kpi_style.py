"""Visual constants shared by the KPI dashboard (``ui.kpi_display``) and its trend charts
(``ui.kpi_trends``). Values only; display-only, no KPI semantics.
"""

# KPI 页面配色（仅 UI 展示）。本仓库没有 ui_theme 模块；ui/what_if_panel.py 的 _WI_* 颜色是按这里手工对齐的副本。
_UI_THEME: dict[str, str] = {
    "bg": "#f8fafc",
    "surface": "#ffffff",
    "surface2": "#f1f5f9",
    "border": "#e2e8f0",
    "border2": "#cbd5e1",
    "text": "#1e293b",
    "text_dim": "#64748b",
    "accent": "#0284c7",
    "blue": "#3b82f6",
    "green": "#22c55e",
    "orange": "#f97316",
    "red": "#ef4444",
    "gold": "#eab308",
    "indigo": "#6366f1",
}

# Plotly 与 Streamlit 共用：Barlow Condensed（与 ui_sidebar 一致）
_PLOT_FONT = '"Barlow Condensed", "Segoe UI", sans-serif'

# 字号层级中两边共用的几档（其余档位见 ui.kpi_display）
_FONT_L5_PX = 13   # caption、hover、副标题
_FONT_CHART_TITLE_PX = 17  # Chart-T（柱图/趋势图标题）
_FONT_CHART_AXIS_PX = 14  # Chart-A（趋势图刻度）

# System / Stage 指标数值色（同一语义同一色）
_KPI_COLOR_WIP = "#0284c7"
_KPI_COLOR_COMPLETION = "#16a34a"
_KPI_COLOR_SCRAP = "#dc2626"

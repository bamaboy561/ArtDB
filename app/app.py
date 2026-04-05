from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
import secrets
from textwrap import dedent

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from auth_store import (
    authenticate_user,
    authenticate_session,
    bootstrap_first_admin,
    create_auth_session,
    create_user,
    delete_user,
    delete_users_by_salon,
    find_user,
    has_admin_users,
    has_users,
    list_users,
    promote_first_manager_to_admin,
    revoke_auth_session,
    set_user_password,
)
from db import log_audit_event
from sales_analytics import (
    DISPLAY_NAMES,
    build_abc_analysis,
    build_forecast,
    build_heatmap_data,
    build_month_comparison,
    build_monthly_summary,
    build_overview_metrics,
    build_product_summary,
    build_rfm_summary,
    build_yoy_comparison,
    detect_anomalies,
    guess_column_mapping,
    list_excel_sheets,
    load_input_file,
    prepare_sales_data,
    to_csv_bytes,
)
from salon_data_store import (
    count_uploads_for_salon,
    delete_salon,
    load_archive_data,
    load_manifest,
    load_salons,
    register_upload,
    save_salon,
)


st.set_page_config(
    page_title="Аналитика продаж 1С",
    page_icon="📊",
    layout="wide",
)


DASHBOARD_CSS = """
<style>
    .block-container {
        padding-top: 1.6rem;
        padding-bottom: 2.4rem;
    }

    .dashboard-hero {
        padding: 1.4rem 1.5rem;
        border-radius: 24px;
        border: 1px solid rgba(15, 118, 110, 0.18);
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.12), transparent 36%),
            linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(240, 246, 242, 0.94));
        margin-bottom: 1rem;
    }

    .dashboard-eyebrow {
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #0f766e;
        margin-bottom: 0.55rem;
        font-weight: 700;
    }

    .dashboard-title {
        font-size: 2rem;
        line-height: 1.08;
        color: #102a28;
        margin: 0;
        font-weight: 700;
    }

    .dashboard-subtitle {
        margin: 0.75rem 0 0;
        color: #48635f;
        max-width: 58rem;
        font-size: 1rem;
    }

    .scope-strip {
        display: flex;
        gap: 0.65rem;
        flex-wrap: wrap;
        align-items: flex-start;
        margin: 0.9rem 0 0;
    }

    .scope-chip {
        border-radius: 999px;
        padding: 0.35rem 0.8rem;
        background: rgba(15, 118, 110, 0.09);
        color: #14524c;
        font-size: 0.9rem;
        border: 1px solid rgba(15, 118, 110, 0.1);
        max-width: 100%;
        overflow-wrap: anywhere;
    }

    .panel-title {
        font-size: 1.16rem;
        color: #102a28;
        margin-bottom: 0.35rem;
        font-weight: 700;
        line-height: 1.3;
    }

    .panel-caption {
        color: #53706c;
        font-size: 0.96rem;
        margin-bottom: 0.95rem;
        line-height: 1.55;
        max-width: 62rem;
        overflow-wrap: anywhere;
    }

    .section-intro {
        padding: 1rem 1.15rem;
        border-radius: 22px;
        border: 1px solid rgba(15, 118, 110, 0.1);
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.08), transparent 28%),
            linear-gradient(180deg, rgba(255, 255, 255, 0.95), rgba(244, 248, 246, 0.95));
        margin: 0.2rem 0 1rem;
    }

    .section-intro-title {
        font-size: 1.12rem;
        line-height: 1.25;
        color: #102a28;
        margin-bottom: 0.3rem;
        font-weight: 700;
    }

    .section-intro-body {
        color: #53706c;
        font-size: 0.95rem;
        line-height: 1.55;
        max-width: 74rem;
        overflow-wrap: anywhere;
    }

    .section-marker {
        padding: 0.25rem 0 0.9rem;
    }

    .section-marker-kicker {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #0f766e;
        font-weight: 700;
        margin-bottom: 0.3rem;
    }

    .section-marker-title {
        font-size: 1.45rem;
        line-height: 1.12;
        color: #102a28;
        font-weight: 700;
        margin-bottom: 0.35rem;
    }

    .section-marker-body {
        color: #56706b;
        font-size: 0.98rem;
        line-height: 1.55;
        max-width: 68rem;
    }

    .workspace-band {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 0.85rem;
        margin: 0.15rem 0 1.15rem;
    }

    .workspace-band-item {
        padding: 1rem 1rem 0.95rem;
        border-radius: 22px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(244, 248, 246, 0.96));
        border: 1px solid rgba(15, 118, 110, 0.08);
        box-shadow: 0 18px 34px rgba(16, 42, 40, 0.05);
        min-height: 144px;
    }

    .workspace-band-label {
        color: #5a726d;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.45rem;
        font-weight: 700;
        line-height: 1.35;
    }

    .workspace-band-value {
        color: #102a28;
        font-size: 1.28rem;
        line-height: 1.18;
        font-weight: 700;
        margin-bottom: 0.35rem;
        overflow-wrap: anywhere;
    }

    .workspace-band-meta {
        color: #58716d;
        font-size: 0.9rem;
        line-height: 1.45;
        overflow-wrap: anywhere;
    }

    .insight-item {
        padding: 0.75rem 0;
        border-bottom: 1px solid rgba(16, 42, 40, 0.08);
    }

    .insight-item:last-child {
        border-bottom: none;
        padding-bottom: 0;
    }

    .insight-title {
        color: #102a28;
        font-weight: 600;
        margin-bottom: 0.15rem;
    }

    .insight-body {
        color: #4f6764;
        font-size: 0.94rem;
        line-height: 1.45;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 0.55rem;
        background: rgba(255, 255, 255, 0.72);
        padding: 0.35rem;
        border-radius: 18px;
        border: 1px solid rgba(15, 118, 110, 0.08);
        flex-wrap: wrap;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 14px;
        padding: 0.65rem 1rem;
        color: #48635f;
        font-weight: 600;
        height: auto;
        white-space: normal;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(180deg, rgba(15, 118, 110, 0.12), rgba(15, 118, 110, 0.06));
        color: #0f766e !important;
    }

    .stButton > button,
    .stDownloadButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        border-radius: 14px;
        border: 1px solid rgba(15, 118, 110, 0.12);
        background: linear-gradient(180deg, #0f766e, #0b5c56);
        color: white;
        font-weight: 700;
        min-height: 2.8rem;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {
        border-color: rgba(15, 118, 110, 0.28);
        color: white;
    }

    section[data-testid="stSidebar"] {
        background:
            radial-gradient(circle at top right, rgba(251, 191, 36, 0.12), transparent 28%),
            linear-gradient(180deg, rgba(246, 247, 243, 0.98), rgba(236, 243, 240, 0.98));
        border-right: 1px solid rgba(15, 118, 110, 0.08);
    }

    .login-shell {
        padding: 2rem 2.1rem;
        border-radius: 30px;
        border: 1px solid rgba(17, 24, 39, 0.08);
        background:
            radial-gradient(circle at top left, rgba(251, 191, 36, 0.18), transparent 30%),
            radial-gradient(circle at bottom right, rgba(15, 118, 110, 0.16), transparent 32%),
            linear-gradient(160deg, rgba(253, 252, 248, 0.98), rgba(242, 247, 245, 0.96));
        min-height: 100%;
    }

    .auth-kicker {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #0f766e;
        font-weight: 700;
        margin-bottom: 0.65rem;
    }

    .auth-title {
        color: #102a28;
        font-size: 2.2rem;
        line-height: 1.05;
        font-weight: 700;
        margin: 0;
    }

    .auth-copy {
        color: #4f6764;
        margin-top: 0.9rem;
        max-width: 30rem;
        line-height: 1.55;
    }

    .feature-list {
        margin-top: 1.2rem;
        display: grid;
        gap: 0.7rem;
    }

    .feature-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.6rem;
        padding: 0.7rem 0.9rem;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.76);
        border: 1px solid rgba(15, 118, 110, 0.08);
        color: #15302d;
        font-size: 0.95rem;
    }

    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem;
        margin: 1rem 0 1.35rem;
    }

    .metric-card {
        padding: 1.1rem 1.1rem 1rem;
        border-radius: 22px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(246, 248, 244, 0.94));
        border: 1px solid rgba(17, 24, 39, 0.07);
        box-shadow: 0 20px 45px rgba(16, 42, 40, 0.06);
        min-height: 152px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }

    .metric-label {
        color: #5a726d;
        font-size: 0.86rem;
        margin-bottom: 0.55rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .metric-value {
        color: #102a28;
        font-size: 1.62rem;
        line-height: 1.1;
        font-weight: 700;
        overflow-wrap: anywhere;
    }

    .metric-delta {
        margin-top: 0.55rem;
        font-size: 0.88rem;
        color: #5a726d;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .metric-delta.positive {
        color: #0f766e;
    }

    .metric-delta.negative {
        color: #b91c1c;
    }

    .user-strip {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 1rem;
        padding: 0.85rem 1rem;
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(247, 248, 245, 0.9));
        border: 1px solid rgba(15, 118, 110, 0.09);
    }

    .user-meta {
        color: #56706b;
        font-size: 0.92rem;
        line-height: 1.45;
        overflow-wrap: anywhere;
    }

    .user-name {
        color: #102a28;
        font-size: 1rem;
        font-weight: 700;
        overflow-wrap: anywhere;
    }

    .role-pill {
        display: inline-flex;
        align-items: center;
        padding: 0.35rem 0.7rem;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }

    .role-pill.manager {
        background: rgba(180, 83, 9, 0.12);
        color: #9a3412;
    }

    .role-pill.salon {
        background: rgba(15, 118, 110, 0.12);
        color: #0f766e;
    }

    @media (max-width: 1200px) {
        .metric-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 760px) {
        .metric-grid {
            grid-template-columns: 1fr;
        }
    }
</style>
"""

REFERENCE_THEME_CSS = """
<style>
    .stApp {
        background:
            radial-gradient(circle at top left, rgba(15, 118, 110, 0.06), transparent 24%),
            radial-gradient(circle at top right, rgba(251, 191, 36, 0.08), transparent 26%),
            linear-gradient(180deg, #f7faf8 0%, #edf4f1 100%);
    }

    [data-testid="stHeader"] {
        background: rgba(0, 0, 0, 0);
    }

    .block-container {
        max-width: 1740px;
        padding-top: 1.2rem;
        padding-bottom: 2rem;
    }

    section[data-testid="stSidebar"] {
        background:
            radial-gradient(circle at top right, rgba(251, 191, 36, 0.1), transparent 26%),
            linear-gradient(180deg, rgba(246, 247, 243, 0.98), rgba(236, 243, 240, 0.98));
        border-right: 1px solid rgba(15, 118, 110, 0.08);
    }

    section[data-testid="stSidebar"] [data-baseweb="radio"] > div {
        gap: 0.4rem;
    }

    section[data-testid="stSidebar"] [data-baseweb="radio"] label {
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid rgba(15, 118, 110, 0.08);
        border-radius: 16px;
        padding: 0.25rem 0.6rem;
        min-height: 2.7rem;
    }

    section[data-testid="stSidebar"] [data-baseweb="radio"] label:has(input:checked) {
        background: linear-gradient(180deg, rgba(15, 118, 110, 0.14), rgba(15, 118, 110, 0.06));
        border-color: rgba(15, 118, 110, 0.22);
        box-shadow: 0 10px 24px rgba(15, 118, 110, 0.08);
    }

    section[data-testid="stSidebar"] .stButton > button,
    section[data-testid="stSidebar"] div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(180deg, #0f766e, #0b5c56);
        color: #ffffff;
    }

    .dashboard-hero {
        padding: 1.55rem 1.6rem;
        border-radius: 28px;
        border: 1px solid rgba(15, 118, 110, 0.14);
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.1), transparent 35%),
            linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(242, 247, 245, 0.96));
        box-shadow: 0 24px 52px rgba(16, 42, 40, 0.08);
        margin-bottom: 1rem;
    }

    .dashboard-eyebrow {
        color: #0f766e;
    }

    .dashboard-title {
        color: #102a28;
        font-size: 1.95rem;
    }

    .dashboard-subtitle {
        color: #48635f;
        max-width: 68rem;
        line-height: 1.5;
        overflow-wrap: anywhere;
    }

    .scope-chip {
        background: rgba(15, 118, 110, 0.08);
        color: #14524c;
        border: 1px solid rgba(15, 118, 110, 0.1);
    }

    .user-strip {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(247, 248, 245, 0.92));
        border: 1px solid rgba(15, 118, 110, 0.09);
        box-shadow: 0 16px 34px rgba(16, 42, 40, 0.06);
    }

    .user-name {
        color: #102a28;
    }

    .user-meta {
        color: #56706b;
        line-height: 1.45;
        overflow-wrap: anywhere;
    }

    .role-pill.admin {
        background: rgba(190, 24, 93, 0.12);
        color: #9d174d;
    }

    .role-pill.manager {
        background: rgba(180, 83, 9, 0.12);
        color: #9a3412;
    }

    .role-pill.salon {
        background: rgba(15, 118, 110, 0.12);
        color: #0f766e;
    }

    .metric-grid {
        margin: 1rem 0 1.3rem;
        gap: 0.95rem;
    }

    .metric-card {
        min-height: 138px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.95), rgba(246, 248, 244, 0.96));
        border: 1px solid rgba(17, 24, 39, 0.07);
        box-shadow: 0 20px 42px rgba(16, 42, 40, 0.06);
    }

    .metric-label {
        color: #5a726d;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .metric-value {
        color: #102a28;
        font-size: 1.5rem;
        overflow-wrap: anywhere;
    }

    .metric-delta,
    .metric-delta.positive,
    .metric-delta.negative {
        color: #5a726d;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .login-shell {
        background:
            radial-gradient(circle at top left, rgba(251, 191, 36, 0.16), transparent 30%),
            radial-gradient(circle at bottom right, rgba(15, 118, 110, 0.14), transparent 32%),
            linear-gradient(160deg, rgba(253, 252, 248, 0.98), rgba(242, 247, 245, 0.96));
        border: 1px solid rgba(17, 24, 39, 0.08);
        box-shadow: 0 26px 48px rgba(16, 42, 40, 0.08);
    }

    .auth-title {
        color: #102a28;
    }

    .auth-copy {
        color: #4f6764;
    }

    .feature-chip {
        background: rgba(255, 255, 255, 0.76);
        border: 1px solid rgba(15, 118, 110, 0.08);
        color: #15302d;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 249, 247, 0.96));
        border: 1px solid rgba(15, 118, 110, 0.08) !important;
        border-radius: 26px !important;
        box-shadow: 0 20px 42px rgba(16, 42, 40, 0.08);
    }

    .panel-title {
        color: #102a28;
        font-size: 1.2rem;
        font-weight: 700;
        line-height: 1.3;
        margin-bottom: 0.3rem;
    }

    .panel-caption {
        color: #53706c;
        font-size: 0.98rem;
        line-height: 1.6;
        max-width: 62rem;
    }

    .journey-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 0.95rem;
        margin: 0.2rem 0 1.15rem;
    }

    .journey-card {
        position: relative;
        padding: 1.05rem 1.05rem 1rem;
        border-radius: 24px;
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.08), transparent 34%),
            linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(244, 248, 246, 0.97));
        border: 1px solid rgba(15, 118, 110, 0.08);
        box-shadow: 0 16px 34px rgba(16, 42, 40, 0.06);
        min-height: 180px;
    }

    .journey-step {
        width: 2rem;
        height: 2rem;
        border-radius: 999px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 0.8rem;
        background: rgba(15, 118, 110, 0.1);
        color: #0f766e;
        font-size: 0.88rem;
        font-weight: 700;
    }

    .journey-title {
        color: #102a28;
        font-size: 1.08rem;
        font-weight: 700;
        line-height: 1.3;
        margin-bottom: 0.35rem;
    }

    .journey-body {
        color: #4f6764;
        font-size: 0.96rem;
        line-height: 1.58;
    }

    .journey-hint {
        margin-top: 0.75rem;
        color: #0f766e;
        font-size: 0.88rem;
        font-weight: 600;
        line-height: 1.45;
    }

    .spotlight-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 0.95rem;
        margin: 0.15rem 0 1.15rem;
    }

    .spotlight-card {
        padding: 1rem 1.05rem;
        border-radius: 24px;
        border: 1px solid rgba(15, 118, 110, 0.08);
        box-shadow: 0 18px 36px rgba(16, 42, 40, 0.06);
        min-height: 170px;
    }

    .spotlight-card.neutral {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(245, 248, 246, 0.97));
    }

    .spotlight-card.accent {
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.12), transparent 34%),
            linear-gradient(180deg, rgba(240, 248, 246, 0.98), rgba(230, 244, 240, 0.98));
    }

    .spotlight-card.warm {
        background:
            radial-gradient(circle at top right, rgba(180, 83, 9, 0.12), transparent 34%),
            linear-gradient(180deg, rgba(252, 248, 241, 0.98), rgba(249, 243, 233, 0.98));
    }

    .spotlight-label {
        color: #5a726d;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.5rem;
        font-weight: 700;
        line-height: 1.35;
    }

    .spotlight-value {
        color: #102a28;
        font-size: 1.45rem;
        line-height: 1.16;
        font-weight: 700;
        margin-bottom: 0.4rem;
        overflow-wrap: anywhere;
    }

    .spotlight-body {
        color: #4f6764;
        font-size: 0.95rem;
        line-height: 1.58;
    }

    .snapshot-strip {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 0.9rem;
        margin-top: 0.2rem;
    }

    .snapshot-card {
        padding: 1rem 1.05rem 0.95rem;
        border-radius: 22px;
        border: 1px solid rgba(15, 118, 110, 0.08);
        background:
            radial-gradient(circle at top right, rgba(15, 118, 110, 0.09), transparent 34%),
            linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(243, 248, 245, 0.97));
        box-shadow: 0 14px 28px rgba(16, 42, 40, 0.05);
        min-height: 152px;
    }

    .snapshot-label {
        color: #5a726d;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        margin-bottom: 0.45rem;
        line-height: 1.35;
    }

    .snapshot-value {
        color: #102a28;
        font-size: 1.55rem;
        line-height: 1.15;
        font-weight: 700;
        margin-bottom: 0.4rem;
        overflow-wrap: anywhere;
    }

    .snapshot-delta {
        color: #0f766e;
        font-size: 0.92rem;
        font-weight: 600;
        line-height: 1.45;
    }

    .snapshot-delta.negative {
        color: #b45309;
    }

    .snapshot-delta.neutral {
        color: #5a726d;
    }

    .insight-item {
        border-bottom-color: rgba(16, 42, 40, 0.08);
    }

    .insight-title {
        color: #102a28;
    }

    .insight-body {
        color: #4f6764;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(15, 118, 110, 0.08);
        padding: 0.45rem;
    }

    .stTabs [data-baseweb="tab"] {
        color: #48635f;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(180deg, rgba(15, 118, 110, 0.12), rgba(15, 118, 110, 0.06));
        color: #0f766e !important;
    }

    .stButton > button,
    .stDownloadButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(180deg, #0f766e, #0b5c56);
        box-shadow: 0 10px 24px rgba(15, 118, 110, 0.14);
    }

    .nav-shell {
        padding: 1rem 0.95rem;
        border-radius: 22px;
        margin-bottom: 1rem;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(246, 248, 244, 0.86));
        border: 1px solid rgba(15, 118, 110, 0.08);
    }

    .nav-title {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #5a726d;
        margin-bottom: 0.55rem;
    }

    .nav-item {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.72rem 0.82rem;
        border-radius: 16px;
        color: #48635f;
        background: rgba(255, 255, 255, 0.8);
        margin-bottom: 0.45rem;
        border: 1px solid rgba(15, 118, 110, 0.06);
        font-size: 0.95rem;
        overflow-wrap: anywhere;
    }

    .nav-item.active {
        background: linear-gradient(180deg, rgba(15, 118, 110, 0.14), rgba(15, 118, 110, 0.06));
        color: #0f766e;
        border-color: rgba(15, 118, 110, 0.18);
        box-shadow: 0 12px 24px rgba(15, 118, 110, 0.08);
    }

    .admin-stat-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.8rem;
        margin-bottom: 1rem;
    }

    .admin-stat {
        padding: 1rem;
        border-radius: 22px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(244, 248, 246, 0.96));
        color: #102a28;
        border: 1px solid rgba(15, 118, 110, 0.08);
        box-shadow: 0 18px 34px rgba(16, 42, 40, 0.06);
    }

    .admin-stat-label {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #5a726d;
        margin-bottom: 0.5rem;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .admin-stat-value {
        font-size: 1.7rem;
        font-weight: 700;
        line-height: 1;
        color: #0f766e;
        overflow-wrap: anywhere;
    }

    @media (max-width: 1200px) {
        .admin-stat-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 760px) {
        .admin-stat-grid {
            grid-template-columns: 1fr;
        }
    }
</style>
"""

st.markdown(DASHBOARD_CSS + REFERENCE_THEME_CSS, unsafe_allow_html=True)


def is_missing(value: object) -> bool:
    return value is None or bool(pd.isna(value))


def render_html_block(html: str) -> None:
    st.markdown(dedent(html).strip(), unsafe_allow_html=True)


def format_money(value: float) -> str:
    if is_missing(value):
        return "н/д"
    return f"{float(value):,.0f} сом".replace(",", " ")


def format_number(value: float) -> str:
    if is_missing(value):
        return "н/д"
    return f"{float(value):,.0f}".replace(",", " ")


def format_percent(value: float) -> str:
    if is_missing(value):
        return "н/д"
    return f"{float(value):,.1f}%".replace(",", " ")


def percent_or_none(value: float) -> str | None:
    return None if is_missing(value) else format_percent(value)


def format_change_percent(value: float) -> str:
    if is_missing(value):
        return ""
    return f"{float(value):+,.1f}%".replace(",", " ")


def calculate_change_pct(current: float, previous: float) -> float | None:
    if is_missing(current) or is_missing(previous):
        return None
    previous_value = float(previous)
    if abs(previous_value) < 1e-9:
        return None
    return ((float(current) - previous_value) / previous_value) * 100.0


ROLE_LABELS = {
    "admin": "Администратор",
    "manager": "Руководитель",
    "salon": "Салон",
}


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def is_network_role(role: str) -> bool:
    return role in {"admin", "manager"}


def can_manage_access(current_user: dict[str, str]) -> bool:
    return current_user["role"] == "admin"


def polish_figure(figure: go.Figure, *, height: int | None = None) -> go.Figure:
    figure.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.0)",
        font=dict(color="#26403d"),
        margin=dict(l=10, r=10, t=36, b=10),
        legend_title_text="",
    )
    figure.update_xaxes(gridcolor="rgba(38,64,61,0.08)", zerolinecolor="rgba(38,64,61,0.08)")
    figure.update_yaxes(gridcolor="rgba(38,64,61,0.08)", zerolinecolor="rgba(38,64,61,0.08)")
    if height is not None:
        figure.update_layout(height=height)
    return figure


def render_metric_cards(cards: list[dict[str, str]]) -> None:
    cards_html: list[str] = []
    for card in cards:
        delta = card.get("delta") or ""
        delta_class = "metric-delta"
        if delta.startswith("+"):
            delta_class += " positive"
        elif delta.startswith("-"):
            delta_class += " negative"
        delta_html = f'<div class="{delta_class}">{escape(delta)}</div>' if delta else ""
        inner = f'<div class="metric-label">{escape(card["label"])}</div><div class="metric-value">{escape(card["value"])}</div>{delta_html}'
        cards_html.append(f'<div class="metric-card">{inner}</div>')

    st.markdown(f'<div class="metric-grid">{"".join(cards_html)}</div>', unsafe_allow_html=True)


def render_section_intro(title: str, description: str) -> None:
    render_html_block(
        f"""
        <div class="section-intro">
            <div class="section-intro-title">{escape(title)}</div>
            <div class="section-intro-body">{escape(description)}</div>
        </div>
        """
    )


def render_panel_header(title: str, description: str) -> None:
    st.markdown(f'<div class="panel-title">{escape(title)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="panel-caption">{escape(description)}</div>', unsafe_allow_html=True)


def render_section_marker(kicker: str, title: str, description: str) -> None:
    render_html_block(
        f"""
        <div class="section-marker">
            <div class="section-marker-kicker">{escape(kicker)}</div>
            <div class="section-marker-title">{escape(title)}</div>
            <div class="section-marker-body">{escape(description)}</div>
        </div>
        """
    )


def render_workspace_band(items: list[dict[str, str]]) -> None:
    cards_html: list[str] = []
    for item in items:
        meta = item.get("meta", "")
        meta_html = f'<div class="workspace-band-meta">{escape(meta)}</div>' if meta else ""
        cards_html.append(
            dedent(
                f"""
                <div class="workspace-band-item">
                    <div class="workspace-band-label">{escape(item["label"])}</div>
                    <div class="workspace-band-value">{escape(item["value"])}</div>
                    {meta_html}
                </div>
                """
            ).strip()
        )

    render_html_block(f'<div class="workspace-band">{"".join(cards_html)}</div>')


def render_journey_cards(items: list[dict[str, str]]) -> None:
    cards_html: list[str] = []
    for index, item in enumerate(items, start=1):
        hint = item.get("hint", "")
        hint_html = f'<div class="journey-hint">{escape(hint)}</div>' if hint else ""
        cards_html.append(
            dedent(
                f"""
                <div class="journey-card">
                    <div class="journey-step">{index:02d}</div>
                    <div class="journey-title">{escape(item["title"])}</div>
                    <div class="journey-body">{escape(item["body"])}</div>
                    {hint_html}
                </div>
                """
            ).strip()
        )

    render_html_block(f'<div class="journey-grid">{"".join(cards_html)}</div>')


def render_spotlight_cards(items: list[dict[str, str]]) -> None:
    cards_html: list[str] = []
    for item in items:
        tone = item.get("tone", "neutral")
        cards_html.append(
            dedent(
                f"""
                <div class="spotlight-card {escape(tone)}">
                    <div class="spotlight-label">{escape(item["label"])}</div>
                    <div class="spotlight-value">{escape(item["value"])}</div>
                    <div class="spotlight-body">{escape(item["body"])}</div>
                </div>
                """
            ).strip()
        )

    render_html_block(f'<div class="spotlight-grid">{"".join(cards_html)}</div>')


def render_snapshot_strip(items: list[dict[str, str]]) -> None:
    cards_html: list[str] = []
    for item in items:
        delta = item.get("delta", "")
        delta_class = "snapshot-delta"
        if isinstance(delta, str) and delta.startswith("-"):
            delta_class += " negative"
        if not delta:
            delta_class += " neutral"
            delta = item.get("hint", "")
        cards_html.append(
            dedent(
                f"""
                <div class="snapshot-card">
                    <div class="snapshot-label">{escape(item["label"])}</div>
                    <div class="snapshot-value">{escape(item["value"])}</div>
                    <div class="{delta_class}">{escape(delta)}</div>
                </div>
                """
            ).strip()
        )

    render_html_block(f'<div class="snapshot-strip">{"".join(cards_html)}</div>')


ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xls", ".xlsx"}
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024


def validate_uploaded_file(file_bytes: bytes, filename: str) -> str:
    normalized_name = Path(filename or "").name.strip()
    if not normalized_name:
        raise ValueError("Укажите файл с корректным именем.")
    if len(normalized_name) > 255:
        raise ValueError("Имя файла слишком длинное. Используйте имя короче 255 символов.")
    extension = Path(normalized_name).suffix.casefold()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("Поддерживаются только файлы CSV, XLS и XLSX.")
    if not file_bytes:
        raise ValueError("Файл пустой. Загрузите выгрузку с данными.")
    if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("Файл слишком большой. Максимальный размер загрузки: 50 МБ.")

    header = file_bytes[:8]
    if extension == ".xlsx" and not header.startswith(b"PK"):
        raise ValueError("Файл XLSX повреждён или не похож на Excel Open XML.")
    if extension == ".xls" and not header.startswith(b"\xD0\xCF\x11\xE0"):
        raise ValueError("Файл XLS повреждён или не похож на классический Excel.")
    if extension == ".csv" and b"\x00" in file_bytes[:4096]:
        raise ValueError("CSV содержит бинарные данные. Проверьте формат файла.")

    return normalized_name


def save_upload_with_feedback(
    *,
    file_bytes: bytes,
    filename: str,
    salon_name: str,
    report_date: date,
    mapping: dict[str, str | None],
    csv_separator: str,
    csv_encoding: str,
    sheet_name: str | int | None,
    replace_existing: bool,
    actor_username: str = "",
) -> None:
    save_salon(salon_name)
    save_result = register_upload(
        file_bytes=file_bytes,
        filename=filename,
        salon=salon_name,
        report_date=report_date,
        mapping=mapping,
        csv_separator=csv_separator,
        csv_encoding=csv_encoding,
        sheet_name=sheet_name,
        replace_existing=replace_existing,
    )
    st.cache_data.clear()
    replaced_text = ""
    if save_result["replaced"]:
        replaced_text = f" Заменено файлов за дату: {save_result['replaced']}."
    st.session_state["upload_flash_message"] = f"Файл «{filename}» сохранён в архив салона «{salon_name}».{replaced_text}"
    st.session_state["upload_last_saved"] = {
        "salon": salon_name,
        "report_date": report_date.isoformat(),
        "filename": filename,
    }
    audit_event(
        action="upload.save",
        user_id=actor_username,
        details={
            "salon": salon_name,
            "report_date": report_date.isoformat(),
            "filename": filename,
            "replace_existing": replace_existing,
            "replaced_count": int(save_result["replaced"]),
            "upload_id": str(save_result["record"].get("upload_id", "")),
        },
    )


def _query_param_text(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value).strip() if value is not None else ""


def get_request_ip() -> str:
    try:
        headers = getattr(st.context, "headers", None)
    except Exception:
        return ""
    if not headers:
        return ""

    forwarded_for = _query_param_text(headers.get("X-Forwarded-For", "") or headers.get("x-forwarded-for", ""))
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    for header_name in ("X-Real-IP", "x-real-ip", "X-Forwarded-Client-Ip", "x-forwarded-client-ip"):
        header_value = _query_param_text(headers.get(header_name, ""))
        if header_value:
            return header_value.strip()
    return ""


def audit_event(*, action: str, user_id: str, details: dict[str, object] | None = None) -> None:
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        return
    try:
        log_audit_event(
            user_id=normalized_user_id,
            action=action,
            ip=get_request_ip(),
            details=details or {},
        )
    except Exception as error:
        print(f"audit log error [{action}]: {error}")


def get_persistent_auth_token() -> str:
    return _query_param_text(st.query_params.get("auth", ""))


def set_persistent_auth_token(token: str) -> None:
    token = token.strip()
    if token:
        st.query_params["auth"] = token


def clear_persistent_auth_token() -> None:
    if "auth" in st.query_params:
        del st.query_params["auth"]


def build_safe_marker_size(series: pd.Series, *, absolute: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if absolute:
        return numeric.abs()
    return numeric.clip(lower=0)


def render_user_strip(current_user: dict[str, str]) -> None:
    salon_text = current_user.get("salon") or "Вся сеть"
    contact = current_user.get("email") or current_user.get("phone") or current_user["username"]
    render_html_block(
        f"""
        <div class="user-strip">
            <div>
                <div class="user-name">{escape(current_user['display_name'])}</div>
                <div class="user-meta">Контакт: {escape(contact)} | Контур: {escape(salon_text)}</div>
            </div>
            <div class="role-pill {escape(current_user['role'])}">{escape(role_label(current_user['role']))}</div>
        </div>
        """
    )


def render_sidebar_navigation(current_user: dict[str, str], work_mode: str) -> None:
    if current_user["role"] == "salon":
        items = [
            ("Мой архив", work_mode == "Архив салона"),
            ("Ежедневная загрузка", work_mode == "Новая выгрузка"),
            ("Локальная аналитика", work_mode in {"Архив салона", "Новая выгрузка"}),
        ]
    elif current_user["role"] == "admin":
        items = [
            ("Сеть салонов", work_mode == "Сводка по сети"),
            ("Загрузка по салонам", work_mode == "Загрузка салона"),
            ("Разовый анализ", work_mode == "Разовая загрузка"),
            ("Управление", True),
        ]
    else:
        items = [
            ("Сеть салонов", work_mode == "Сводка по сети"),
            ("Загрузка по салонам", work_mode == "Загрузка салона"),
            ("Разовый анализ", work_mode == "Разовая загрузка"),
            ("Сводный контроль", True),
        ]

    items_html = "".join(
        f'<div class="nav-item{" active" if active else ""}">{escape(label)}</div>' for label, active in items
    )
    render_html_block(
        f"""
        <div class="nav-shell">
            <div class="nav-title">Рабочие зоны</div>
            {items_html}
        </div>
        """
    )


def generate_temp_password(length: int = 10) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def render_sidebar_admin_quick_actions(current_user: dict[str, str], registered_salons: list[str]) -> None:
    st.divider()
    st.markdown("**Быстрое создание**")
    st.caption("Создайте салон и сразу выдайте сотруднику логин с паролем.")

    created_credentials = st.session_state.get("sidebar_created_credentials")
    if created_credentials:
        st.success("Пользователь создан. Эти данные можно сразу отправить сотруднику.")
        credentials_text = "\n".join(
            [
                f"Роль: {created_credentials['role_label']}",
                f"Салон: {created_credentials['salon'] or 'Без привязки'}",
                f"Логин: {created_credentials['username']}",
                f"Пароль: {created_credentials['password']}",
                f"Контакт: {created_credentials['contact'] or 'Не указан'}",
            ]
        )
        st.code(credentials_text, language="text")
        if st.button("Очистить карточку доступа", key="sidebar_clear_created_credentials", use_container_width=True):
            st.session_state.pop("sidebar_created_credentials", None)
            st.rerun()

    current_salons = load_salons()

    with st.expander("Добавить салон", expanded=False):
        new_salon_name = st.text_input(
            "Название салона",
            key="sidebar_new_salon_name",
            placeholder="Например: Бишкек | Азия Молл",
        )
        if st.button("Сохранить салон", key="sidebar_add_salon_button", use_container_width=True):
            normalized_salon_name = new_salon_name.strip()
            existing_salons = {salon.casefold() for salon in current_salons}

            if not normalized_salon_name:
                st.error("Введите название салона.")
            elif normalized_salon_name.casefold() in existing_salons:
                st.warning("Такой салон уже существует.")
            else:
                save_salon(normalized_salon_name)
                audit_event(
                    action="salon.create",
                    user_id=current_user["username"],
                    details={
                        "salon": normalized_salon_name,
                        "source": "sidebar_quick_action",
                    },
                )
                st.session_state.pop("sidebar_new_salon_name", None)
                st.session_state["admin_flash_message"] = f"Салон «{normalized_salon_name}» добавлен."
                st.rerun()

    with st.expander("Создать пользователя", expanded=False):
        st.caption("Хотя бы один контакт обязателен: email или телефон.")

        role_choice = st.selectbox(
            "Роль",
            options=["salon", "manager", "admin"],
            format_func=role_label,
            key="sidebar_create_role",
        )
        username = st.text_input(
            "Логин",
            key="sidebar_create_username",
            placeholder="Например: salon_mega",
        )
        display_name = st.text_input(
            "Имя в системе",
            key="sidebar_create_display_name",
            placeholder="Например: Салон Мега",
        )
        email = st.text_input(
            "Email",
            key="sidebar_create_email",
            placeholder="user@company.ru",
        )
        phone = st.text_input(
            "Телефон",
            key="sidebar_create_phone",
            placeholder="+996 555 123 456",
        )

        selected_salon = ""
        if role_choice == "salon":
            salon_options = current_salons if current_salons else ["Сначала добавьте салон"]
            selected_salon = st.selectbox(
                "Салон",
                options=salon_options,
                key="sidebar_create_salon",
                disabled=not bool(current_salons),
            )
        else:
            st.caption("Для администратора и руководителя выбор салона не нужен.")

        generate_password_col, _ = st.columns([1, 1])
        with generate_password_col:
            if st.button("Сгенерировать пароль", key="sidebar_generate_password", use_container_width=True):
                generated_password = generate_temp_password()
                st.session_state["sidebar_create_password"] = generated_password
                st.session_state["sidebar_create_password_confirm"] = generated_password
                st.rerun()

        password = st.text_input("Пароль", type="password", key="sidebar_create_password")
        password_confirm = st.text_input("Повтор пароля", type="password", key="sidebar_create_password_confirm")

        create_user_disabled = role_choice == "salon" and not bool(current_salons)
        if create_user_disabled:
            st.warning("Сначала добавьте салон, потом можно создать пользователя салона.")

        if st.button(
            "Создать пользователя",
            key="sidebar_create_user_button",
            use_container_width=True,
            disabled=create_user_disabled,
        ):
            if password != password_confirm:
                st.error("Пароли не совпадают.")
            else:
                try:
                    create_user(
                        username=username,
                        password=password,
                        role=role_choice,
                        display_name=display_name,
                        email=email,
                        phone=phone,
                        salon=selected_salon,
                    )
                    st.session_state["sidebar_created_credentials"] = {
                        "role_label": role_label(role_choice),
                        "salon": selected_salon if role_choice == "salon" else "",
                        "username": username.strip(),
                        "password": password,
                        "contact": email.strip() or phone.strip(),
                    }
                    audit_event(
                        action="user.create",
                        user_id=current_user["username"],
                        details={
                            "username": username.strip(),
                            "role": role_choice,
                            "salon": selected_salon if role_choice == "salon" else "",
                            "has_email": bool(email.strip()),
                            "has_phone": bool(phone.strip()),
                            "source": "sidebar_quick_action",
                        },
                    )
                    for key in (
                        "sidebar_create_username",
                        "sidebar_create_display_name",
                        "sidebar_create_email",
                        "sidebar_create_phone",
                        "sidebar_create_password",
                        "sidebar_create_password_confirm",
                        "sidebar_create_salon",
                    ):
                        st.session_state.pop(key, None)
                    st.session_state.pop("sidebar_create_role", None)
                    st.session_state["admin_flash_message"] = (
                        f"Пользователь «{display_name.strip() or username.strip()}» создан."
                    )
                    st.rerun()
                except Exception as error:
                    st.error(str(error))


def render_auth_gate() -> dict[str, str]:
    if "auth_user" in st.session_state:
        session_user = st.session_state["auth_user"]
        actual_user = find_user(str(session_user.get("username", "")))
        if actual_user and bool(actual_user.get("is_active", True)):
            refreshed_user = {
                "username": str(actual_user.get("username", "")),
                "display_name": str(actual_user.get("display_name", "") or actual_user.get("username", "")),
                "role": str(actual_user.get("role", "")),
                "salon": str(actual_user.get("salon", "") or ""),
                "email": str(actual_user.get("email", "") or ""),
                "phone": str(actual_user.get("phone", "") or ""),
                "is_active": bool(actual_user.get("is_active", True)),
                "created_at": str(actual_user.get("created_at", "") or ""),
            }
            st.session_state["auth_user"] = refreshed_user
            if not get_persistent_auth_token():
                session_token = create_auth_session(refreshed_user["username"])
                set_persistent_auth_token(session_token)
            return refreshed_user

        st.session_state.pop("auth_user", None)
        clear_persistent_auth_token()
        st.session_state["auth_notice"] = "Эта учётная запись удалена или отключена. Войдите под другим пользователем."

    persistent_token = get_persistent_auth_token()
    if persistent_token:
        persistent_user = authenticate_session(persistent_token)
        if persistent_user:
            st.session_state["auth_user"] = persistent_user
            return persistent_user
        clear_persistent_auth_token()
        st.session_state["auth_notice"] = "Сессия входа закончилась или стала недействительной. Войдите снова."

    left_col, right_col = st.columns([1.05, 0.95], gap="large")

    with left_col:
        st.markdown(
            """
            <div class="login-shell">
                <div class="auth-kicker">Retail Intelligence</div>
                <h1 class="auth-title">Система продаж для сети салонов</h1>
                <p class="auth-copy">
                    Каждый салон видит только свой контур продаж и ежедневные выгрузки.
                    Руководитель получает общую картину по сети, сравнение салонов и единую управленческую панель.
                </p>
                <div class="feature-list">
                    <div class="feature-chip">Ежедневная загрузка файлов из 1С по салонам</div>
                    <div class="feature-chip">Роли доступа: салон и руководитель</div>
                    <div class="feature-chip">Сводка по сети, ABC, маржинальность и динамика</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right_col:
        auth_notice = st.session_state.pop("auth_notice", "")
        if auth_notice:
            st.warning(auth_notice)

        if not has_users():
            st.subheader("Первичная настройка")
            st.caption("Создайте первого пользователя-руководителя. Для входа потребуется email или телефон.")
            with st.form("bootstrap_admin_form"):
                username = st.text_input("Логин руководителя")
                display_name = st.text_input("Имя")
                email = st.text_input("Email")
                phone = st.text_input("Телефон")
                password = st.text_input("Пароль", type="password")
                password_confirm = st.text_input("Подтверждение пароля", type="password")
                submitted = st.form_submit_button("Создать руководителя", use_container_width=True)

            if submitted:
                if password != password_confirm:
                    st.error("Пароли не совпадают.")
                else:
                    try:
                        user = bootstrap_first_admin(
                            username=username,
                            password=password,
                            display_name=display_name,
                            email=email,
                            phone=phone,
                        )
                        st.session_state["auth_user"] = user
                        set_persistent_auth_token(create_auth_session(user["username"]))
                        audit_event(
                            action="auth.bootstrap_admin",
                            user_id=user["username"],
                            details={
                                "role": user["role"],
                                "has_email": bool(user.get("email")),
                                "has_phone": bool(user.get("phone")),
                            },
                        )
                        audit_event(
                            action="auth.login",
                            user_id=user["username"],
                            details={
                                "method": "bootstrap",
                                "role": user["role"],
                            },
                        )
                        st.success("Руководитель создан. Выполняю вход.")
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
            st.stop()

        st.subheader("Вход в систему")
        with st.form("login_form"):
            identifier = st.text_input("Email или телефон")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", use_container_width=True)

        if submitted:
            user = authenticate_user(identifier, password)
            if not user:
                st.error("Неверный email, телефон или пароль.")
            else:
                st.session_state["auth_user"] = user
                set_persistent_auth_token(create_auth_session(user["username"]))
                audit_event(
                    action="auth.login",
                    user_id=user["username"],
                    details={
                        "method": "password",
                        "role": user["role"],
                    },
                )
                st.rerun()

    st.stop()


@st.cache_data(show_spinner=False)
def cached_load_input_file(
    file_bytes: bytes,
    filename: str,
    csv_separator: str,
    csv_encoding: str,
    sheet_name: str | int | None,
) -> pd.DataFrame:
    return load_input_file(
        file_bytes,
        filename,
        csv_separator=csv_separator,
        csv_encoding=csv_encoding,
        sheet_name=sheet_name,
    )


@st.cache_data(show_spinner=False)
def cached_prepare_sales_data(
    frame: pd.DataFrame,
    mapping_items: tuple[tuple[str, str | None], ...],
):
    mapping = dict(mapping_items)
    return prepare_sales_data(frame, mapping)


@st.cache_data(show_spinner=False)
def cached_load_archive_data(selected_salons: tuple[str, ...]):
    return load_archive_data(salons=list(selected_salons))


def select_column(
    label: str,
    columns: list[str],
    default_value: str | None,
    *,
    help_text: str | None = None,
) -> str | None:
    options = ["Не использовать", *columns]
    default_index = options.index(default_value) if default_value in options else 0
    selection = st.selectbox(label, options=options, index=default_index, help=help_text)
    return None if selection == "Не использовать" else selection


def build_download_frame(selected_mapping: dict[str, str | None]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Поле в приложении": DISPLAY_NAMES[key],
                "Колонка в файле": value or "Не используется",
            }
            for key, value in selected_mapping.items()
        ]
    )


DISPLAY_COLUMN_NAMES = {
    "date": "Дата",
    "Дата": "Дата",
    "product": "Товар",
    "Номенклатура": "Товар",
    "group_name": "Позиция",
    "category": "Категория",
    "Категория": "Категория",
    "manager": "Менеджер",
    "salon": "Салон",
    "revenue": "Выручка",
    "Доход": "Выручка",
    "cost": "Себестоимость",
    "Себестоимость": "Себестоимость",
    "margin": "Маржа",
    "Прибыль": "Маржа",
    "margin_pct": "Маржа, %",
    "quantity": "Количество",
    "Количество": "Количество",
    "month": "Месяц",
    "month_label": "Месяц",
    "product_count": "Товаров",
    "sales_lines": "Строк продаж",
    "line_count": "Строк",
    "abc_basis": "База ABC",
    "abc_class": "Класс ABC",
    "share_pct": "Доля, %",
    "cum_share_pct": "Накопительная доля, %",
    "revenue_change_pct": "Изменение выручки, %",
    "margin_change_pct": "Изменение маржи, %",
    "report_date": "Дата отчёта",
    "source_filename": "Файл",
    "uploaded_at": "Загружен",
    "username": "Логин",
    "display_name": "Имя",
    "role": "Роль",
    "phone": "Телефон",
    "email": "Email",
    "created_at": "Создан",
    "Группа": "Группа",
    "Путь категории": "Путь категории",
    "Скидка": "Скидка",
    "Сумма НДС": "НДС",
    "Сумма НСП": "НСП",
    "Всего": "Итого",
}


def _normalized_label(value: object) -> str:
    return str(value).strip().casefold().replace("_", " ")


def _is_money_label(label: str) -> bool:
    normalized = _normalized_label(label)
    return normalized.startswith(
        (
            "выручка",
            "себестоимость",
            "маржа",
            "доход",
            "прибыль",
            "изменение выручки",
            "изменение маржи",
            "ндс",
            "нсп",
            "итого",
        )
    )


def _is_percent_label(label: str) -> bool:
    normalized = _normalized_label(label)
    return "%" in label or normalized.startswith(
        (
            "доля",
            "накопительная доля",
            "маржа, %",
            "изменение выручки, %",
            "изменение маржи, %",
            "изменение количества, %",
        )
    )


def _is_count_label(label: str) -> bool:
    normalized = _normalized_label(label)
    return normalized.startswith(
        (
            "количество",
            "изменение количества",
            "товаров",
            "строк",
            "sku",
        )
    )


def _is_date_label(label: str) -> bool:
    normalized = _normalized_label(label)
    return normalized in {"дата", "дата отчёта", "создан", "загружен"}


def _is_month_label(label: str) -> bool:
    return _normalized_label(label) == "месяц"


def _format_datetime_value(value: object, *, include_time: bool) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.strftime("%d.%m.%Y %H:%M" if include_time else "%d.%m.%Y")


def _coerce_float_for_display(value: object) -> float:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)

    text = str(value).strip()
    if not text:
        raise ValueError("Пустое значение нельзя преобразовать в число.")

    text = (
        text.replace("₽", "")
        .replace("сом.", "")
        .replace("сом", "")
        .replace("KGS", "")
        .replace("kgs", "")
        .replace("%", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    numeric = pd.to_numeric(text, errors="coerce")
    if pd.isna(numeric):
        raise ValueError(f"Не удалось преобразовать значение в число: {value}")

    return float(numeric)


def _make_unique_label(label: str, source_column: str, used_labels: dict[str, int]) -> str:
    if label not in used_labels:
        used_labels[label] = 1
        return label

    fallback_label = DISPLAY_COLUMN_NAMES.get(source_column, str(source_column))
    if fallback_label not in used_labels:
        used_labels[fallback_label] = 1
        return fallback_label

    suffix = used_labels[label] + 1
    unique_label = f"{label} ({suffix})"
    while unique_label in used_labels:
        suffix += 1
        unique_label = f"{label} ({suffix})"

    used_labels[label] = suffix
    used_labels[unique_label] = 1
    return unique_label


def format_display_frame(
    frame: pd.DataFrame,
    rename_map: dict[str, str] | None = None,
    *,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    view = frame.copy()
    if columns is not None:
        available_columns = [column for column in columns if column in view.columns]
        view = view[available_columns]

    formatted_columns: dict[str, pd.Series] = {}
    used_labels: dict[str, int] = {}

    for source_column in view.columns:
        if rename_map and source_column in rename_map:
            preferred_label = rename_map[source_column]
        else:
            preferred_label = DISPLAY_COLUMN_NAMES.get(source_column, str(source_column))

        final_label = _make_unique_label(preferred_label, source_column, used_labels)
        series = view[source_column]

        if _is_date_label(final_label):
            include_time = _normalized_label(final_label) in {"создан", "загружен"}
            formatted_columns[final_label] = series.map(
                lambda value: "—" if is_missing(value) else _format_datetime_value(value, include_time=include_time)
            )
            continue

        if _is_month_label(final_label):
            formatted_columns[final_label] = series.map(
                lambda value: "—"
                if is_missing(value)
                else (
                    pd.to_datetime(value, errors="coerce").strftime("%Y-%m")
                    if pd.notna(pd.to_datetime(value, errors="coerce"))
                    else str(value)
                )
            )
            continue

        if _is_percent_label(final_label):
            formatted_columns[final_label] = series.map(
                lambda value: "—" if is_missing(value) else format_percent(_coerce_float_for_display(value))
            )
            continue

        if _is_money_label(final_label):
            formatted_columns[final_label] = series.map(
                lambda value: "—" if is_missing(value) else format_money(_coerce_float_for_display(value))
            )
            continue

        if _is_count_label(final_label):
            formatted_columns[final_label] = series.map(
                lambda value: "—" if is_missing(value) else format_number(_coerce_float_for_display(value))
            )
            continue

        formatted_columns[final_label] = series.map(lambda value: "—" if is_missing(value) else value)

    return pd.DataFrame(formatted_columns, index=view.index)


def to_display_table(frame: pd.DataFrame, rename_map: dict[str, str]) -> pd.DataFrame:
    visible_columns = [column for column in rename_map if column in frame.columns]
    if not visible_columns:
        return format_display_frame(frame)
    return format_display_frame(frame, rename_map, columns=visible_columns)


def render_access_tab(registered_salons: list[str]) -> None:
    users = pd.DataFrame(list_users())
    salons_table = pd.DataFrame({"Салон": registered_salons}) if registered_salons else pd.DataFrame(columns=["Салон"])

    left_col, right_col = st.columns([1.15, 1], gap="large")

    with left_col:
        with st.container(border=True):
            st.markdown('<div class="panel-title">Пользователи</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="panel-caption">Список логинов и назначенных ролей в системе.</div>',
                unsafe_allow_html=True,
            )
            if users.empty:
                st.info("Пользователей пока нет.")
            else:
                display_users = users.rename(
                    columns={
                        "username": "Логин",
                        "display_name": "Имя",
                        "role": "Роль",
                        "salon": "Салон",
                        "email": "Email",
                        "phone": "Телефон",
                        "created_at": "Создан",
                    }
                )
                display_users["Роль"] = display_users["Роль"].map(role_label)
                st.dataframe(display_users, use_container_width=True, hide_index=True)

        if False:
            st.markdown('<div class="panel-title">Салоны</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="panel-caption">Реестр салонов, доступных для загрузок и назначения пользователям.</div>',
                unsafe_allow_html=True,
            )
            if salons_table.empty:
                st.info("Салоны ещё не зарегистрированы.")
            else:
                st.dataframe(salons_table, use_container_width=True, hide_index=True)

    with right_col:
        with st.container(border=True):
            st.markdown('<div class="panel-title">Создать пользователя</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="panel-caption">Создайте отдельный логин для салона или ещё одного руководителя.</div>',
                unsafe_allow_html=True,
            )
            with st.form("create_user_form"):
                username = st.text_input("Логин")
                display_name = st.text_input("Имя пользователя")
                role_choice = st.selectbox("Роль", options=["salon", "manager"], format_func=role_label)
                email = st.text_input("Email")
                phone = st.text_input("Телефон")
                salon_name = st.text_input("Салон", disabled=role_choice != "salon")
                password = st.text_input("Пароль", type="password")
                password_confirm = st.text_input("Подтверждение пароля", type="password")
                submitted = st.form_submit_button("Создать пользователя", use_container_width=True)

            if submitted:
                if password != password_confirm:
                    st.error("Пароли не совпадают.")
                else:
                    try:
                        if role_choice == "salon":
                            save_salon(salon_name)
                        create_user(
                            username=username,
                            password=password,
                            role=role_choice,
                            display_name=display_name,
                            email=email,
                            phone=phone,
                            salon=salon_name,
                        )
                        st.success("Пользователь создан.")
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))

        with st.container(border=True):
            st.markdown('<div class="panel-title">Сброс пароля</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="panel-caption">Быстрый сброс пароля для действующего пользователя.</div>',
                unsafe_allow_html=True,
            )
            user_options = users["username"].tolist() if not users.empty else []
            with st.form("reset_password_form"):
                selected_username = st.selectbox("Пользователь", options=user_options) if user_options else st.selectbox("Пользователь", options=["Нет пользователей"], disabled=True)
                new_password = st.text_input("Новый пароль", type="password")
                new_password_confirm = st.text_input("Подтверждение нового пароля", type="password")
                submitted = st.form_submit_button("Сменить пароль", use_container_width=True, disabled=not user_options)

            if submitted:
                if new_password != new_password_confirm:
                    st.error("Пароли не совпадают.")
                else:
                    try:
                        set_user_password(selected_username, new_password)
                        st.success("Пароль обновлён.")
                    except Exception as error:
                        st.error(str(error))


def render_admin_tab(current_user: dict[str, str], registered_salons: list[str]) -> None:
    users = pd.DataFrame(list_users())
    salons_table = pd.DataFrame({"Салон": registered_salons}) if registered_salons else pd.DataFrame(columns=["Салон"])

    if users.empty:
        users = pd.DataFrame(columns=["username", "display_name", "role", "salon", "email", "phone", "created_at"])

    admin_count = int((users["role"] == "admin").sum()) if "role" in users else 0
    manager_count = int((users["role"] == "manager").sum()) if "role" in users else 0
    salon_user_count = int((users["role"] == "salon").sum()) if "role" in users else 0

    display_users = users.rename(
        columns={
            "username": "Логин",
            "display_name": "Имя",
            "role": "Роль",
            "salon": "Салон",
            "email": "Email",
            "phone": "Телефон",
            "created_at": "Создан",
        }
    )
    if not display_users.empty:
        display_users["Роль"] = display_users["Роль"].map(role_label)

    render_html_block(
        f"""
        <div class="admin-stat-grid">
            <div class="admin-stat">
                <div class="admin-stat-label">Салоны</div>
                <div class="admin-stat-value">{len(registered_salons)}</div>
            </div>
            <div class="admin-stat">
                <div class="admin-stat-label">Администраторы</div>
                <div class="admin-stat-value">{admin_count}</div>
            </div>
            <div class="admin-stat">
                <div class="admin-stat-label">Руководители</div>
                <div class="admin-stat-value">{manager_count}</div>
            </div>
            <div class="admin-stat">
                <div class="admin-stat-label">Пользователи салонов</div>
                <div class="admin-stat-value">{salon_user_count}</div>
            </div>
        </div>
        """
    )

    st.info(
        "Как работать: 1. Добавьте салон. 2. Перейдите в «Пользователи». "
        "3. Выберите роль и создайте аккаунт. Для входа пользователю достаточно логина, email или телефона."
    )
    st.caption(f"В систему вы вошли как: {current_user['display_name']} ({role_label(current_user['role'])})")
    flash_message = st.session_state.pop("admin_flash_message", "")
    if flash_message:
        st.success(flash_message)

    section = st.radio(
        "Раздел управления",
        options=["Салоны", "Пользователи", "Пароли"],
        horizontal=True,
        key="admin_section_switch",
        label_visibility="collapsed",
    )

    if section == "Салоны":
        salons_left, salons_right = st.columns([0.95, 1.15], gap="large")

        with salons_left:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Добавить салон</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Введите название салона так, как оно должно отображаться в аналитике и фильтрах.</div>',
                    unsafe_allow_html=True,
                )
                new_salon_name = st.text_input(
                    "Название салона",
                    key="admin_new_salon_name",
                    placeholder="Например: Омск | Мега",
                    help="После создания этот салон появится в выборе при создании пользователя и загрузке данных.",
                )
                if st.button("Добавить салон", key="admin_add_salon_button", use_container_width=True):
                    normalized_salon_name = new_salon_name.strip()
                    existing_salons = {salon.casefold() for salon in registered_salons}

                    if not normalized_salon_name:
                        st.error("Введите название салона.")
                    elif normalized_salon_name.casefold() in existing_salons:
                        st.warning("Такой салон уже существует.")
                    else:
                        save_salon(normalized_salon_name)
                        audit_event(
                            action="salon.create",
                            user_id=current_user["username"],
                            details={
                                "salon": normalized_salon_name,
                                "source": "admin_tab",
                            },
                        )
                        st.session_state["admin_new_salon_name"] = ""
                        st.session_state["admin_flash_message"] = f"Салон «{normalized_salon_name}» добавлен."
                        st.rerun()

        with salons_right:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Список салонов</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Все салоны, доступные для выгрузок и назначения пользователей.</div>',
                    unsafe_allow_html=True,
                )
                if salons_table.empty:
                    st.info("Салоны пока не зарегистрированы.")
                else:
                    st.dataframe(salons_table, use_container_width=True, hide_index=True)

            with st.container(border=True):
                st.markdown('<div class="panel-title">Удалить салон</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Удаление салона можно делать безопасно: либо только пустой салон, либо полностью вместе с архивом и пользователями салона.</div>',
                    unsafe_allow_html=True,
                )

                salon_to_delete = (
                    st.selectbox("Какой салон удалить", options=registered_salons, key="admin_delete_salon_name")
                    if registered_salons
                    else st.selectbox("Какой салон удалить", options=["Нет салонов"], disabled=True, key="admin_delete_salon_name_empty")
                )

                if registered_salons:
                    related_users_count = int(
                        (
                            users["salon"].fillna("").astype(str).str.casefold() == str(salon_to_delete).strip().casefold()
                        ).sum()
                    ) if not users.empty else 0
                    related_uploads_count = count_uploads_for_salon(str(salon_to_delete))
                    st.caption(
                        f"Пользователей салона: {related_users_count} | Архивных выгрузок: {related_uploads_count}"
                    )

                    delete_salon_users = st.checkbox(
                        "Удалить пользователей этого салона",
                        key="admin_delete_salon_users",
                    )
                    delete_salon_uploads = st.checkbox(
                        "Удалить архив выгрузок этого салона",
                        key="admin_delete_salon_uploads",
                    )
                    salon_confirm_text = st.text_input(
                        "Для подтверждения введите название салона",
                        key="admin_delete_salon_confirm",
                        placeholder=str(salon_to_delete),
                    )

                    if st.button(
                        "Удалить салон",
                        key="admin_delete_salon_button",
                        use_container_width=True,
                        type="secondary",
                    ):
                        if salon_confirm_text.strip() != str(salon_to_delete).strip():
                            st.error("Введите точное название салона для подтверждения удаления.")
                        elif related_users_count and not delete_salon_users:
                            st.error("У салона есть пользователи. Включите удаление пользователей салона или сначала удалите их вручную.")
                        else:
                            try:
                                deleted_users = delete_users_by_salon(str(salon_to_delete)) if delete_salon_users else 0
                                delete_result = delete_salon(
                                    str(salon_to_delete),
                                    remove_uploads=delete_salon_uploads,
                                )
                                audit_event(
                                    action="salon.delete",
                                    user_id=current_user["username"],
                                    details={
                                        "salon": str(salon_to_delete),
                                        "deleted_users": int(deleted_users),
                                        "deleted_uploads": int(delete_result["deleted_uploads"]),
                                        "remove_users": bool(delete_salon_users),
                                        "remove_uploads": bool(delete_salon_uploads),
                                        "source": "admin_tab",
                                    },
                                )
                                st.session_state["admin_delete_salon_confirm"] = ""
                                st.session_state["admin_delete_salon_users"] = False
                                st.session_state["admin_delete_salon_uploads"] = False
                                st.session_state["admin_flash_message"] = (
                                    f"Салон «{salon_to_delete}» удалён. "
                                    f"Удалено пользователей: {deleted_users}. "
                                    f"Удалено выгрузок: {delete_result['deleted_uploads']}."
                                )
                                st.rerun()
                            except Exception as error:
                                st.error(str(error))

    elif section == "Пользователи":
        users_left, users_right = st.columns([1.05, 1], gap="large")

        with users_left:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Создать пользователя</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Выберите роль, заполните данные и сохраните нового пользователя.</div>',
                    unsafe_allow_html=True,
                )

                role_choice = st.selectbox(
                    "Роль пользователя",
                    options=["manager", "salon", "admin"],
                    format_func=role_label,
                    key="admin_create_role",
                )

                identity_col, name_col = st.columns(2)
                with identity_col:
                    username = st.text_input(
                        "Логин",
                        key="admin_create_username",
                        placeholder="Например: ivanov",
                    )
                with name_col:
                    display_name = st.text_input(
                        "Имя в системе",
                        key="admin_create_display_name",
                        placeholder="Например: Иван Иванов",
                    )

                contact_col, phone_col = st.columns(2)
                with contact_col:
                    email = st.text_input(
                        "Email",
                        key="admin_create_email",
                        placeholder="user@company.ru",
                    )
                with phone_col:
                    phone = st.text_input(
                        "Телефон",
                        key="admin_create_phone",
                        placeholder="+7 999 123-45-67",
                    )

                selected_salon = ""
                if role_choice == "salon":
                    selected_salon = st.selectbox(
                        "Какой салон будет видеть пользователь",
                        options=registered_salons if registered_salons else ["Сначала создайте салон"],
                        key="admin_create_salon",
                        disabled=not bool(registered_salons),
                    )
                else:
                    st.caption("Для администратора и руководителя выбор салона не нужен.")

                password_col, confirm_col = st.columns(2)
                with password_col:
                    password = st.text_input("Пароль", type="password", key="admin_create_password")
                with confirm_col:
                    password_confirm = st.text_input("Повтор пароля", type="password", key="admin_create_password_confirm")

                create_user_disabled = role_choice == "salon" and not bool(registered_salons)
                if create_user_disabled:
                    st.warning("Сначала добавьте хотя бы один салон, потом можно будет создать пользователя салона.")

                if st.button(
                    "Создать пользователя",
                    key="admin_create_user_button",
                    use_container_width=True,
                    disabled=create_user_disabled,
                ):
                    if password != password_confirm:
                        st.error("Пароли не совпадают.")
                    else:
                        try:
                            create_user(
                                username=username,
                                password=password,
                                role=role_choice,
                                display_name=display_name,
                                email=email,
                                phone=phone,
                                salon=selected_salon,
                            )
                            audit_event(
                                action="user.create",
                                user_id=current_user["username"],
                                details={
                                    "username": username.strip(),
                                    "role": role_choice,
                                    "salon": selected_salon if role_choice == "salon" else "",
                                    "has_email": bool(email.strip()),
                                    "has_phone": bool(phone.strip()),
                                    "source": "admin_tab",
                                },
                            )
                            for key in (
                                "admin_create_username",
                                "admin_create_display_name",
                                "admin_create_email",
                                "admin_create_phone",
                                "admin_create_password",
                                "admin_create_password_confirm",
                                "admin_create_salon",
                            ):
                                st.session_state.pop(key, None)
                            st.session_state.pop("admin_create_role", None)
                            st.session_state["admin_flash_message"] = (
                                f"Пользователь «{display_name or username}» с ролью «{role_label(role_choice)}» создан."
                            )
                            st.rerun()
                        except Exception as error:
                            st.error(str(error))

        with users_right:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Реестр пользователей</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Проверяйте роли, контакты и привязку пользователей к салонам.</div>',
                    unsafe_allow_html=True,
                )
                if display_users.empty:
                    st.info("Пользователи пока не созданы.")
                else:
                    st.dataframe(display_users, use_container_width=True, hide_index=True)

            with st.container(border=True):
                st.markdown('<div class="panel-title">Удалить пользователя</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Удаление сразу закрывает доступ пользователю. Текущего администратора под собой удалить нельзя.</div>',
                    unsafe_allow_html=True,
                )

                available_usernames = users["username"].tolist() if not users.empty else []
                selected_user_to_delete = (
                    st.selectbox("Какого пользователя удалить", options=available_usernames, key="admin_delete_username")
                    if available_usernames
                    else st.selectbox("Какого пользователя удалить", options=["Нет пользователей"], disabled=True, key="admin_delete_username_empty")
                )

                if available_usernames:
                    selected_user_row = users.loc[users["username"] == selected_user_to_delete].iloc[0]
                    st.caption(
                        f"Роль: {role_label(str(selected_user_row['role']))} | "
                        f"Салон: {str(selected_user_row.get('salon', '') or 'Не привязан')}"
                    )
                    if str(selected_user_to_delete).strip().casefold() == current_user["username"].strip().casefold():
                        st.warning("Вы сейчас вошли под этим пользователем. Для безопасности самоуничтожение аккаунта отключено.")

                    user_confirm_text = st.text_input(
                        "Для подтверждения введите логин пользователя",
                        key="admin_delete_user_confirm",
                        placeholder=str(selected_user_to_delete),
                    )

                    if st.button(
                        "Удалить пользователя",
                        key="admin_delete_user_button",
                        use_container_width=True,
                        type="secondary",
                        disabled=str(selected_user_to_delete).strip().casefold() == current_user["username"].strip().casefold(),
                    ):
                        if user_confirm_text.strip() != str(selected_user_to_delete).strip():
                            st.error("Введите точный логин пользователя для подтверждения удаления.")
                        else:
                            try:
                                deleted_user = delete_user(
                                    str(selected_user_to_delete),
                                    actor_username=current_user["username"],
                                )
                                audit_event(
                                    action="user.delete",
                                    user_id=current_user["username"],
                                    details={
                                        "username": str(deleted_user.get("username", "")),
                                        "role": str(deleted_user.get("role", "")),
                                        "salon": str(deleted_user.get("salon", "")),
                                        "source": "admin_tab",
                                    },
                                )
                                st.session_state["admin_delete_user_confirm"] = ""
                                st.session_state["admin_flash_message"] = (
                                    f"Пользователь «{deleted_user['display_name']}» удалён."
                                )
                                st.rerun()
                            except Exception as error:
                                st.error(str(error))

    else:
        password_left, password_right = st.columns([0.95, 1.05], gap="large")

        with password_left:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Сброс пароля</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Выберите пользователя и задайте ему новый пароль.</div>',
                    unsafe_allow_html=True,
                )
                user_options = users["username"].tolist() if not users.empty else []
                selected_username = (
                    st.selectbox("Пользователь", options=user_options, key="admin_reset_username")
                    if user_options
                    else st.selectbox("Пользователь", options=["Нет пользователей"], disabled=True, key="admin_reset_username_empty")
                )
                password_col, confirm_col = st.columns(2)
                with password_col:
                    new_password = st.text_input("Новый пароль", type="password", key="admin_reset_password")
                with confirm_col:
                    new_password_confirm = st.text_input(
                        "Повтор нового пароля",
                        type="password",
                        key="admin_reset_password_confirm",
                    )

                if st.button(
                    "Сменить пароль",
                    key="admin_reset_password_button",
                    use_container_width=True,
                    disabled=not user_options,
                ):
                    if new_password != new_password_confirm:
                        st.error("Пароли не совпадают.")
                    else:
                        try:
                            set_user_password(selected_username, new_password)
                            audit_event(
                                action="user.password_reset",
                                user_id=current_user["username"],
                                details={
                                    "username": str(selected_username),
                                    "source": "admin_tab",
                                },
                            )
                            st.session_state.pop("admin_reset_password", None)
                            st.session_state.pop("admin_reset_password_confirm", None)
                            st.session_state["admin_flash_message"] = f"Пароль для пользователя «{selected_username}» обновлён."
                            st.rerun()
                        except Exception as error:
                            st.error(str(error))

        with password_right:
            with st.container(border=True):
                st.markdown('<div class="panel-title">Что важно</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="panel-caption">Короткая памятка по созданию пользователей и доступам.</div>',
                    unsafe_allow_html=True,
                )
                st.write("1. Сначала создайте салон, если нужен пользователь салона.")
                st.write("2. Затем в разделе «Пользователи» выберите роль и заполните поля.")
                st.write("3. Нужно указать хотя бы один контакт: email или телефон.")
                st.write("4. Для входа можно использовать логин, email или телефон.")


def render_intro() -> None:
    render_html_block(
        """
        <div class="dashboard-hero">
            <div class="dashboard-eyebrow">Локальная аналитика продаж</div>
            <h1 class="dashboard-title">Панель продаж для выгрузок из 1С</h1>
            <p class="dashboard-subtitle">
                Каждый салон может загружать ежедневные Excel или CSV из 1С в свой архив,
                а руководитель может смотреть все салоны сразу и общую картину по сети.
            </p>
        </div>
        """
    )

    sample_path = Path("sample_sales_data.csv")
    actions_col, sample_col = st.columns([1, 1])

    with actions_col:
        st.info("Начните с загрузки своего файла. Поддерживаются `xlsx`, `xls`, `csv`.")

    with sample_col:
        if sample_path.exists():
            st.download_button(
                "Скачать пример файла",
                data=sample_path.read_bytes(),
                file_name=sample_path.name,
                mime="text/csv",
                use_container_width=True,
            )

    with st.expander("Какие поля желательно иметь в выгрузке"):
        st.markdown(
            """
            - Обязательные: `Дата`, `Товар`.
            - Для выручки: `Выручка` или связка `Цена за единицу` + `Количество`.
            - Для маржинальности: `Себестоимость` или `Маржа`.
            - Дополнительно: `Категория`, `Менеджер`.
            - Русские и английские названия колонок поддерживаются, а спорные поля можно выбрать вручную.
            """
        )


def render_dataset_hero(
    filename: str,
    data: pd.DataFrame,
    overview: dict[str, float],
    selected_categories: list[str] | None,
    selected_managers: list[str] | None,
    current_user: dict[str, str],
) -> None:
    min_date = data["date"].min().strftime("%d.%m.%Y")
    max_date = data["date"].max().strftime("%d.%m.%Y")

    category_text = "Все категории"
    manager_text = "Все менеджеры"

    if selected_categories:
        category_text = ", ".join(selected_categories[:3])
        if len(selected_categories) > 3:
            category_text += f" +{len(selected_categories) - 3}"

    if selected_managers:
        manager_text = ", ".join(selected_managers[:3])
        if len(selected_managers) > 3:
            manager_text += f" +{len(selected_managers) - 3}"

    chips = [
        f"Роль: {role_label(current_user['role'])}",
        f"Источник: {escape(filename)}",
        f"Период: {min_date} - {max_date}",
        f"Строк в анализе: {format_number(overview['line_count'])}",
        f"Товаров: {format_number(overview['product_count'])}",
        f"Категории: {escape(category_text)}",
        f"Менеджеры: {escape(manager_text)}",
    ]
    chips_html = "".join(f'<span class="scope-chip">{chip}</span>' for chip in chips)

    render_html_block(
        f"""
        <div class="dashboard-hero">
            <div class="dashboard-eyebrow">Единая панель продаж</div>
            <h1 class="dashboard-title">Картина по продажам в одном экране</h1>
            <p class="dashboard-subtitle">
                Здесь собраны дневные загрузки, управленческие KPI, точки роста и зоны риска.
                Салон видит только свой контур, руководитель работает со всей сетью.
            </p>
            <div class="scope-strip">{chips_html}</div>
        </div>
        """
    )


def build_insights(
    overview: dict[str, float],
    monthly_summary: pd.DataFrame,
    category_summary: pd.DataFrame,
    manager_summary: pd.DataFrame,
    product_summary: pd.DataFrame,
    abc_data: pd.DataFrame,
    salon_summary: pd.DataFrame | None = None,
    anomalies: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    insights: list[tuple[str, str]] = []

    if len(monthly_summary) >= 2:
        latest_month = monthly_summary.iloc[-1]
        previous_month = monthly_summary.iloc[-2]
        insights.append(
            (
                f"Последний месяц: {latest_month['month_label']}",
                f"Выручка {format_money(latest_month['revenue'])}, изменение к {previous_month['month_label']} "
                f"составило {format_percent(latest_month['revenue_change_pct'])}.",
            )
        )

    if not category_summary.empty and overview["total_revenue"] > 0:
        top_category = category_summary.iloc[0]
        share = top_category["revenue"] / overview["total_revenue"] * 100
        insights.append(
            (
                f"Главная категория: {top_category['group_name']}",
                f"Даёт {format_money(top_category['revenue'])} и формирует {format_percent(share)} всей выручки.",
            )
        )

    if not manager_summary.empty:
        top_manager = manager_summary.iloc[0]
        insights.append(
            (
                f"Лидер по продажам: {top_manager['group_name']}",
                f"У менеджера {format_money(top_manager['revenue'])} выручки и {format_money(top_manager['margin'])} маржи.",
            )
        )

    if salon_summary is not None and not salon_summary.empty and len(salon_summary) > 1:
        top_salon = salon_summary.iloc[0]
        insights.append(
            (
                f"Сильнейший салон: {top_salon['group_name']}",
                f"Формирует {format_money(top_salon['revenue'])} выручки и {format_money(top_salon['margin'])} маржи.",
            )
        )

    if not product_summary.empty and not product_summary["margin_pct"].isna().all():
        low_margin_count = int((product_summary["margin_pct"] < 20).fillna(False).sum())
        insights.append(
            (
                "Риски по марже",
                f"SKU с маржой ниже 20%: {low_margin_count}. Проверьте блок с зонами риска во вкладке `Маржинальность`.",
            )
        )

    if not abc_data.empty:
        a_share = abc_data.loc[abc_data["abc_class"] == "A", "share_pct"].sum()
        a_count = int((abc_data["abc_class"] == "A").sum())
        insights.append(
            (
                "Концентрация выручки",
                f"Класс A содержит {a_count} SKU и формирует {format_percent(a_share)} выбранной ABC-метрики.",
            )
        )

    if anomalies:
        for month_label, description in anomalies[:1]:
            insights.append((f"⚠ Аномалия: {month_label}", description))

    return insights[:6]


def render_insight_panel(insights: list[tuple[str, str]]) -> None:
    with st.container(border=True):
        render_panel_header(
            "Что важно сейчас",
            "Правая колонка для быстрых управленческих выводов. Сюда лучше смотреть после рамки периода и до перехода к подробным графикам и таблицам.",
        )
        for title, body in insights:
            render_html_block(
                f"""
                <div class="insight-item">
                    <div class="insight-title">{escape(title)}</div>
                    <div class="insight-body">{escape(body)}</div>
                </div>
                """
            )


def build_movement_chart(comparison: pd.DataFrame, left_month: str, right_month: str) -> go.Figure:
    movement = comparison.copy()
    movement["abs_revenue_delta"] = movement["revenue_delta"].abs()
    movement = movement.nlargest(12, "abs_revenue_delta").sort_values("revenue_delta")

    figure = px.bar(
        movement,
        x="revenue_delta",
        y="group_name",
        orientation="h",
        color="revenue_delta",
        color_continuous_scale=["#991B1B", "#FACC15", "#0F766E"],
        labels={"group_name": "Товар", "revenue_delta": "Изменение выручки"},
        title=f"Главные движения: {right_month} к {left_month}",
    )
    figure.update_layout(coloraxis_showscale=False)
    return polish_figure(figure, height=460)


promoted_admin = promote_first_manager_to_admin()
if promoted_admin and st.session_state.get("auth_user", {}).get("username", "").casefold() == promoted_admin["username"].casefold():
    st.session_state["auth_user"] = promoted_admin


current_user = render_auth_gate()

with st.sidebar:
    st.markdown(f"**Пользователь:** {current_user['display_name']}")
    st.caption(f"Роль: {role_label(current_user['role'])}")
    if current_user.get("email"):
        st.caption(f"Email: {current_user['email']}")
    elif current_user.get("phone"):
        st.caption(f"Телефон: {current_user['phone']}")
    if current_user.get("salon"):
        st.caption(f"Салон: {current_user['salon']}")
    if st.button("Выйти", use_container_width=True):
        revoke_auth_session(get_persistent_auth_token())
        clear_persistent_auth_token()
        st.session_state.pop("auth_user", None)
        st.rerun()

render_intro()
render_user_strip(current_user)

uploaded_file = None
raw_data: pd.DataFrame | None = None
selected_mapping: dict[str, str | None] = {}
prepared_result = None
archive_result = None
manifest_view = load_manifest()
registered_salons = load_salons()
analysis_source = ""
source_label = ""
selected_salon_name = ""
selected_categories: list[str] | None = None
selected_managers: list[str] | None = None
selected_salons_filter: list[str] | None = None
selected_salons_for_archive: list[str] = []
current_file_report_date = date.today()
auto_detected_report_date: date | None = None
sheet_name: str | int | None = 0
csv_separator = ";"
csv_encoding = "utf-8"
replace_existing_upload = True

if current_user["role"] == "salon":
    if not current_user.get("salon"):
        st.error("Для пользователя салона не назначен салон. Зайдите под руководителем и привяжите пользователя.")
        st.stop()
    selected_salon_name = current_user["salon"]
    manifest_view = manifest_view[manifest_view["salon"].astype(str) == selected_salon_name].copy()
else:
    manifest_view = manifest_view.copy()

if current_user["role"] == "salon" and selected_salon_name not in registered_salons:
    registered_salons = sorted([*registered_salons, selected_salon_name])

with st.sidebar:
    st.header("Режим работы")
    if current_user["role"] == "salon":
        work_mode = st.radio("Режим салона", ["Архив салона", "Новая выгрузка"], index=0)
        st.caption("Салон видит только свой архив и может загружать только свои файлы.")

        if work_mode == "Новая выгрузка":
            current_file_report_date = st.date_input("Дата отчёта", value=date.today())
            replace_existing_upload = st.checkbox("Заменять файл за эту дату", value=True)
            st.caption("Ниже на странице будет открыт отдельный блок для загрузки файла.")
    else:
        work_mode = st.radio(
            "Режим сети",
            ["Сводка по сети", "Загрузка салона", "Разовая загрузка"],
            index=0,
        )

        if work_mode == "Сводка по сети":
            selected_salons_for_archive = st.multiselect(
                "Салоны в отчёте",
                registered_salons,
                default=registered_salons,
            )
            st.caption("Руководитель видит выбранные салоны и общую картину по сети.")
        elif work_mode == "Загрузка салона":
            salon_options = [*registered_salons, "Новый салон"] if registered_salons else ["Новый салон"]
            chosen_salon = st.selectbox("Салон для загрузки", options=salon_options)
            if chosen_salon == "Новый салон":
                selected_salon_name = st.text_input("Название нового салона").strip()
            else:
                selected_salon_name = chosen_salon

            current_file_report_date = st.date_input("Дата отчёта", value=date.today())
            replace_existing_upload = st.checkbox("Заменять файл за эту дату", value=True)
            st.caption("Ниже на странице будет открыт отдельный блок для загрузки файла.")
        else:
            st.caption("Разовый анализ одного файла без сохранения в архив.")
            st.caption("Ниже на странице будет открыт отдельный блок для загрузки файла.")

with st.sidebar:
    render_sidebar_navigation(current_user, work_mode)
    if can_manage_access(current_user):
        render_sidebar_admin_quick_actions(current_user, registered_salons)

upload_modes = {"Новая выгрузка", "Загрузка салона", "Разовая загрузка"}
upload_flash_message = st.session_state.pop("upload_flash_message", "")
if upload_flash_message:
    st.success(upload_flash_message)

if work_mode in upload_modes:
    with st.container(border=True):
        st.markdown('<div class="panel-title">Загрузка файла</div>', unsafe_allow_html=True)
        if current_user["role"] == "salon":
            st.markdown(
                '<div class="panel-caption">Загрузите ежедневную выгрузку вашего салона. После проверки её можно сохранить в архив салона.</div>',
                unsafe_allow_html=True,
            )
        elif work_mode == "Загрузка салона":
            st.markdown(
                '<div class="panel-caption">Выберите салон и загрузите для него файл. Руководитель может загрузить файл за любой салон сети.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="panel-caption">Загрузите файл для быстрого анализа без сохранения в архив.</div>',
                unsafe_allow_html=True,
            )

        upload_left, upload_right = st.columns([1.2, 1])
        with upload_left:
            uploaded_file = st.file_uploader("Файл из 1С", type=["xlsx", "xls", "csv"], key=f"main_uploader_{work_mode}")
        with upload_right:
            upload_scope = selected_salon_name or "Без привязки к салону"
            if work_mode in {"Новая выгрузка", "Загрузка салона"} and selected_salon_name:
                current_manifest = load_manifest()
                salon_manifest = current_manifest[current_manifest["salon"].astype(str) == selected_salon_name].copy()
                uploads_count = len(salon_manifest)
                latest_report_date = ""
                if not salon_manifest.empty:
                    latest_report_date = str(salon_manifest["report_date"].astype(str).max())
                status_lines = [
                    f"Контур загрузки: {upload_scope}",
                    f"Режим: {work_mode}",
                    f"Сохранено файлов в архиве: {uploads_count}",
                ]
                if latest_report_date:
                    status_lines.append(f"Последняя дата в архиве: {latest_report_date}")
                st.info("\n\n".join(status_lines))
            else:
                st.info(
                    f"Контур загрузки: {upload_scope}\n\n"
                    f"Режим: {work_mode}\n\n"
                    "Поддерживаются форматы Excel и CSV. Максимальный размер файла: 50 МБ."
                )

if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    try:
        filename = validate_uploaded_file(file_bytes, uploaded_file.name)
    except ValueError as error:
        st.error(str(error))
        st.stop()

    with st.sidebar:
        with st.expander("Параметры загрузки", expanded=False):
            if filename.lower().endswith(".csv"):
                csv_separator = st.selectbox("Разделитель CSV", options=[";", ",", "\t"], index=0)
                csv_encoding = st.selectbox("Кодировка CSV", options=["utf-8", "cp1251", "utf-8-sig"], index=0)
            else:
                sheet_names = list_excel_sheets(file_bytes)
                sheet_name = st.selectbox("Лист Excel", options=sheet_names, index=0)

    try:
        raw_data = cached_load_input_file(file_bytes, filename, csv_separator, csv_encoding, sheet_name)
    except Exception as error:
        st.error(f"Не удалось прочитать файл: {error}")
        st.stop()

    if raw_data.empty:
        st.warning("Файл прочитан, но в нём нет строк.")
        st.stop()

    columns = raw_data.columns.astype(str).tolist()
    guesses = guess_column_mapping(columns)

    with st.sidebar:
        with st.expander("Сопоставление колонок", expanded=False):
            st.caption("Если автоподбор ошибся, выберите нужные поля вручную.")
            selected_mapping = {
                "date": select_column("Дата", columns, guesses.get("date"), help_text="Обязательное поле."),
                "product": select_column("Товар", columns, guesses.get("product"), help_text="Обязательное поле."),
                "revenue": select_column("Выручка", columns, guesses.get("revenue")),
                "cost": select_column("Себестоимость", columns, guesses.get("cost")),
                "margin": select_column("Маржа", columns, guesses.get("margin")),
                "quantity": select_column("Количество", columns, guesses.get("quantity")),
                "unit_price": select_column("Цена за единицу", columns, guesses.get("unit_price")),
                "unit_cost": select_column("Себестоимость за единицу", columns, guesses.get("unit_cost")),
                "category": select_column("Категория", columns, guesses.get("category")),
                "manager": select_column("Менеджер", columns, guesses.get("manager")),
            }

    mapping_items = tuple(sorted(selected_mapping.items()))

    try:
        prepared_result = cached_prepare_sales_data(raw_data, mapping_items)
    except Exception as error:
        st.error(str(error))
        st.stop()

    detected_dates = prepared_result.data["date"].dropna().dt.date.unique().tolist()
    if len(detected_dates) == 1:
        auto_detected_report_date = detected_dates[0]
        if work_mode in {"Новая выгрузка", "Загрузка салона"} and current_file_report_date == date.today():
            current_file_report_date = auto_detected_report_date

    if work_mode in {"Новая выгрузка", "Загрузка салона"}:
        with st.container(border=True):
            render_panel_header(
                "Сохранение в архив",
                "После проверки файла нажмите кнопку ниже, чтобы закрепить выгрузку за выбранным салоном и датой. Сохранённые файлы попадают в архив и участвуют в сводной аналитике.",
            )
            if auto_detected_report_date is not None:
                st.caption(f"Дата, найденная в файле: {auto_detected_report_date.strftime('%d.%m.%Y')}")
            if not selected_salon_name:
                st.warning("Сначала выберите салон, иначе выгрузку нельзя сохранить в архив.")
            else:
                save_col, helper_col = st.columns([1.2, 1])
                with save_col:
                    if st.button("Сохранить выгрузку в архив", key="main_save_upload_button", use_container_width=True):
                        save_upload_with_feedback(
                            file_bytes=file_bytes,
                            filename=filename,
                            salon_name=selected_salon_name,
                            report_date=current_file_report_date,
                            mapping=selected_mapping,
                            csv_separator=csv_separator,
                            csv_encoding=csv_encoding,
                            sheet_name=sheet_name,
                            replace_existing=replace_existing_upload,
                            actor_username=current_user["username"],
                        )
                        st.rerun()
                with helper_col:
                    st.caption(
                        "Сохранение доступно только в режимах салонной загрузки. "
                        "В режиме разового анализа файл не попадает в архив."
                    )

        with st.sidebar:
            with st.expander("Сохранение в архив", expanded=True):
                if auto_detected_report_date is not None:
                    st.caption(f"Дата из файла: {auto_detected_report_date.strftime('%d.%m.%Y')}")
                if not selected_salon_name:
                    st.info("Укажите салон, чтобы сохранить выгрузку в архив.")
                else:
                    if st.button("Сохранить выгрузку в архив", key="sidebar_save_upload_button", use_container_width=True):
                        save_upload_with_feedback(
                            file_bytes=file_bytes,
                            filename=filename,
                            salon_name=selected_salon_name,
                            report_date=current_file_report_date,
                            mapping=selected_mapping,
                            csv_separator=csv_separator,
                            csv_encoding=csv_encoding,
                            sheet_name=sheet_name,
                            replace_existing=replace_existing_upload,
                            actor_username=current_user["username"],
                        )
                        st.rerun()

if work_mode == "Разовая загрузка":
    if prepared_result is None:
        st.info("Загрузите файл, чтобы открыть обзор.")
        st.stop()
    data = prepared_result.data.copy()
    source_label = filename
elif work_mode in {"Новая выгрузка", "Загрузка салона"}:
    if prepared_result is None:
        st.info("Загрузите файл, чтобы провести анализ.")
        st.stop()
    data = prepared_result.data.copy()
    if selected_salon_name:
        data["salon"] = selected_salon_name
    source_label = f"Текущая выгрузка: {filename}"
else:
    selected_archive_salons = []
    if current_user["role"] == "salon":
        selected_archive_salons = [selected_salon_name]
    else:
        selected_archive_salons = selected_salons_for_archive if selected_salons_for_archive else registered_salons

    archive_result = cached_load_archive_data(tuple(selected_archive_salons))
    manifest_view = archive_result.manifest.copy()
    data = archive_result.data.copy()

    for warning in archive_result.warnings:
        st.warning(warning)

    if data.empty:
        if current_user["role"] == "salon":
            st.info("У этого салона пока нет сохранённых выгрузок. Загрузите первый файл и сохраните его в архив.")
        else:
            st.info("В архиве пока нет данных по выбранным салонам.")
        st.stop()

    source_label = "Архив сети" if is_network_role(current_user["role"]) else f"Архив салона: {selected_salon_name}"

if prepared_result is not None and work_mode in {"Разовая загрузка", "Новая выгрузка", "Загрузка салона"}:
    for warning in prepared_result.warnings:
        st.warning(warning)

with st.sidebar:
    st.header("Фильтры")

    min_date = data["date"].min().date()
    max_date = data["date"].max().date()
    selected_dates = st.date_input(
        "Период продаж",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if len(selected_dates) == 2:
        date_from, date_to = selected_dates
        data = data[(data["date"].dt.date >= date_from) & (data["date"].dt.date <= date_to)]

    if is_network_role(current_user["role"]) and "salon" in data.columns and data["salon"].nunique() > 1:
        all_salons = sorted(data["salon"].dropna().unique().tolist())
        selected_salons_filter = st.multiselect("Салоны", all_salons, default=all_salons)
        data = data[data["salon"].isin(selected_salons_filter)]

    if data["category"].nunique() > 1:
        all_categories = sorted(data["category"].dropna().unique().tolist())
        selected_categories = st.multiselect("Категории", all_categories, default=all_categories)
        data = data[data["category"].isin(selected_categories)]

    if data["manager"].nunique() > 1:
        all_managers = sorted(data["manager"].dropna().unique().tolist())
        selected_managers = st.multiselect("Менеджеры", all_managers, default=all_managers)
        data = data[data["manager"].isin(selected_managers)]

if data.empty:
    st.warning("После применения фильтров не осталось данных.")
    st.stop()

overview = build_overview_metrics(data)
product_summary = build_product_summary(data)
category_summary = build_product_summary(data, "category")
manager_summary = build_product_summary(data, "manager")
salon_summary = build_product_summary(data, "salon") if "salon" in data.columns else pd.DataFrame()
monthly_summary = build_monthly_summary(data)
abc_data = build_abc_analysis(product_summary, "revenue")
forecast_data = build_forecast(monthly_summary)
revenue_anomalies = detect_anomalies(monthly_summary)
yoy_data = build_yoy_comparison(data)

default_left_month: str | None = None
default_right_month: str | None = None
comparison = pd.DataFrame()

if len(monthly_summary) >= 2:
    default_left_month = monthly_summary.iloc[-2]["month_label"]
    default_right_month = monthly_summary.iloc[-1]["month_label"]
    comparison = build_month_comparison(data, default_left_month, default_right_month)

render_dataset_hero(source_label, data, overview, selected_categories, selected_managers, current_user)

a_revenue_share = abc_data.loc[abc_data["abc_class"] == "A", "share_pct"].sum() if not abc_data.empty else float("nan")
latest_revenue_delta = monthly_summary.iloc[-1]["revenue_change_pct"] if len(monthly_summary) >= 2 else float("nan")
latest_margin_delta = monthly_summary.iloc[-1]["margin_change_pct"] if len(monthly_summary) >= 2 else float("nan")

_rev_delta_str = f"{format_change_percent(latest_revenue_delta)} к пред. месяцу" if not is_missing(latest_revenue_delta) else ""
_mar_delta_str = f"{format_change_percent(latest_margin_delta)} к пред. месяцу" if not is_missing(latest_margin_delta) else ""
render_metric_cards(
    [
        {
            "label": "Выручка",
            "value": format_money(overview["total_revenue"]),
            "delta": _rev_delta_str,
        },
        {
            "label": "Маржа",
            "value": format_money(overview["total_margin"]),
            "delta": _mar_delta_str,
        },
        {"label": "Маржа %", "value": format_percent(overview["margin_pct"]), "delta": ""},
        {"label": "Количество", "value": format_number(overview["total_quantity"]), "delta": ""},
        {
            "label": "К прошлому месяцу",
            "value": format_percent(latest_revenue_delta) if not is_missing(latest_revenue_delta) else f"{len(monthly_summary)} мес. в данных",
            "delta": (
                f"{latest_margin_delta:+.1f}% по марже".replace(".", ",")
                if not is_missing(latest_margin_delta)
                else ("Загрузите ещё месяц для сравнения" if len(monthly_summary) < 2 else "")
            ),
        },
        {"label": "Доля класса A", "value": format_percent(a_revenue_share), "delta": ""},
    ]
)

tab_labels = ["Обзор", "ABC-анализ", "Маржинальность", "Сравнение месяцев", "Расширенный анализ", "Источники"]
if can_manage_access(current_user):
    tab_labels.append("Управление")

tabs = st.tabs(tab_labels)
tab_dashboard, tab_abc, tab_margin, tab_months, tab_advanced, tab_data = tabs[:6]
tab_access = tabs[6] if can_manage_access(current_user) else None

with tab_dashboard:
    render_section_intro(
        "Обзор бизнеса",
        "Главный рабочий экран по текущему срезу продаж. Здесь собраны самые важные зоны для руководителя: динамика, структура бизнеса, ключевые риски по марже и товары-лидеры.",
    )
    insights = build_insights(
        overview,
        monthly_summary,
        category_summary,
        manager_summary,
        product_summary,
        abc_data,
        salon_summary,
        anomalies=revenue_anomalies,
    )
    latest_month = monthly_summary.iloc[-1]
    top_products = product_summary.head(15)
    margin_risk_table = product_summary[product_summary["margin_pct"].notna()].sort_values("margin_pct", ascending=True).head(12)
    period_text = f"{data['date'].min().strftime('%d.%m.%Y')} - {data['date'].max().strftime('%d.%m.%Y')}"
    salon_count = int(data["salon"].nunique()) if "salon" in data.columns else 1
    context_value = current_user.get("salon") or ("Вся сеть" if is_network_role(current_user["role"]) else "Локальный контур")

    render_section_marker(
        "Контекст периода",
        "Что происходит в текущем срезе",
        "Сначала считайте рамку периода и масштаб выборки, потом переходите к динамике, структуре сети и товарным решениям. Такой порядок помогает не потерять контекст, прежде чем смотреть детали.",
    )
    render_workspace_band(
        [
            {"label": "Период", "value": period_text, "meta": "Фактический диапазон данных после фильтров"},
            {"label": "Контур", "value": context_value, "meta": source_label},
            {"label": "Строк продаж", "value": format_number(overview["line_count"]), "meta": "Сколько операций участвует в анализе"},
            {"label": "Ассортимент", "value": format_number(overview["product_count"]), "meta": "Уникальные товары в текущем срезе"},
            {"label": "Салоны", "value": format_number(salon_count), "meta": "Сколько точек вошло в отчёт"},
            {"label": "Последний месяц", "value": str(latest_month["month_label"]), "meta": "Текущая опорная точка для контроля"},
        ]
    )
    top_category = category_summary.iloc[0] if not category_summary.empty else None
    dashboard_risk_threshold = 15.0
    dashboard_risk_count = (
        int((product_summary["margin_pct"].fillna(9999) < dashboard_risk_threshold).sum())
        if "margin_pct" in product_summary.columns
        else 0
    )
    trend_value = (
        format_percent(latest_revenue_delta)
        if not is_missing(latest_revenue_delta)
        else f"{len(monthly_summary)} мес. в выборке"
    )
    trend_body = (
        f"Последний месяц {latest_month['month_label']} закрылся с изменением выручки к предыдущему периоду. "
        f"С этого блока удобно начинать чтение дашборда, чтобы сразу понять, ускоряется сеть или замедляется."
        if not is_missing(latest_revenue_delta)
        else "В выборке пока только один месяц. Дашборд уже показывает структуру и товары, но для оценки динамики нужен ещё минимум один период."
    )

    render_journey_cards(
        [
            {
                "title": "Сначала прочитайте контекст",
                "body": "Период, контур, объём строк и число салонов задают рамку анализа. Если рамка неверная, все последующие выводы по графикам тоже будут неточными.",
                "hint": "Проверяйте этот блок первым после смены фильтров.",
            },
            {
                "title": "Потом найдите источник результата",
                "body": "Графики по динамике, салонам, категориям и менеджерам отвечают на вопрос, где именно формируется выручка и маржа. Это зона для управленческих решений по сети.",
                "hint": "Ищите не просто лидеров, а концентрацию результата.",
            },
            {
                "title": "В конце смотрите риск и ассортимент",
                "body": "ABC-срез, движение между месяцами и маржинальные риски помогают решить, какие товары держат бизнес, какие ускоряются и какие уже съедают прибыль.",
                "hint": "Эта часть нужна для конкретных действий по товарной матрице.",
            },
        ]
    )
    render_spotlight_cards(
        [
            {
                "label": "Темп периода",
                "value": trend_value,
                "body": trend_body,
                "tone": "accent" if not is_missing(latest_revenue_delta) and latest_revenue_delta >= 0 else "warm",
            },
            {
                "label": "Ядро выручки",
                "value": format_percent(a_revenue_share) if not is_missing(a_revenue_share) else "н/д",
                "body": (
                    f"Класс A формирует основную часть оборота. Лидирующая категория сейчас: {top_category['group_name']}."
                    if top_category is not None
                    else "Когда появятся категории, здесь будет видно, насколько выручка держится на ядре ассортимента."
                ),
                "tone": "neutral",
            },
            {
                "label": "Маржинальный риск",
                "value": format_number(dashboard_risk_count),
                "body": (
                    f"Товаров ниже {int(dashboard_risk_threshold)}% по марже в текущем срезе. Этот индикатор нужен, чтобы не потерять прибыль за красивой выручкой."
                ),
                "tone": "warm" if dashboard_risk_count else "accent",
            },
        ]
    )

    left_col, right_col = st.columns([2.2, 0.95], gap="large")

    with left_col:
        with st.container(border=True):
            render_panel_header(
                "Динамика продаж",
                "Основной график периода: показывает выручку и маржу по месяцам в одном поле зрения. Это первая точка для оценки тренда и общего направления бизнеса.",
            )
            trend_chart = go.Figure()
            trend_chart.add_trace(
                go.Bar(
                    x=monthly_summary["month_label"],
                    y=monthly_summary["revenue"],
                    name="Выручка",
                    marker_color="#0F766E",
                )
            )
            trend_chart.add_trace(
                go.Scatter(
                    x=monthly_summary["month_label"],
                    y=monthly_summary["margin"],
                    name="Маржа",
                    mode="lines+markers",
                    line=dict(color="#B45309", width=3),
                )
            )
            if not forecast_data.empty:
                trend_chart.add_trace(
                    go.Bar(
                        x=forecast_data["month_label"],
                        y=forecast_data["revenue"],
                        name="Прогноз выручки",
                        marker_color="#0F766E",
                        opacity=0.4,
                    )
                )
                if not forecast_data["margin"].isna().all():
                    trend_chart.add_trace(
                        go.Scatter(
                            x=forecast_data["month_label"],
                            y=forecast_data["margin"],
                            name="Прогноз маржи",
                            mode="lines+markers",
                            line=dict(color="#B45309", width=2, dash="dot"),
                            marker=dict(symbol="diamond", size=8),
                        )
                    )
            trend_chart.update_layout(
                legend_title="Показатель",
                xaxis_title="Месяц",
                yaxis_title="Сумма",
            )
            polish_figure(trend_chart, height=540)
            st.plotly_chart(trend_chart, use_container_width=True)
            if not forecast_data.empty:
                st.caption(f"Прогноз на {len(forecast_data)} мес. рассчитан по линейному тренду исторических данных.")

    with right_col:
        render_insight_panel(insights)

    with st.container(border=True):
        render_panel_header(
            f"Итог месяца {latest_month['month_label']}",
            "Горизонтальная сводка по самому свежему периоду. Этот блок нужен для быстрого чтения финала месяца: сколько дали выручка, маржа и товарный объём без лишнего вертикального шума.",
        )
        render_snapshot_strip(
            [
                {
                    "label": "Выручка",
                    "value": format_money(latest_month["revenue"]),
                    "delta": percent_or_none(latest_revenue_delta) or "",
                    "hint": "Нет сравнения с прошлым месяцем",
                },
                {
                    "label": "Маржа",
                    "value": format_money(latest_month["margin"]),
                    "delta": percent_or_none(latest_margin_delta) or "",
                    "hint": "Нет сравнения с прошлым месяцем",
                },
                {
                    "label": "Количество",
                    "value": format_number(latest_month["quantity"]),
                    "hint": "Товарный объём за последний месяц",
                },
            ]
        )

    render_section_marker(
        "Структура бизнеса",
        "Где сосредоточен результат",
        "Эта часть отвечает на вопрос, какие салоны, категории и менеджеры реально держат выручку и маржу. Она нужна для управленческого распределения внимания, а не только для красивых графиков.",
    )

    if not salon_summary.empty and len(salon_summary) > 1:
        with st.container(border=True):
            render_panel_header(
                "Салоны сети",
                "Сравнение салонов по выручке и марже за выбранный период. Блок помогает быстро увидеть сильные точки сети и зоны, где результат проседает.",
            )
            salon_chart = go.Figure()
            salon_chart.add_trace(
                go.Bar(
                    x=salon_summary["group_name"],
                    y=salon_summary["revenue"],
                    name="Выручка",
                    marker_color="#0F766E",
                )
            )
            salon_chart.add_trace(
                go.Scatter(
                    x=salon_summary["group_name"],
                    y=salon_summary["margin"],
                    name="Маржа",
                    mode="lines+markers",
                    line=dict(color="#B45309", width=3),
                )
            )
            salon_chart.update_layout(xaxis_tickangle=-20)
            polish_figure(salon_chart, height=480)
            st.plotly_chart(salon_chart, use_container_width=True)

    second_left, second_right = st.columns(2, gap="large")

    with second_left:
        with st.container(border=True):
            render_panel_header(
                "Категории",
                "Показывает, какие товарные группы сильнее всего влияют на выручку и где маржа выглядит устойчивее. Подходит для быстрой оценки структуры ассортимента.",
            )
            category_chart_data = category_summary.head(10).sort_values("revenue", ascending=True)
            category_chart = px.bar(
                category_chart_data,
                x="revenue",
                y="group_name",
                orientation="h",
                color="margin_pct",
                color_continuous_scale=["#DBEAFE", "#60A5FA", "#0F766E"],
                labels={"group_name": "Категория", "revenue": "Выручка", "margin_pct": "Маржа %"},
            )
            category_chart.update_layout(coloraxis_showscale=False, yaxis_title="")
            polish_figure(category_chart, height=500)
            st.plotly_chart(category_chart, use_container_width=True)

    with second_right:
        with st.container(border=True):
            render_panel_header(
                "Менеджеры",
                "Сравнение результатов по команде продаж. Блок помогает быстро увидеть, кто ведет основной объём, а где нужна дополнительная управленческая поддержка.",
            )
            manager_chart_data = manager_summary.head(10).sort_values("revenue", ascending=True)
            manager_chart = px.bar(
                manager_chart_data,
                x="revenue",
                y="group_name",
                orientation="h",
                color="margin_pct",
                color_continuous_scale=["#E0F2FE", "#38BDF8", "#0F766E"],
                labels={"group_name": "Менеджер", "revenue": "Выручка", "margin_pct": "Маржа %"},
            )
            manager_chart.update_layout(coloraxis_showscale=False, yaxis_title="")
            polish_figure(manager_chart, height=500)
            st.plotly_chart(manager_chart, use_container_width=True)

    render_section_marker(
        "Товары и решения",
        "Что требует внимания сегодня",
        "Здесь начинается практическая часть дашборда: ядро ассортимента, движение товаров между периодами, слабые позиции по марже и лидеры по выручке. Это зона ежедневных управленческих действий.",
    )

    abc_summary = (
        abc_data.groupby("abc_class", as_index=False)
        .agg(items=("group_name", "count"), share_pct=("share_pct", "sum"), revenue=("abc_basis", "sum"))
        .sort_values("abc_class")
    )
    top_a = abc_data[abc_data["abc_class"] == "A"].head(7)
    movement_focus = pd.DataFrame()
    if not comparison.empty and default_left_month and default_right_month:
        movement_focus = comparison.copy()
        movement_focus["abs_revenue_delta"] = movement_focus["revenue_delta"].abs()
        movement_focus = movement_focus.nlargest(7, "abs_revenue_delta")

    balanced_left, balanced_right = st.columns(2, gap="large")

    with balanced_left:
        with st.container(border=True):
            render_panel_header(
                "ABC-срез",
                "Равная по весу панель для ядра ассортимента. Показывает, какую часть результата удерживает класс A и какие позиции сейчас являются опорой продаж.",
            )
            abc_pie = px.pie(
                abc_summary,
                names="abc_class",
                values="revenue",
                color="abc_class",
                color_discrete_map={"A": "#0F766E", "B": "#D97706", "C": "#64748B"},
            )
            polish_figure(abc_pie, height=360)
            st.plotly_chart(abc_pie, use_container_width=True)
            st.caption("Ключевые позиции класса A в текущем срезе.")
            st.dataframe(
                to_display_table(
                    top_a,
                    {
                        "group_name": "Товар",
                        "abc_basis": "База ABC",
                        "share_pct": "Доля, %",
                        "cum_share_pct": "Накопительная доля, %",
                    },
                ),
                use_container_width=True,
                hide_index=True,
                height=260,
            )

    with balanced_right:
        with st.container(border=True):
            render_panel_header(
                "Движение товаров",
                "Парная панель к ABC-срезу. Показывает, какие позиции выросли и просели между двумя последними месяцами, чтобы быстро разобрать причину изменения результата.",
            )
            if comparison.empty or not default_left_month or not default_right_month:
                st.info("Для этого блока нужны минимум два месяца в данных.")
            else:
                movement_chart = build_movement_chart(comparison, default_left_month, default_right_month)
                polish_figure(movement_chart, height=360)
                st.plotly_chart(movement_chart, use_container_width=True)
                st.caption(f"Товары с самым заметным движением: {default_right_month} к {default_left_month}.")
                st.dataframe(
                    to_display_table(
                        movement_focus,
                        {
                            "group_name": "Товар",
                            f"revenue_{default_left_month}": f"Выручка {default_left_month}",
                            f"revenue_{default_right_month}": f"Выручка {default_right_month}",
                            "revenue_delta": "Изменение выручки",
                            "revenue_delta_pct": "Изменение, %",
                        },
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=260,
                )

    risk_left, risk_right = st.columns(2, gap="large")

    with risk_left:
        with st.container(border=True):
            render_panel_header(
                "Зоны риска по марже",
                "Крупный визуальный блок для слабых позиций. По нему легче всего увидеть товары, где маржа уже стала управленческой проблемой.",
            )
            if margin_risk_table.empty:
                st.info("Недостаточно данных по марже для построения зоны риска.")
            else:
                margin_risk_chart = px.bar(
                    margin_risk_table.sort_values("margin_pct", ascending=True),
                    x="margin_pct",
                    y="group_name",
                    orientation="h",
                    color="margin_pct",
                    color_continuous_scale=["#991B1B", "#DC2626", "#F59E0B"],
                    labels={"group_name": "Товар", "margin_pct": "Маржа, %"},
                    title="",
                )
                margin_risk_chart.update_layout(coloraxis_showscale=False, yaxis_title="")
                polish_figure(margin_risk_chart, height=540)
                st.plotly_chart(margin_risk_chart, use_container_width=True)

    with risk_right:
        with st.container(border=True):
            render_panel_header(
                "Таблица риска",
                "Точная расшифровка проблемных товаров по выручке, марже и количеству. Подходит для быстрой постановки задач по цене, скидке или закупке.",
            )
            if margin_risk_table.empty:
                st.info("Недостаточно данных по марже для построения таблицы риска.")
            else:
                st.dataframe(
                    to_display_table(
                        margin_risk_table,
                        {
                            "group_name": "Товар",
                            "revenue": "Выручка",
                            "margin": "Маржа",
                            "quantity": "Количество",
                            "margin_pct": "Маржа, %",
                        },
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=540,
                )

    with st.container(border=True):
        render_panel_header(
            "Топ товаров",
            "Главная таблица лидеров по выручке с количеством и маржой. Это удобный список для ежедневного контроля и быстрой расшифровки сильных позиций.",
        )
        st.dataframe(
            to_display_table(
                top_products,
                {
                    "group_name": "Товар",
                    "revenue": "Выручка",
                    "margin": "Маржа",
                    "quantity": "Количество",
                    "margin_pct": "Маржа, %",
                },
            ),
            use_container_width=True,
            hide_index=True,
            height=560,
        )

with tab_abc:
    render_section_intro(
        "ABC-анализ",
        "Показывает, какие товары формируют основную долю выручки, маржи или объема продаж. Эта вкладка помогает быстро отделить ключевой ассортимент от позиций, которые дают мало результата.",
    )
    abc_metric_options = {
        "revenue": "Выручка",
        "margin": "Маржа",
        "quantity": "Количество",
    }
    abc_metric = st.selectbox(
        "Метрика для ABC-анализа",
        options=list(abc_metric_options.keys()),
        format_func=abc_metric_options.get,
        index=0,
    )

    abc_tab_data = build_abc_analysis(product_summary, abc_metric)
    abc_classes = (
        abc_tab_data.groupby("abc_class", as_index=False)
        .agg(items=("group_name", "count"), metric_total=("abc_basis", "sum"))
        .sort_values("abc_class")
    )
    abc_share_map = abc_tab_data.groupby("abc_class")["share_pct"].sum().to_dict()
    abc_count_map = abc_tab_data.groupby("abc_class")["group_name"].count().to_dict()

    render_section_marker(
        "Картина ассортимента",
        "Как распределён результат по классам",
        "Сначала смотрите, какую часть метрики удерживают классы A, B и C, затем переходите к лидерам, структуре и кривой Парето. Такой порядок помогает сначала увидеть концентрацию результата, а потом уже разбирать конкретные товары.",
    )
    render_snapshot_strip(
        [
            {
                "label": "Класс A",
                "value": format_percent(abc_share_map.get("A", 0)),
                "hint": f"{format_number(abc_count_map.get('A', 0))} товаров в ядре результата",
            },
            {
                "label": "Класс B",
                "value": format_percent(abc_share_map.get("B", 0)),
                "hint": f"{format_number(abc_count_map.get('B', 0))} товаров в опорной зоне",
            },
            {
                "label": "Класс C",
                "value": format_percent(abc_share_map.get("C", 0)),
                "hint": f"{format_number(abc_count_map.get('C', 0))} товаров в длинном хвосте",
            },
        ]
    )

    left_col, right_col = st.columns(2, gap="large")

    with left_col:
        with st.container(border=True):
            render_panel_header(
                "Лидеры по выбранной метрике",
                "Показывает товары, которые дают наибольший вклад в выбранную метрику. Здесь удобно искать основной ассортимент, который держит результат.",
            )
            fig_abc = px.bar(
                abc_tab_data.head(15).sort_values("abc_basis", ascending=True),
                x="abc_basis",
                y="group_name",
                orientation="h",
                color="abc_class",
                color_discrete_map={"A": "#0F766E", "B": "#D97706", "C": "#64748B"},
                title="",
                labels={"group_name": "Товар", "abc_basis": abc_metric_options[abc_metric]},
            )
            fig_abc.update_layout(yaxis_title="", coloraxis_showscale=False)
            polish_figure(fig_abc, height=520)
            st.plotly_chart(fig_abc, use_container_width=True)

    with right_col:
        with st.container(border=True):
            render_panel_header(
                "Структура ABC по товарам",
                "Показывает, как весь ассортимент распределяется по классам A, B и C. Подходит для быстрой оценки ширины ассортимента и доли ключевых позиций.",
            )
            fig_treemap = px.treemap(
                abc_tab_data,
                path=["abc_class", "group_name"],
                values="abc_basis",
                color="abc_class",
                color_discrete_map={"A": "#0F766E", "B": "#D97706", "C": "#64748B"},
                title="",
            )
            polish_figure(fig_treemap, height=520)
            st.plotly_chart(fig_treemap, use_container_width=True)

    with st.container(border=True):
        render_panel_header(
            "Кривая Парето",
            "Накопленная доля выбранной метрики по мере добавления товаров. Линия 80% помогает быстро понять, где заканчивается ядро ассортимента и начинается менее значимая часть матрицы.",
        )
        pareto_fig = go.Figure()
        pareto_fig.add_trace(
            go.Bar(
                x=abc_tab_data["group_name"],
                y=abc_tab_data["share_pct"],
                name=abc_metric_options[abc_metric],
                marker_color=abc_tab_data["abc_class"].map({"A": "#0F766E", "B": "#D97706", "C": "#64748B"}),
                showlegend=False,
            )
        )
        pareto_fig.add_trace(
            go.Scatter(
                x=abc_tab_data["group_name"],
                y=abc_tab_data["cum_share_pct"],
                name="Накопленная доля, %",
                mode="lines",
                line=dict(color="#B45309", width=2),
                yaxis="y2",
            )
        )
        pareto_fig.add_hline(y=80, line_dash="dash", line_color="#991B1B", annotation_text="80%", yref="y2")
        pareto_fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", range=[0, 105], title="Накопленная доля, %"),
            yaxis=dict(title=abc_metric_options[abc_metric]),
            xaxis=dict(showticklabels=False),
        )
        polish_figure(pareto_fig, height=380)
        st.plotly_chart(pareto_fig, use_container_width=True)

    with st.container(border=True):
        render_panel_header(
            "Таблица ABC по товарам",
            "Подробная расшифровка по каждой позиции: класс, доля, накопительный вклад и ключевые показатели. Используйте таблицу для точечной работы с ассортиментом.",
        )
        st.dataframe(
            format_display_frame(
                abc_tab_data,
                {
                    "group_name": "Товар",
                    "abc_basis": abc_metric_options[abc_metric],
                    "share_pct": "Доля, %",
                    "cum_share_pct": "Накопительная доля, %",
                    "abc_class": "Класс ABC",
                    "revenue": "Выручка",
                    "margin": "Маржа",
                    "quantity": "Количество",
                    "sales_lines": "Строк продаж",
                    "margin_pct": "Маржа, %",
                },
            ),
            use_container_width=True,
            hide_index=True,
            height=520,
        )
        st.download_button(
            "Скачать ABC-анализ",
            data=to_csv_bytes(abc_tab_data),
            file_name=f"abc_analysis_{abc_metric}.csv",
            mime="text/csv",
        )

with tab_margin:
    render_section_intro(
        "Маржинальность",
        "Показывает, какие товары приносят больше валовой прибыли, а какие создают риск по марже. Вкладка нужна, чтобы отделить прибыльный ассортимент от позиций с низкой отдачей.",
    )
    if data["margin"].isna().all():
        st.info("Для маржинальности не хватает колонок `Себестоимость` или `Маржа` в исходном файле.")
    else:
        margin_sorted = product_summary.sort_values("margin", ascending=False).copy()
        low_margin = product_summary.sort_values("margin_pct", ascending=True).head(15).copy()
        margin_pct_series = product_summary["margin_pct"].dropna()
        risk_threshold = 20.0
        risk_count = int((product_summary["margin_pct"].fillna(9999) < risk_threshold).sum())
        top_margin_product = margin_sorted.iloc[0] if not margin_sorted.empty else None

        render_metric_cards(
            [
                {
                    "label": "Общая маржа",
                    "value": format_money(overview["total_margin"]),
                    "delta": format_percent(overview["margin_pct"]),
                },
                {
                    "label": "Средняя маржа по товарам",
                    "value": format_percent(margin_pct_series.mean()) if not margin_pct_series.empty else "н/д",
                    "delta": "Средний процент по текущему ассортименту",
                },
                {
                    "label": "Позиции в зоне риска",
                    "value": format_number(risk_count),
                    "delta": f"Ниже {int(risk_threshold)}% по марже",
                },
                {
                    "label": "Лидер по валовой марже",
                    "value": str(top_margin_product['group_name']) if top_margin_product is not None else "н/д",
                    "delta": format_money(top_margin_product["margin"]) if top_margin_product is not None else "",
                },
            ]
        )
        render_section_marker(
            "Прибыль и риск",
            "Что создаёт маржу, а что её съедает",
            "Сначала смотрите лидеров и зону риска, затем переходите к карте распределения и детальной таблице. Такой порядок помогает не потеряться в цифрах и сразу выделить товары для действия.",
        )

        leaders_left, leaders_right = st.columns(2, gap="large")

        with leaders_left:
            with st.container(border=True):
                render_panel_header(
                    "Лидеры по валовой марже",
                    "Крупный список товаров, которые приносят наибольшую сумму маржи. Этот блок помогает понять, на каком ассортименте держится прибыль.",
                )
                fig_margin = px.bar(
                    margin_sorted.head(20).sort_values("margin", ascending=True),
                    x="margin",
                    y="group_name",
                    orientation="h",
                    color="margin_pct",
                    color_continuous_scale=["#FDE68A", "#D97706", "#92400E"],
                    title="",
                    labels={"group_name": "Товар", "margin": "Маржа", "margin_pct": "Маржа %"},
                )
                fig_margin.update_layout(coloraxis_showscale=False, yaxis_title="")
                polish_figure(fig_margin, height=520)
                st.plotly_chart(fig_margin, use_container_width=True)

        with leaders_right:
            with st.container(border=True):
                render_panel_header(
                    "Зоны риска по марже",
                    "Показывает товары с самой низкой маржой в процентах. Горизонтальный формат делает длинные названия читаемыми и позволяет быстрее искать слабые позиции.",
                )
                fig_low = px.bar(
                    low_margin.sort_values("margin_pct", ascending=True),
                    x="margin_pct",
                    y="group_name",
                    orientation="h",
                    color="margin_pct",
                    color_continuous_scale=["#991B1B", "#DC2626", "#F59E0B"],
                    title="",
                    labels={"group_name": "Товар", "margin_pct": "Маржа %"},
                )
                fig_low.update_layout(coloraxis_showscale=False, yaxis_title="")
                polish_figure(fig_low, height=520)
                st.plotly_chart(fig_low, use_container_width=True)

        with st.container(border=True):
            render_panel_header(
                "Выручка vs Маржинальность",
                "Каждая точка — отдельный товар. Правый верхний угол показывает сильные позиции, а левый нижний помогает быстро заметить слабые товары с маленькой выручкой и низкой маржой. Размер пузыря отражает объём продаж без знака, поэтому возвраты не ломают график.",
            )
            scatter_data = product_summary[product_summary["margin_pct"].notna()].copy()
            if not scatter_data.empty:
                scatter_data["quantity_bubble"] = build_safe_marker_size(scatter_data["quantity"], absolute=True)
                scatter_fig = px.scatter(
                    scatter_data,
                    x="revenue",
                    y="margin_pct",
                    size="quantity_bubble",
                    color="margin_pct",
                    color_continuous_scale=["#991B1B", "#F59E0B", "#0F766E"],
                    hover_name="group_name",
                    labels={"revenue": "Выручка", "margin_pct": "Маржа, %", "quantity_bubble": "Объём продаж"},
                    title="",
                    size_max=60,
                )
                scatter_fig.add_hline(y=20, line_dash="dash", line_color="#991B1B", annotation_text="Порог 20%")
                polish_figure(scatter_fig, height=560)
                st.plotly_chart(scatter_fig, use_container_width=True)
            else:
                st.info("Недостаточно данных для построения скаттер-плота (нужны себестоимость или маржа).")

        with st.container(border=True):
            render_panel_header(
                "Таблица по маржинальности товаров",
                "Подробная расшифровка по выручке, себестоимости, марже и количеству. Таблица подходит для фильтрации, проверки аномалий и выгрузки в Excel.",
            )
            st.dataframe(
                format_display_frame(
                    margin_sorted,
                    {
                        "group_name": "Товар",
                        "revenue": "Выручка",
                        "cost": "Себестоимость",
                        "margin": "Маржа",
                        "quantity": "Количество",
                        "sales_lines": "Строк продаж",
                        "margin_pct": "Маржа, %",
                    },
                ),
                use_container_width=True,
                hide_index=True,
                height=640,
            )
            st.download_button(
                "Скачать маржинальность по товарам",
                data=to_csv_bytes(margin_sorted),
                file_name="margin_by_product.csv",
                mime="text/csv",
            )

with tab_months:
    render_section_intro(
        "Сравнение месяцев",
        "Показывает, как меняются продажи, маржа и количество между двумя периодами. Вкладка помогает увидеть рост, просадку и товары, которые сильнее всего повлияли на результат.",
    )
    render_section_marker(
        "Сценарий сравнения",
        "Сначала выберите два периода, затем ищите причину изменения",
        "Эта вкладка читается в три шага: сначала смотрите общий результат двух месяцев, потом разбирайте движение товаров и вклад в выручку, а таблицу оставляйте для точечной проверки.",
    )
    available_months = monthly_summary["month_label"].tolist()

    if len(available_months) < 2:
        st.info("Для сравнения нужно минимум два месяца в данных.")
    else:
        default_left_index = max(len(available_months) - 2, 0)
        default_right_index = len(available_months) - 1

        left_col, right_col = st.columns(2, gap="large")
        with left_col:
            selected_left_month = st.selectbox("Базовый месяц", options=available_months, index=default_left_index)
        with right_col:
            selected_right_month = st.selectbox(
                "Месяц сравнения",
                options=available_months,
                index=default_right_index,
            )

        month_comparison = build_month_comparison(data, selected_left_month, selected_right_month)

        if month_comparison.empty:
            st.warning("Не получилось собрать сравнение для выбранных месяцев.")
        else:
            selected_left_row = monthly_summary.loc[monthly_summary["month_label"] == selected_left_month].iloc[-1]
            selected_right_row = monthly_summary.loc[monthly_summary["month_label"] == selected_right_month].iloc[-1]
            revenue_change_selected = calculate_change_pct(selected_right_row["revenue"], selected_left_row["revenue"])
            margin_change_selected = calculate_change_pct(selected_right_row["margin"], selected_left_row["margin"])
            quantity_change_selected = calculate_change_pct(selected_right_row["quantity"], selected_left_row["quantity"])

            render_snapshot_strip(
                [
                    {
                        "label": f"Выручка {selected_left_month}",
                        "value": format_money(selected_left_row["revenue"]),
                        "hint": "Базовый месяц для сравнения",
                    },
                    {
                        "label": f"Выручка {selected_right_month}",
                        "value": format_money(selected_right_row["revenue"]),
                        "delta": format_change_percent(revenue_change_selected),
                        "hint": "Изменение к базовому месяцу",
                    },
                    {
                        "label": f"Маржа {selected_right_month}",
                        "value": format_money(selected_right_row["margin"]),
                        "delta": format_change_percent(margin_change_selected),
                        "hint": "Изменение к базовому месяцу",
                    },
                    {
                        "label": f"Количество {selected_right_month}",
                        "value": format_number(selected_right_row["quantity"]),
                        "delta": format_change_percent(quantity_change_selected),
                        "hint": "Изменение к базовому месяцу",
                    },
                ]
            )

            chart_left, chart_right = st.columns(2, gap="large")

            with chart_left:
                with st.container(border=True):
                    render_panel_header(
                        "Движение товаров между месяцами",
                        "Показывает, какие позиции выросли, просели или исчезли между двумя выбранными месяцами. Блок помогает быстро увидеть структуру изменений.",
                    )
                    movement_chart = build_movement_chart(month_comparison, selected_left_month, selected_right_month)
                    st.plotly_chart(movement_chart, use_container_width=True)

            with chart_right:
                with st.container(border=True):
                    render_panel_header(
                        "Вклад товаров в изменение выручки",
                        "Показывает, за счет каких товаров общая выручка выросла или снизилась. Хорошо подходит для разбора причин изменения месяца.",
                    )
                    top_movers = month_comparison.nlargest(5, "revenue_delta")
                    bottom_movers = month_comparison.nsmallest(5, "revenue_delta")
                    left_total = month_comparison[f"revenue_{selected_left_month}"].sum()
                    right_total = month_comparison[f"revenue_{selected_right_month}"].sum()

                    wf_x = (
                        [selected_left_month]
                        + top_movers["group_name"].tolist()
                        + bottom_movers["group_name"].tolist()
                        + [selected_right_month]
                    )
                    wf_y = (
                        [left_total]
                        + top_movers["revenue_delta"].tolist()
                        + bottom_movers["revenue_delta"].tolist()
                        + [right_total]
                    )
                    wf_measure = (
                        ["absolute"]
                        + ["relative"] * len(top_movers)
                        + ["relative"] * len(bottom_movers)
                        + ["total"]
                    )
                    wf_fig = go.Figure(
                        go.Waterfall(
                            x=wf_x,
                            y=wf_y,
                            measure=wf_measure,
                            connector=dict(line=dict(color="#0F766E", width=1)),
                            increasing=dict(marker_color="#0F766E"),
                            decreasing=dict(marker_color="#991B1B"),
                            totals=dict(marker_color="#D97706"),
                            name="Изменение выручки",
                        )
                    )
                    wf_fig.update_layout(title="")
                    polish_figure(wf_fig, height=460)
                    st.plotly_chart(wf_fig, use_container_width=True)

            rename_map = {
                "group_name": "Товар",
                f"revenue_{selected_left_month}": f"Выручка {selected_left_month}",
                f"revenue_{selected_right_month}": f"Выручка {selected_right_month}",
                "revenue_delta": "Изменение выручки",
                "revenue_delta_pct": "Изменение выручки %",
                f"margin_{selected_left_month}": f"Маржа {selected_left_month}",
                f"margin_{selected_right_month}": f"Маржа {selected_right_month}",
                "margin_delta": "Изменение маржи",
                "margin_delta_pct": "Изменение маржи %",
                f"quantity_{selected_left_month}": f"Количество {selected_left_month}",
                f"quantity_{selected_right_month}": f"Количество {selected_right_month}",
                "quantity_delta": "Изменение количества",
                "quantity_delta_pct": "Изменение количества %",
            }

            with st.container(border=True):
                render_panel_header(
                    "Таблица сравнения месяцев",
                    "Подробная расшифровка по товарам: значения за оба месяца, абсолютное изменение и изменение в процентах. Удобно для детальной проверки и выгрузки.",
                )
                st.dataframe(
                    format_display_frame(month_comparison, rename_map),
                    use_container_width=True,
                    hide_index=True,
                    height=520,
                )
                st.download_button(
                    "Скачать сравнение месяцев",
                    data=to_csv_bytes(month_comparison.rename(columns=rename_map)),
                    file_name=f"month_comparison_{selected_left_month}_vs_{selected_right_month}.csv",
                    mime="text/csv",
                )

    if not yoy_data.empty and yoy_data["year"].nunique() >= 2:
        st.divider()
        with st.container(border=True):
            render_panel_header(
                "Год к году (YoY)",
                "Сравнение одинаковых месяцев разных лет. Этот блок помогает отделить сезонность от реального роста или просадки бизнеса.",
            )
            yoy_metric = st.radio(
                "Метрика YoY",
                options=["revenue", "margin", "quantity"],
                format_func={"revenue": "Выручка", "margin": "Маржа", "quantity": "Количество"}.get,
                horizontal=True,
                key="yoy_metric_radio",
            )
            yoy_fig = px.bar(
                yoy_data,
                x="month_num",
                y=yoy_metric,
                color="year",
                barmode="group",
                color_discrete_sequence=["#0F766E", "#B45309", "#64748B", "#991B1B"],
                labels={"month_num": "Месяц", yoy_metric: {"revenue": "Выручка", "margin": "Маржа", "quantity": "Количество"}[yoy_metric], "year": "Год"},
                title="",
            )
            yoy_fig.update_xaxes(tickmode="array", tickvals=list(range(1, 13)), ticktext=["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"])
            polish_figure(yoy_fig, height=420)
            st.plotly_chart(yoy_fig, use_container_width=True)

with tab_advanced:
    render_section_intro(
        "Расширенный анализ",
        "Дополнительные блоки для более глубокого разбора продаж: сегментация по активности, поиск ритма продаж по дням и простой прогноз по будущим месяцам.",
    )
    adv_tabs = st.tabs(["RFM-анализ", "Тепловая карта", "Прогноз"])

    with adv_tabs[0]:
        render_panel_header(
            "RFM-анализ",
            "Показывает, кто продает чаще, кто давно не активен и кто приносит наибольшую выручку. Подходит для оценки менеджеров или категорий по качеству работы.",
        )
        rfm_group = st.radio(
            "Группировать по",
            options=["manager", "category"],
            format_func={"manager": "Менеджерам", "category": "Категориям"}.get,
            horizontal=True,
            key="rfm_group_radio",
        )
        rfm_data = build_rfm_summary(data, group_column=rfm_group)

        if rfm_data.empty:
            st.info("Для RFM-анализа нужны данные по менеджерам или категориям.")
        else:
            rfm_data["monetary_bubble"] = build_safe_marker_size(rfm_data["monetary"], absolute=False)
            rfm_left, rfm_right = st.columns(2, gap="large")

            with rfm_left:
                with st.container(border=True):
                    render_panel_header(
                        "Карта RFM-сегментов",
                        "Каждая точка показывает менеджера или категорию. Чем крупнее точка, тем выше вклад в выручку; чем левее и выше, тем лучше активность.",
                    )
                    rfm_scatter = px.scatter(
                        rfm_data,
                        x="recency",
                        y="frequency",
                        size="monetary_bubble",
                        color="segment",
                        hover_name="group_name",
                        color_discrete_map={
                            "Лидер": "#0F766E",
                            "Активный": "#D97706",
                            "Стабильный": "#64748B",
                            "Слабый": "#991B1B",
                        },
                        labels={
                            "recency": "Давность (дней)",
                            "frequency": "Активность (месяцев)",
                            "monetary_bubble": "Выручка",
                            "segment": "Сегмент",
                        },
                        title="",
                        size_max=60,
                    )
                    rfm_scatter.update_xaxes(autorange="reversed")
                    polish_figure(rfm_scatter, height=460)
                    st.plotly_chart(rfm_scatter, use_container_width=True)

            with rfm_right:
                with st.container(border=True):
                    render_panel_header(
                        "Распределение по сегментам",
                        "Показывает, какая часть менеджеров или категорий попадает в сильные и слабые группы. Это быстрый обзор качества продажного контура.",
                    )
                    seg_counts = rfm_data["segment"].value_counts().reset_index()
                    seg_counts.columns = ["segment", "count"]
                    seg_pie = px.pie(
                        seg_counts,
                        names="segment",
                        values="count",
                        color="segment",
                        color_discrete_map={
                            "Лидер": "#0F766E",
                            "Активный": "#D97706",
                            "Стабильный": "#64748B",
                            "Слабый": "#991B1B",
                        },
                        title="",
                    )
                    polish_figure(seg_pie, height=340)
                    st.plotly_chart(seg_pie, use_container_width=True)

            rfm_display = rfm_data[["group_name", "recency", "frequency", "monetary", "rfm_score", "segment"]].copy()
            rfm_display.columns = ["Позиция", "Давность (дней)", "Активность (мес.)", "Выручка", "RFM-балл", "Сегмент"]
            with st.container(border=True):
                render_panel_header(
                    "Таблица RFM",
                    "Подробный список сегментов с давностью, активностью, выручкой и итоговым баллом. Подходит для точечной проверки слабых и сильных зон.",
                )
                st.dataframe(rfm_display, use_container_width=True, hide_index=True, height=420)

    with adv_tabs[1]:
        heatmap_df = build_heatmap_data(data)
        if heatmap_df.empty:
            st.info("Нет данных для тепловой карты.")
        else:
            dow_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            all_weeks = sorted(heatmap_df["week_label"].unique())
            all_dows = list(range(7))

            pivot = heatmap_df.pivot(index="dow", columns="week_label", values="revenue").reindex(
                index=all_dows, columns=all_weeks
            ).fillna(0)

            with st.container(border=True):
                render_panel_header(
                    "Тепловая карта продаж",
                    "Показывает, в какие дни недели и недели периода продажи были сильнее или слабее. Блок помогает увидеть повторяющийся ритм продаж.",
                )
                heatmap_fig = go.Figure(
                    go.Heatmap(
                        z=pivot.values,
                        x=all_weeks,
                        y=dow_labels,
                        colorscale=[[0, "#F0FDF4"], [0.5, "#0F766E"], [1, "#022C22"]],
                        hoverongaps=False,
                        hovertemplate="Неделя: %{x}<br>День: %{y}<br>Выручка: %{z:,.0f}<extra></extra>",
                    )
                )
                heatmap_fig.update_layout(
                    title="",
                    xaxis=dict(showticklabels=len(all_weeks) <= 52),
                    yaxis=dict(title=""),
                )
                polish_figure(heatmap_fig, height=320)
                st.plotly_chart(heatmap_fig, use_container_width=True)
                st.caption("Чем темнее ячейка, тем выше выручка в этот день и неделю.")

    with adv_tabs[2]:
        if forecast_data.empty:
            st.info("Для прогноза нужно минимум 3 месяца данных.")
        else:
            combined = pd.concat(
                [
                    monthly_summary[["month_label", "revenue", "margin"]].assign(is_forecast=False),
                    forecast_data[["month_label", "revenue", "margin"]].assign(is_forecast=True),
                ],
                ignore_index=True,
            )
            with st.container(border=True):
                render_panel_header(
                    "Прогноз продаж",
                    "Показывает простую оценку будущих месяцев на основе текущего тренда. Используйте блок как ориентир, а не как точный финансовый план.",
                )
                forecast_fig = go.Figure()
                hist = combined[~combined["is_forecast"]]
                fut = combined[combined["is_forecast"]]

                forecast_fig.add_trace(
                    go.Scatter(
                        x=hist["month_label"],
                        y=hist["revenue"],
                        name="Факт: выручка",
                        mode="lines+markers",
                        line=dict(color="#0F766E", width=3),
                    )
                )
                forecast_fig.add_trace(
                    go.Scatter(
                        x=hist["month_label"],
                        y=hist["margin"],
                        name="Факт: маржа",
                        mode="lines+markers",
                        line=dict(color="#B45309", width=2),
                    )
                )
                forecast_fig.add_trace(
                    go.Scatter(
                        x=[hist["month_label"].iloc[-1]] + fut["month_label"].tolist(),
                        y=[hist["revenue"].iloc[-1]] + fut["revenue"].tolist(),
                        name="Прогноз: выручка",
                        mode="lines+markers",
                        line=dict(color="#0F766E", width=2, dash="dot"),
                        marker=dict(symbol="diamond", size=10),
                    )
                )
                if not fut["margin"].isna().all():
                    forecast_fig.add_trace(
                        go.Scatter(
                            x=[hist["month_label"].iloc[-1]] + fut["month_label"].tolist(),
                            y=[hist["margin"].iloc[-1]] + fut["margin"].tolist(),
                            name="Прогноз: маржа",
                            mode="lines+markers",
                            line=dict(color="#B45309", width=2, dash="dot"),
                            marker=dict(symbol="diamond", size=10),
                        )
                    )
                forecast_fig.update_layout(xaxis_title="Месяц", yaxis_title="Сумма", legend_title="")
                polish_figure(forecast_fig, height=480)
                st.plotly_chart(forecast_fig, use_container_width=True)

                st.info(
                    f"Прогноз построен на основе линейного тренда по {len(monthly_summary)} месяцам. "
                    "Не учитывает сезонность и внешние факторы — используйте как ориентир."
                )
            forecast_table = fut[["month_label", "revenue", "margin"]].copy()
            forecast_table.columns = ["Месяц (прогноз)", "Выручка", "Маржа"]
            forecast_table["Выручка"] = forecast_table["Выручка"].apply(format_money)
            forecast_table["Маржа"] = forecast_table["Маржа"].apply(lambda v: format_money(v) if not pd.isna(v) else "н/д")
            with st.container(border=True):
                render_panel_header(
                    "Таблица прогноза",
                    "Помесячная расшифровка прогнозных значений. Удобно использовать для обсуждения плана и сверки с фактическими данными позже.",
                )
                st.dataframe(forecast_table, use_container_width=True, hide_index=True)

with tab_data:
    render_section_intro(
        "Источники и выгрузки",
        "Здесь собраны исходные данные, служебная информация по сопоставлению колонок, журнал загрузок и сводка по месяцам. Эта вкладка нужна для проверки качества загрузки и состава данных.",
    )
    data_period_text = f"{data['date'].min().strftime('%d.%m.%Y')} - {data['date'].max().strftime('%d.%m.%Y')}"
    archive_upload_count = len(manifest_view) if manifest_view is not None else 0
    data_salon_count = int(data["salon"].nunique()) if "salon" in data.columns else 1
    mapping_count = sum(1 for value in selected_mapping.values() if value) if selected_mapping else 0

    render_section_marker(
        "Паспорт данных",
        "Как читать эту вкладку",
        "Сначала проверьте паспорт набора данных, затем исходный файл и сопоставление колонок. После этого журнал загрузок покажет, что уже сохранено в архиве, а сводка по месяцам подтвердит, что периоды сложились корректно.",
    )
    render_workspace_band(
        [
            {"label": "Источник", "value": source_label, "meta": "Откуда сейчас построен анализ"},
            {"label": "Период данных", "value": data_period_text, "meta": "Фактический диапазон после фильтров"},
            {"label": "Строк в анализе", "value": format_number(overview["line_count"]), "meta": "Сколько операций прошло в текущую выборку"},
            {"label": "Салоны в наборе", "value": format_number(data_salon_count), "meta": "Сколько торговых точек участвует сейчас"},
            {"label": "Месяцев в выборке", "value": format_number(len(monthly_summary)), "meta": "Сколько периодов попало в итоговую аналитику"},
            {"label": "Архивных загрузок", "value": format_number(archive_upload_count), "meta": "Сколько файлов уже сохранено в архиве"},
        ]
    )
    render_journey_cards(
        [
            {
                "title": "Проверьте, что загружено",
                "body": "Блок `Источник данных` показывает файл до аналитики. Здесь легче всего заметить съехавшую шапку отчёта, лишние строки или неверно прочитанные столбцы.",
                "hint": "Если тут всё выглядит верно, дальше можно доверять расчётам.",
            },
            {
                "title": "Сверьте поля аналитики",
                "body": "В служебной информации видно, какие колонки файла стали датой, товаром, выручкой, себестоимостью и маржой. Это главный контроль качества распознавания 1С.",
                "hint": "Особенно важны дата, товар и денежные поля.",
            },
            {
                "title": "Подтвердите архив и периоды",
                "body": "Журнал загрузок показывает, что уже сохранено в системе, а сводка по месяцам подтверждает, что временной ряд сложился корректно и без дыр в данных.",
                "hint": "Эта проверка особенно полезна перед управленческими выводами.",
            },
        ]
    )
    render_snapshot_strip(
        [
            {
                "label": "Распознано полей",
                "value": format_number(mapping_count),
                "hint": "Сколько колонок уже привязано к аналитической модели",
            },
            {
                "label": "Архив выгрузок",
                "value": format_number(archive_upload_count),
                "hint": "Чем больше корректных файлов, тем сильнее анализ истории",
            },
            {
                "label": "Глубина периода",
                "value": format_number(len(monthly_summary)),
                "hint": "Количество месяцев в текущем наборе данных",
            },
        ]
    )
    top_left, top_right = st.columns(2, gap="large")

    with top_left:
        with st.container(border=True):
            render_panel_header(
                "Источник данных",
                "Показывает первые строки исходной выгрузки в том виде, в котором файл был прочитан приложением. Смотрите сюда, если нужно проверить шапку отчёта, типы колонок и то, не съехала ли структура файла ещё до аналитики.",
            )
            if raw_data is not None:
                st.dataframe(
                    format_display_frame(raw_data.head(25)),
                    use_container_width=True,
                    hide_index=True,
                    height=420,
                )
            else:
                st.info("Сейчас открыт архивный режим. Исходный файл не выбран, анализ идет по сохраненным загрузкам.")
                if not manifest_view.empty:
                    st.dataframe(
                        format_display_frame(
                            manifest_view,
                            columns=["salon", "report_date", "source_filename", "uploaded_at"],
                        ),
                        use_container_width=True,
                        hide_index=True,
                        height=320,
                    )

    with top_right:
        with st.container(border=True):
            render_panel_header(
                "Как файл распознан",
                "Показывает, как именно колонки исходного файла были привязаны к полям аналитики. Этот блок полезен, когда нужно понять, откуда взялись выручка, дата, товар, маржа и почему итоговые таблицы выглядят именно так.",
            )
            if selected_mapping:
                st.caption(f"Распознано и привязано полей: {mapping_count}. Если что-то не совпало, ищите причину сначала здесь.")
                st.dataframe(build_download_frame(selected_mapping), use_container_width=True, hide_index=True, height=420)
            else:
                st.info("Для архивного режима сопоставление колонок хранится у каждой загруженной выгрузки отдельно.")
            st.download_button(
                "Скачать текущую выборку",
                data=to_csv_bytes(data),
                file_name="filtered_sales_data.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if not manifest_view.empty:
        with st.container(border=True):
            render_panel_header(
                "Журнал загрузок",
                "История всех сохранённых файлов по салонам и датам. Журнал нужен для контроля архива: какой файл уже лежит в системе, когда он был добавлен и какой салон он пополняет.",
            )
            manifest_table = manifest_view.copy()
            visible_columns = [column for column in ["salon", "report_date", "source_filename", "uploaded_at"] if column in manifest_table.columns]
            if visible_columns:
                st.dataframe(
                    format_display_frame(manifest_table, columns=visible_columns),
                    use_container_width=True,
                    hide_index=True,
                    height=320,
                )

    with st.container(border=True):
        render_panel_header(
            "Сводка по месяцам",
            "Главная проверочная таблица по периодам: выручка, себестоимость, маржа, количество и динамика. Если хотите понять, корректно ли сложились месяцы и откуда берётся тренд, сначала смотрите именно сюда.",
        )
        st.dataframe(
            format_display_frame(
                monthly_summary,
                {
                    "month": "Месяц",
                    "month_label": "Код месяца",
                    "revenue": "Выручка",
                    "cost": "Себестоимость",
                    "margin": "Маржа",
                    "quantity": "Количество",
                    "product_count": "Товаров",
                    "revenue_change_pct": "Изменение выручки, %",
                    "margin_change_pct": "Изменение маржи, %",
                },
                columns=[
                    "month",
                    "revenue",
                    "cost",
                    "margin",
                    "quantity",
                    "product_count",
                    "revenue_change_pct",
                    "margin_change_pct",
                ],
            ),
            use_container_width=True,
            hide_index=True,
            height=360,
        )
        st.download_button(
            "Скачать сводку по месяцам",
            data=to_csv_bytes(monthly_summary),
            file_name="monthly_summary.csv",
            mime="text/csv",
        )

if tab_access is not None:
    with tab_access:
        render_admin_tab(current_user, registered_salons)

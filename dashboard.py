"""
CS Operations Dashboard v3 — Masters' Union branded.
Overall Summary + per-pod drill-down for all eight priority pods.
Run with: streamlit run dashboard.py
"""

import json
import os
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from dotenv import load_dotenv
from streamlit_calendar import calendar

load_dotenv()

# Streamlit Cloud injects secrets through st.secrets, not .env. Copy them
# into os.environ so the rest of the file works unchanged in both worlds.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    pass

DATABASE_URL = os.environ["DATABASE_URL"]

LOGO_PATH = Path(__file__).parent / "assets" / "masters_union_logo.png"
SCRIBBLE_PATH = Path(__file__).parent / "assets" / "masters_union_scribble.png"


# Per-pod monthly targets, sourced from Vision FY26-27 (Devansh's 136/year
# breakdown, Naveen's MOFU/BOFU cadence, MU's Builders.mu 10/month reference).
POD_MONTHLY_TARGETS = {
    "Builders.mu": 10,
    "Brand/Ad films": 1,        # Marquee 1/quarter + Micro 2/quarter ≈ 1/month total
    "PGP Bharat IG": 8,         # Bharat.mu Sabhya — short-form rough estimate, confirm
    "Perf Ads": 10,             # 120/year (Vision: 30/quarter)
    "YT - Podcasts (Series C)": 4,    # 4/month
    "YT - Off Campus": 4,             # placeholder — Vision says cadence not locked
    "YT - Masters Of The Market": 4,  # CMT 4/month
    "YT - Family Business": 4,        # Family Business Podcast 4/month
}

# Coverage is the shoot-scheduling desk run by Shashank under Non Fiction.
# Lives in its own Google Sheet (separate from the CS Mastersheet).
COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"
COVERAGE_LEAD = "Shashank"
COVERAGE_EP = "Arun"

# Brand neutrals (from the Masters' Union brand skill)
MU_BLACK = "#171717"
MU_GREY_1 = "#D4D4D4"
MU_GREY_2 = "#A3A3A3"
MU_GREY_3 = "#737373"
MU_OFF_WHITE_1 = "#FAFAFA"
MU_OFF_WHITE_2 = "#F5F5F5"
MU_LIGHT_GREY = "#E5E5E5"

# Brand accents
MU_CYAN = "#39B6D8"
MU_YELLOW = "#F7D344"
MU_ORANGE = "#E38330"


# Each pod's "Live" definition depends on whether it has an upload step.
PODS = {
    "Builders.mu":              {"upload_cols": ["Date of Upload"],                   "lead": "Raja"},
    "Brand/Ad films":           {"upload_cols": [],                                   "lead": "Devansh"},
    "PGP Bharat IG":            {"upload_cols": ["Date of Upload"],                   "lead": "Sabhya"},
    "Perf Ads":                 {"upload_cols": [],                                   "lead": "Devansh"},
    "YT - Podcasts (Series C)": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika"},
    "YT - Off Campus":          {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika"},
    "YT - Masters Of The Market": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika"},
    "YT - Family Business":     {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika"},
}

# Display-name overrides for pods whose sheet-tab name differs from what
# the team calls them in conversation. Keep the real tab name as the dict key
# so data loading stays wired to the sheet.
POD_DISPLAY_NAMES = {
    "PGP Bharat IG": "Bharat.mu",
    "Brand/Ad films": "Brand/Ad Films",
    "Perf Ads": "Performance Ads",
}


def pod_display(pod_name: str) -> str:
    return POD_DISPLAY_NAMES.get(pod_name, pod_name)


# --- Organisational hierarchy: Sub-Department → Vertical → Pod ---
# Pods listed here use either an existing sheet-tab name (matches PODS dict),
# the special string "Coverage", or any string for a "no data yet" placeholder.
ORG = {
    "Digital": {
        "status": "active",
        "headcount_now": 51,
        "headcount_target": 70,
        "verticals": {
            "Fiction": {
                "ep": "Abhishek Mishra",
                "creative_director": "Devansh Kotak",
                "headcount": 14,
                "pods": ["Brand/Ad films", "Perf Ads", "Moonshots"],
            },
            "Non-Fiction": {
                "ep": "Arun Rengaswamy",
                "headcount": 27,
                "pods": [
                    "YouTube",
                    "Builders.mu",
                    "PGP Bharat IG",
                    "Coverage",
                ],
            },
            "Socials": {
                "lead": "Ayushi Kothari",
                "headcount": 4,
                "pods": [],
                "note": "No data integration yet — covers organic IG, LinkedIn, X.",
            },
            "Brand": {
                "lead": "Ananya Dengri",
                "headcount": 4,
                "pods": ["Newsletters", "ORM", "PR", "Partnerships"],
            },
            "Influencer Marketing": {
                "lead": "Khushi Nahar",
                "headcount": 1,
                "pods": ["Creator Campaigns"],
            },
        },
    },
    "Offline Events": {
        "status": "building",
        "headcount_now": 0,
        "headcount_target": 10,
        "verticals": {},
        "note": "Building. Flagship 5,000-person event scheduled for October 2026 at Cyber Park.",
    },
    "Books & Publishing": {
        "status": "building",
        "headcount_now": 0,
        "headcount_target": 5,
        "verticals": {},
        "note": "Building. AD - Publishing House role open.",
    },
    "New Initiatives": {
        "status": "active",
        "headcount_now": 0,
        "headcount_target": 11,
        "verticals": {
            "Project YC": {
                "lead": "TBD",
                "pods": [],
                "note": "Running. MU student team going through Y Combinator. Reports incoming.",
            },
            "Project Bran": {
                "lead": "TBD",
                "pods": [],
                "note": "Running. Reports incoming.",
            },
            "A Team": {
                "lead": "TBD",
                "pods": [],
                "note": "Working. Reports incoming.",
            },
        },
    },
    "Systems & Hiring": {
        "status": "active",
        "verticals": {},
        "note": "Hiring pipeline, dashboard system health, usage analytics. Integration coming.",
    },
    "Others": {
        "status": "active",
        "verticals": {},
        "note": "Ad-hoc projects, special initiatives, one-off engagements. Add a sheet to track.",
    },
}


# Sheets that Divyam uses regularly. Keep this short and curated.
# Add new entries as MU shares more sheet links.
SHEET_LIBRARY = [
    {
        "name": "CS Mastersheet",
        "purpose": "Production tracker for all eight priority pods.",
        "url": "https://docs.google.com/spreadsheets/d/1Y3wiAYnS2e9Pjjo420Ul7GQ0_PNpl6nNNY2DzDVXEjg/edit",
        "owner": "Creative Studio",
    },
    {
        "name": "Coverage Sheet (Main Shoot Calendar)",
        "purpose": "Shoot scheduling for the Coverage desk (Shashank).",
        "url": "https://docs.google.com/spreadsheets/d/14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM/edit",
        "owner": "Shashank Rai (Non-Fiction)",
    },
    {
        "name": "Salaries & Expenses FY26",
        "purpose": "Per-employee salaries plus expense ledger across pods.",
        "url": "https://docs.google.com/spreadsheets/d/1eok2NGU7gzhM7sGraFcyeqO-AMyyQXzj4AxhV-bXUrw/edit",
        "owner": "Finance / Director's Office",
    },
    {
        "name": "Newsletters",
        "purpose": "Paradox Weekly, Swati's Memo, Nandini's Newsletter.",
        "url": "https://docs.google.com/spreadsheets/d/1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94/edit",
        "owner": "Brand (Ananya)",
    },
    {
        "name": "ORM Tracker",
        "purpose": "Online Reputation Management — Reddit, Quora, review platforms.",
        "url": "https://docs.google.com/spreadsheets/d/1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE/edit",
        "owner": "Brand (Akash, Inagiffy retainer)",
    },
    {
        "name": "Annual Operating Plan (AOP) FY27",
        "purpose": "Per-pod annual targets. Drives AOP-attainment progress bars.",
        "url": "https://docs.google.com/spreadsheets/d/16tKPWj33VN1Y7PGRf1LqNyYHOw6gLIJErG3f6nfY5AU/edit",
        "owner": "Director's Office (Pratham Nagpal)",
    },
    {
        "name": "PR (Public Relations)",
        "purpose": "Press, publications, and tiered media coverage tracker.",
        "url": "https://docs.google.com/spreadsheets/d/1Tr4HPLouJsXRHtJDWBjsxKgLxX2StDI8Cb6f0Wyb2D0/edit",
        "owner": "Brand (Akash, Aim High India retainer)",
    },
    {
        "name": "Influencer Marketing",
        "purpose": "Marquee creator campaigns, links, impressions, and spend.",
        "url": "https://docs.google.com/spreadsheets/d/1RCMD8DHsIVBnwrIfl_2qgvt0LQZaUG2eoDFLwwuHano/edit",
        "owner": "Khushi Nahar",
    },
]


# Lead overrides for pods whose lead is not in the PODS dict (placeholder pods)
EXTRA_POD_LEADS = {
    "Moonshots": "Anu Kiran",
}


def pod_lead(pod_name: str) -> str:
    if pod_name in PODS:
        return PODS[pod_name].get("lead", "TBD")
    return EXTRA_POD_LEADS.get(pod_name, "TBD")

BASE_DATE_COLUMNS = [
    "Date of Shoot",
    "Edit Start Date",
    "Planned Date of Delivery",
    "Actual Date of Delivery",
]

STATUS_ORDER_UPLOAD = [
    "Pre-production / Ideation",
    "Shot, Awaiting Edit",
    "In Editing",
    "Delivered, Awaiting Upload",
    "Live",
]

STATUS_ORDER_DELIVERY = [
    "Pre-production / Ideation",
    "Shot, Awaiting Edit",
    "In Editing",
    "Delivered",
]

STATUS_COLOURS = {
    "Pre-production / Ideation": MU_GREY_1,
    "Shot, Awaiting Edit": MU_GREY_3,
    "In Editing": MU_YELLOW,
    "Delivered, Awaiting Upload": MU_ORANGE,
    "Delivered": MU_ORANGE,
    "Live": MU_CYAN,
}

EVENT_COLOURS = {
    "Shoot": MU_GREY_3,
    "Edit Start": MU_YELLOW,
    "Delivered": MU_ORANGE,
    "Uploaded": MU_CYAN,
}

# Cycle for colouring categorical things (shoot types, departments, etc.)
CATEGORY_COLOURS = [
    MU_CYAN, MU_YELLOW, MU_ORANGE,
    "#A78BFA", "#10B981", "#F472B6",
    "#60A5FA", "#F87171", "#34D399", "#FBBF24",
]


def colour_for(value: str) -> str:
    """Stable colour for a string label, biased to brand accents."""
    if not value:
        return MU_GREY_3
    return CATEGORY_COLOURS[hash(value) % len(CATEGORY_COLOURS)]


CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700&display=swap');

:root {
    --mu-black: #171717;
    --mu-off-white-1: #FAFAFA;
    --mu-off-white-2: #F5F5F5;
    --mu-light-grey: #E5E5E5;
    --mu-grey-2: #A3A3A3;
    --mu-grey-3: #737373;
    --mu-cyan: #39B6D8;
    --mu-yellow: #F7D344;
    --mu-orange: #E38330;
    --mu-gradient: linear-gradient(90deg, #39B6D8 0%, #F7D344 50%, #E38330 100%);
}

html, body, [class*="css"], [data-testid="stAppViewContainer"],
[data-testid="stSidebar"], .stMarkdown, .stButton, .stSelectbox, .stRadio {
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
    color: var(--mu-black);
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at 1px 1px, rgba(23,23,23,0.045) 1px, transparent 0)
        0 0 / 22px 22px,
        var(--mu-off-white-1);
}

#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stToolbar"] { visibility: hidden; height: 0 !important; }
[data-testid="stDecoration"] { display: none !important; }
[data-testid="stHeader"] { display: none !important; height: 0 !important; }
[data-testid="stAppViewBlockContainer"] { padding-top: 1rem !important; }

/* No left sidebar — single-page top-tab navigation */
[data-testid="stSidebar"], section[data-testid="stSidebar"] { display: none !important; }
[data-testid="stSidebarCollapsedControl"] { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
[data-testid="stAppViewContainer"] > .main { margin-left: 0 !important; }

.block-container {
    padding-top: 1.25rem !important;
    padding-bottom: 3.5rem !important;
    max-width: 1480px;
    animation: fadeIn 0.45s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

@keyframes slideUp {
    from { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: translateY(0); }
}

@keyframes shimmer {
    0% { background-position: -1000px 0; }
    100% { background-position: 1000px 0; }
}

@keyframes pulse {
    0%, 100% { opacity: 0.6; }
    50% { opacity: 1; }
}

/* Smoother stagger — ~50% slower easing per MU brief */
[data-testid="stMetric"] {
    animation: slideUp 0.55s cubic-bezier(0.22, 1, 0.36, 1) backwards;
}
[data-testid="column"]:nth-of-type(1) [data-testid="stMetric"] { animation-delay: 0.04s; }
[data-testid="column"]:nth-of-type(2) [data-testid="stMetric"] { animation-delay: 0.10s; }
[data-testid="column"]:nth-of-type(3) [data-testid="stMetric"] { animation-delay: 0.16s; }
[data-testid="column"]:nth-of-type(4) [data-testid="stMetric"] { animation-delay: 0.22s; }
[data-testid="column"]:nth-of-type(5) [data-testid="stMetric"] { animation-delay: 0.28s; }

.mu-pod-card {
    animation: slideUp 0.55s cubic-bezier(0.22, 1, 0.36, 1) backwards;
}
[data-testid="column"]:nth-of-type(1) .mu-pod-card { animation-delay: 0.08s; }
[data-testid="column"]:nth-of-type(2) .mu-pod-card { animation-delay: 0.16s; }

/* Tab content fade — slower & gentler */
.stTabs [role="tabpanel"] {
    animation: fadeIn 0.35s cubic-bezier(0.22, 1, 0.36, 1);
}

[data-testid="stPlotlyChart"] {
    animation: fadeIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) backwards;
    animation-delay: 0.10s;
}

[data-testid="stDataFrame"] {
    animation: fadeIn 0.45s cubic-bezier(0.22, 1, 0.36, 1) backwards;
    animation-delay: 0.05s;
}

/* Custom scrollbar — matches the techy feel */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: var(--mu-light-grey);
    border-radius: 8px;
    transition: background 0.2s;
}
::-webkit-scrollbar-thumb:hover { background: var(--mu-grey-3); }

.mu-eyebrow {
    font-family: 'DM Sans', sans-serif;
    font-weight: 600;
    font-size: 0.72rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--mu-grey-3);
    margin-bottom: 0.15rem;
}

h1 {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 700 !important;
    font-size: 2.75rem !important;
    letter-spacing: -0.03em !important;
    color: var(--mu-black) !important;
    margin-bottom: 0.35rem !important;
    line-height: 1.05 !important;
}

.mu-title-underline {
    height: 4px;
    width: 160px;
    background: var(--mu-gradient);
    border-radius: 2px;
    margin-bottom: 2rem;
}

h3 {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 600 !important;
    color: var(--mu-black) !important;
    letter-spacing: -0.015em !important;
    margin-top: 2rem !important;
    font-size: 1.4rem !important;
}

h4 {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 600 !important;
    color: var(--mu-black) !important;
    letter-spacing: -0.01em !important;
}

p, .stMarkdown p {
    font-family: 'DM Sans', sans-serif !important;
    color: var(--mu-black);
    line-height: 1.55;
}

/* Metric tiles */
[data-testid="stMetric"] {
    background:
        linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(250,250,250,1) 100%);
    padding: 1.6rem 1.5rem 1.3rem 1.5rem;
    border-radius: 16px;
    border: 1px solid var(--mu-light-grey);
    position: relative;
    overflow: hidden;
    /* Smoother eases (was 0.28s, now 0.42s — ~50% smoother) */
    transition: transform 0.42s cubic-bezier(0.22, 1, 0.36, 1),
                box-shadow 0.42s cubic-bezier(0.22, 1, 0.36, 1),
                border-color 0.42s ease;
}

[data-testid="stMetric"]::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: var(--mu-gradient);
}

[data-testid="stMetric"]::after {
    content: '';
    position: absolute;
    bottom: -40px;
    right: -40px;
    width: 120px;
    height: 120px;
    border-radius: 50%;
    background: var(--mu-gradient);
    opacity: 0.04;
    pointer-events: none;
}

[data-testid="stMetric"]:hover {
    transform: translateY(-4px);
    box-shadow: 0 20px 40px rgba(23, 23, 23, 0.10);
    border-color: transparent;
}

[data-testid="stMetricValue"] {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 700 !important;
    color: var(--mu-black) !important;
    font-size: 2.75rem !important;
    letter-spacing: -0.035em !important;
    line-height: 1 !important;
    font-variant-numeric: tabular-nums !important;
}

[data-testid="stMetricLabel"] {
    font-family: 'DM Sans', sans-serif !important;
    color: var(--mu-grey-3) !important;
    font-weight: 600 !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    margin-bottom: 0.55rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.35rem;
    border-bottom: 1px solid var(--mu-light-grey);
    padding-bottom: 0;
    margin-bottom: 1.5rem;
    flex-wrap: wrap !important;        /* allow tabs to wrap to a second line */
    overflow-x: visible !important;     /* kill the ellipsis */
    overflow-y: visible !important;
    row-gap: 0.25rem;
}

.stTabs [data-baseweb="tab"] {
    height: 3rem;
    padding: 0 1.4rem !important;
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.95rem !important;
    color: var(--mu-grey-3) !important;
    border: none !important;
    background: transparent !important;
    transition: color 0.2s ease;
    letter-spacing: -0.005em;
    white-space: nowrap !important;    /* each tab label stays on one line */
    flex-shrink: 0 !important;          /* don't shrink, don't truncate */
    max-width: none !important;
}

/* Hide Streamlit's scroll arrows that appear when the tab row overflows */
.stTabs button[kind="headerNoPadding"] {
    display: none !important;
}

.stTabs [data-baseweb="tab"]:hover {
    color: var(--mu-black) !important;
}

.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: var(--mu-black) !important;
    font-weight: 600 !important;
}

.stTabs [data-baseweb="tab-highlight"] {
    background: var(--mu-gradient) !important;
    height: 3px !important;
    border-radius: 3px 3px 0 0 !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: var(--mu-off-white-2) !important;
    border-right: 1px solid var(--mu-light-grey);
}

[data-testid="stSidebar"] h2 {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 600 !important;
    color: var(--mu-black) !important;
    font-size: 0.85rem !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}

/* Buttons */
.stButton > button {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    border-radius: 10px !important;
    border: 1px solid var(--mu-light-grey) !important;
    color: var(--mu-black) !important;
    background: #FFFFFF !important;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
    padding: 0.55rem 1.1rem !important;
    letter-spacing: -0.005em;
}

.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 8px 18px rgba(23, 23, 23, 0.08);
    border-color: var(--mu-black) !important;
}

/* Dataframe */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--mu-light-grey);
}

.stCaption, [data-testid="stCaptionContainer"] {
    font-family: 'DM Sans', sans-serif !important;
    color: var(--mu-grey-3) !important;
    font-size: 0.875rem !important;
    letter-spacing: -0.005em;
}

[data-testid="stAlert"] {
    border-radius: 12px;
    border-left-width: 4px;
    font-family: 'DM Sans', sans-serif !important;
}

.stSelectbox label, .stRadio label {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    color: var(--mu-grey-3) !important;
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
}

/* Pod card (used on Overall Summary) */
.mu-pod-card {
    background: #FFFFFF;
    border: 1px solid var(--mu-light-grey);
    border-radius: 16px;
    padding: 1.5rem 1.4rem 1.25rem 1.4rem;
    position: relative;
    overflow: hidden;
    transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1),
                box-shadow 0.25s cubic-bezier(0.16, 1, 0.3, 1),
                border-color 0.25s ease;
    height: 100%;
}

.mu-pod-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: var(--mu-gradient);
    opacity: 0.85;
}

.mu-pod-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 18px 36px rgba(23, 23, 23, 0.09);
    border-color: transparent;
}

.mu-pod-name {
    font-family: 'DM Sans', sans-serif;
    font-weight: 700;
    font-size: 1.2rem;
    color: var(--mu-black);
    letter-spacing: -0.02em;
    margin-bottom: 0.15rem;
}

.mu-pod-sub {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    color: var(--mu-grey-3);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 1rem;
}

.mu-pod-stats {
    display: flex;
    gap: 1.2rem;
    margin-bottom: 0.9rem;
}

.mu-pod-stat {
    flex: 1;
}

.mu-pod-stat-value {
    font-family: 'DM Sans', sans-serif;
    font-weight: 700;
    font-size: 1.7rem;
    color: var(--mu-black);
    letter-spacing: -0.02em;
    line-height: 1.05;
}

.mu-pod-stat-label {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.7rem;
    color: var(--mu-grey-3);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 0.1rem;
}

.mu-status-bar {
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    display: flex;
    margin-bottom: 0.5rem;
    background: var(--mu-light-grey);
}

.mu-status-bar-seg {
    height: 100%;
    transition: flex 0.3s ease;
}

.mu-status-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    margin-top: 0.25rem;
}

.mu-status-dot {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.72rem;
    color: var(--mu-grey-3);
    letter-spacing: 0.01em;
}

.mu-status-dot::before {
    content: '';
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
}

/* Gradient footer strip */
.mu-gradient-strip {
    height: 4px;
    background: var(--mu-gradient);
    border-radius: 2px;
    margin-top: 3rem;
    margin-bottom: 0.5rem;
}

.mu-footer-text {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.75rem;
    color: var(--mu-grey-3);
    letter-spacing: 0.08em;
    text-align: right;
}
</style>
"""


def parse_date(s) -> Optional[date]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                "%d %b %Y", "%d %B %Y",
                "%d/%m/%y", "%d-%m-%y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def ensure_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return {}


def pod_date_columns(pod_name: str) -> list:
    return BASE_DATE_COLUMNS + PODS.get(pod_name, {}).get("upload_cols", [])


def pod_status_order(pod_name: str) -> list:
    return (
        STATUS_ORDER_UPLOAD
        if PODS.get(pod_name, {}).get("upload_cols")
        else STATUS_ORDER_DELIVERY
    )


def _first_filled(row: dict, candidates: list) -> str:
    """Return the first non-empty value across a list of candidate column names."""
    for col in candidates:
        v = str(row.get(col, "") or "").strip()
        if v:
            return v
    return ""


# Column-name variants we tolerate so different pods with slightly different
# header conventions all compute status correctly.
SHOOT_DATE_COLS = ["Date of Shoot", "Shoot Date", "Shooting Date", "Date Shot"]
EDIT_START_COLS = ["Edit Start Date", "Editing Start Date", "Edit Date", "Edit Start"]
DELIVERED_COLS = [
    "Actual Date of Delivery", "Date of Delivery",
    "Delivered Date", "Final Delivery Date", "Delivery Date",
]


def compute_status(row: dict, pod_name: str) -> str:
    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))

    if has_upload:
        for col in PODS[pod_name]["upload_cols"]:
            if str(row.get(col, "") or "").strip():
                return "Live"

    delivered = _first_filled(row, DELIVERED_COLS)
    edit_start = _first_filled(row, EDIT_START_COLS)
    shot = _first_filled(row, SHOOT_DATE_COLS)

    if delivered:
        return "Delivered, Awaiting Upload" if has_upload else "Delivered"
    if edit_start:
        return "In Editing"
    if shot:
        return "Shot, Awaiting Edit"
    return "Pre-production / Ideation"


def has_date_in_range(row: dict, start: date, end: date, cols: list) -> bool:
    for col in cols:
        d = parse_date(row.get(col))
        if d and start <= d <= end:
            return True
    return False


def format_range(start: date, end: date) -> str:
    """Human-friendly label for a date range. Collapses to month or year
    when the range is exactly one month or falls inside one year."""
    if start == end:
        return start.strftime("%d %b %Y")
    if start.year == end.year and start.month == end.month:
        # Whole calendar month?
        first = date(start.year, start.month, 1)
        # Last day of that month
        next_month = date(start.year + (1 if start.month == 12 else 0),
                          1 if start.month == 12 else start.month + 1, 1)
        last = next_month - timedelta(days=1)
        if start == first and end == last:
            return start.strftime("%B %Y")
        return f"{start:%d %b} to {end:%d %b %Y}"
    if start.year == end.year:
        return f"{start:%d %b} to {end:%d %b %Y}"
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


@st.cache_data(ttl=1800, show_spinner="Loading Creative Studio data...")
def load_all_pod_snapshots() -> pd.DataFrame:
    """Single DB call that pulls the latest snapshot of every priority pod
    in one go. All downstream per-pod code filters this in memory."""
    tab_names = list(PODS.keys())
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                with latest as (
                    select id from (
                        select id,
                               row_number() over
                                   (partition by tab_name order by captured_at desc) as rn
                        from snapshots
                        where tab_name = any(%s)
                    ) t
                    where t.rn = 1
                )
                select s.tab_name, s.captured_at,
                       sr.row_number, sr.data, sr.is_divider
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where sr.snapshot_id in (select id from latest)
                order by s.tab_name, sr.row_number
                """,
                (tab_names,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=cols)
    if len(df):
        df["data"] = df["data"].apply(ensure_dict)
    return df


def load_latest_snapshot(tab_name: str) -> pd.DataFrame:
    """Filters the cached all-pods DataFrame down to one tab. No DB call."""
    all_df = load_all_pod_snapshots()
    return all_df[all_df["tab_name"] == tab_name].copy()


@st.cache_data(ttl=1800, show_spinner="Loading Coverage data...")
def load_coverage_snapshot() -> pd.DataFrame:
    """Latest Coverage snapshot. Filtered by sheet_id so the tab name
    can be anything containing '2026' without breaking the dashboard."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                with latest as (
                    select id from snapshots
                    where sheet_id = %s
                    order by captured_at desc
                    limit 1
                )
                select sr.row_number, sr.data, sr.is_divider,
                       s.captured_at, s.tab_name
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where sr.snapshot_id = (select id from latest)
                order by sr.row_number
                """,
                (COVERAGE_SHEET_ID,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=cols)
    if len(df):
        df["data"] = df["data"].apply(ensure_dict)
    return df


# --- Coverage helpers ---

def parse_clock_time(s) -> Optional[time]:
    """Parse a time string in many formats: '10:00', '10:00 AM', '14:30', '2 PM'."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p",
                "%I %p", "%I%p", "%H"]:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def compute_shoot_duration(row_data: dict) -> float:
    """Returns shoot duration in hours, or 0 if either time is missing."""
    t_from = parse_clock_time(row_data.get("Time (From)"))
    t_till = parse_clock_time(row_data.get("Time (Till)"))
    if not t_from or not t_till:
        return 0.0
    from_min = t_from.hour * 60 + t_from.minute
    till_min = t_till.hour * 60 + t_till.minute
    delta = till_min - from_min
    if delta < 0:  # crosses midnight
        delta += 24 * 60
    return delta / 60.0


def parse_crew(s) -> list:
    """Splits a 'who shot it' cell into individual names. Tolerates
    comma, ampersand, slash, plus, and ' and ' as separators."""
    if s is None:
        return []
    s = str(s).strip()
    if not s:
        return []
    for sep in [" & ", " and ", " AND ", " + ", "/", "+"]:
        s = s.replace(sep, ",")
    return [name.strip() for name in s.split(",") if name.strip()]


@st.cache_data(ttl=1800, show_spinner=False)
def _prepare_pod_status(pod_name: str) -> Optional[pd.DataFrame]:
    """Cached status computation. Cache key only depends on the pod name,
    so toggling the date range does NOT invalidate this expensive step."""
    df = load_latest_snapshot(pod_name)
    if df.empty:
        return None

    df = df[~df["is_divider"]].copy()
    if df.empty:
        return df

    df["status"] = df["data"].apply(lambda r: compute_status(r, pod_name))
    return df


def prepare_pod_df(pod_name: str, start: date, end: date) -> Optional[pd.DataFrame]:
    """Layer the date-range filter on top of cached pod status."""
    df = _prepare_pod_status(pod_name)
    if df is None:
        return None
    df = df.copy()
    if df.empty:
        return df
    date_cols = pod_date_columns(pod_name)
    df["in_range"] = df["data"].apply(
        lambda r: has_date_in_range(r, start, end, date_cols)
    )
    return df


def build_calendar_events(df: pd.DataFrame, pod_name: str,
                          start: date, end: date) -> list:
    events = []
    milestone_cols = [
        ("Date of Shoot", "Shoot"),
        ("Edit Start Date", "Edit Start"),
        ("Actual Date of Delivery", "Delivered"),
    ]
    for upload_col in PODS.get(pod_name, {}).get("upload_cols", []):
        milestone_cols.append((upload_col, "Uploaded"))

    for _, row in df.iterrows():
        d = row["data"]
        video_name = d.get("Video Name", "") or "(no name)"
        for col, phase in milestone_cols:
            event_date = parse_date(d.get(col))
            if event_date and start <= event_date <= end:
                text_colour = "#171717" if phase == "Edit Start" else "#FFFFFF"
                events.append({
                    "title": f"{phase} - {video_name}",
                    "start": event_date.isoformat(),
                    "end": event_date.isoformat(),
                    "backgroundColor": EVENT_COLOURS[phase],
                    "borderColor": EVENT_COLOURS[phase],
                    "textColor": text_colour,
                })
    return events


def build_timeline_df(df: pd.DataFrame, pod_name: str) -> pd.DataFrame:
    date_cols = pod_date_columns(pod_name)
    rows = []
    for _, row in df.iterrows():
        d = row["data"]
        dates = [parse_date(d.get(col)) for col in date_cols]
        dates = [x for x in dates if x]
        if not dates:
            continue
        start = min(dates)
        end = max(dates)
        if start == end:
            end = start + timedelta(days=1)
        video_name = d.get("Video Name", "") or f"(row {row['row_number']})"
        rows.append({
            "Video": video_name[:60],
            "Start": start,
            "End": end,
            "Status": compute_status(d, pod_name),
            "Lead": d.get("Lead", ""),
        })
    return pd.DataFrame(rows)


def build_full_table(df: pd.DataFrame, pod_name: str) -> pd.DataFrame:
    """Tidy table with link columns FIRST so Divyam can click straight through."""
    rows = []
    upload_cols = PODS.get(pod_name, {}).get("upload_cols", [])
    primary_upload_col = upload_cols[0] if upload_cols else None

    for _, row in df.iterrows():
        d = row["data"]
        rows.append({
            # Link columns first
            "Instagram / YouTube": d.get("Upload Link", "") or d.get("Upload link", "") or d.get("YT UPLOAD", ""),
            "Final Edit": d.get("Final Drive Link", "") or d.get("Final files (drive link)", "") or d.get("Drive Link", ""),
            "Raw Footage": d.get("Raw Vid Link", "") or d.get("Raw Footage Drive Link", ""),
            "Script": d.get("Script Link", "") or d.get("Script", "") or d.get("Script/Master doc", ""),
            # Then identity
            "Video": d.get("Video Name", "") or d.get("Podcast Name", ""),
            "Status": compute_status(d, pod_name),
            "Lead": d.get("Lead", "") or d.get("POC in Charge", "") or d.get("POC in charge", "") or d.get("Pod Lead", ""),
            "Type": d.get("Type of video", "") or d.get("Type", ""),
            # Then dates
            "Shoot Date": d.get("Date of Shoot", "") or d.get("Shoot Date", ""),
            "Edit Start": d.get("Edit Start Date", ""),
            "Planned Delivery": d.get("Planned Date of Delivery", "") or d.get("Tentative Date Of Delivery", "") or d.get("Tentative date of delivery", ""),
            "Actual Delivery": d.get("Actual Date of Delivery", "") or d.get("Date of Delivery", ""),
            "Upload Date": d.get(primary_upload_col, "") if primary_upload_col else "",
        })
    return pd.DataFrame(rows)


def collect_milestones(row_data: dict, pod_name: str) -> list:
    """Returns a list of {date, label, colour, is_planned} dicts for a single video."""
    milestones = []
    shoot = parse_date(row_data.get("Date of Shoot"))
    if shoot:
        milestones.append({
            "date": shoot, "label": "Shoot",
            "colour": EVENT_COLOURS["Shoot"], "is_planned": False,
        })
    edit_start = parse_date(row_data.get("Edit Start Date"))
    if edit_start:
        milestones.append({
            "date": edit_start, "label": "Edit Start",
            "colour": EVENT_COLOURS["Edit Start"], "is_planned": False,
        })
    planned = parse_date(row_data.get("Planned Date of Delivery"))
    if planned:
        milestones.append({
            "date": planned, "label": "Planned Delivery",
            "colour": EVENT_COLOURS["Delivered"], "is_planned": True,
        })
    actual = parse_date(row_data.get("Actual Date of Delivery"))
    if actual:
        milestones.append({
            "date": actual, "label": "Delivered",
            "colour": EVENT_COLOURS["Delivered"], "is_planned": False,
        })
    for col in PODS.get(pod_name, {}).get("upload_cols", []):
        upload = parse_date(row_data.get(col))
        if upload:
            milestones.append({
                "date": upload, "label": "Uploaded",
                "colour": EVENT_COLOURS["Uploaded"], "is_planned": False,
            })
            break
    milestones.sort(key=lambda m: m["date"])
    return milestones


def build_journey_fig(milestones: list):
    """Horizontal timeline for a single video. Planned marker is hollow, actual is solid."""
    if not milestones:
        return None

    xs = [m["date"] for m in milestones]

    fig = go.Figure()

    # Connecting backbone
    fig.add_trace(go.Scatter(
        x=xs, y=[0] * len(milestones),
        mode="lines",
        line=dict(color=MU_LIGHT_GREY, width=3),
        hoverinfo="skip",
        showlegend=False,
    ))

    # One marker trace per milestone so each can carry its own colour and symbol
    for m in milestones:
        symbol = "circle-open" if m["is_planned"] else "circle"
        fig.add_trace(go.Scatter(
            x=[m["date"]],
            y=[0],
            mode="markers+text",
            marker=dict(
                size=22,
                color=m["colour"] if not m["is_planned"] else "white",
                line=dict(color=m["colour"], width=3),
                symbol=symbol,
            ),
            text=[f"<b>{m['label']}</b><br>{m['date']:%d %b}"],
            textposition="top center",
            textfont=dict(family="DM Sans, sans-serif", size=11, color=MU_BLACK),
            hovertemplate=(
                f"<b>{m['label']}</b><br>{m['date']:%A, %d %B %Y}<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.update_layout(
        height=280,
        margin=dict(l=20, r=20, t=60, b=30),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans, sans-serif", color=MU_BLACK),
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickformat="%d %b",
            tickfont=dict(family="DM Sans, sans-serif", size=11, color=MU_GREY_3),
        ),
        yaxis=dict(
            showgrid=False, zeroline=False,
            showticklabels=False,
            range=[-0.7, 0.9],
        ),
    )
    return fig


def render_video_journey(df_month: pd.DataFrame, pod_name: str):
    """Dropdown + horizontal timeline + deltas + links for one video."""
    if df_month.empty:
        st.info("No videos to inspect this month.")
        return

    # Build the select list with readable labels
    option_rows = []
    for _, row in df_month.iterrows():
        d = row["data"]
        name = (d.get("Video Name") or "").strip() or f"(row {row['row_number']})"
        status = compute_status(d, pod_name)
        option_rows.append((f"{name}  ·  {status}", d))

    labels = [o[0] for o in option_rows]
    chosen = st.selectbox(
        "Pick a video to inspect its journey",
        labels,
    )
    chosen_data = next((o[1] for o in option_rows if o[0] == chosen), None)
    if not chosen_data:
        return

    video_name = (chosen_data.get("Video Name") or "").strip() or "Untitled"
    status = compute_status(chosen_data, pod_name)

    # Header line
    meta_bits = []
    if chosen_data.get("Lead"):
        meta_bits.append(f"Lead: <strong>{chosen_data['Lead']}</strong>")
    if chosen_data.get("Type of video"):
        meta_bits.append(f"Type: <strong>{chosen_data['Type of video']}</strong>")
    if chosen_data.get("Department"):
        meta_bits.append(f"Dept: <strong>{chosen_data['Department']}</strong>")
    if chosen_data.get("Pod"):
        meta_bits.append(f"Pod: <strong>{chosen_data['Pod']}</strong>")
    meta_html = " · ".join(meta_bits)

    status_colour = STATUS_COLOURS.get(status, MU_GREY_3)
    text_colour = MU_BLACK if status == "In Editing" else "#FFFFFF"
    header_html = f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-top:0.5rem;margin-bottom:0.5rem;gap:1rem;flex-wrap:wrap;">
      <div style="flex:1;">
        <div style="font-family:'DM Sans',sans-serif;font-weight:700;font-size:1.6rem;letter-spacing:-0.02em;color:{MU_BLACK};line-height:1.2;">
          {video_name}
        </div>
        <div style="font-family:'DM Sans',sans-serif;font-size:0.85rem;color:{MU_GREY_3};margin-top:0.35rem;">
          {meta_html}
        </div>
      </div>
      <div style="background:{status_colour};color:{text_colour};padding:0.4rem 0.9rem;border-radius:999px;font-family:'DM Sans',sans-serif;font-weight:600;font-size:0.8rem;letter-spacing:0.02em;white-space:nowrap;">
        {status}
      </div>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)

    # Timeline
    milestones = collect_milestones(chosen_data, pod_name)
    fig = build_journey_fig(milestones)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, key=f"journey_{video_name}")
    else:
        st.info("This video has no dated milestones recorded yet.")

    # Gap stats
    if len(milestones) >= 2:
        st.markdown("##### Duration between milestones")
        cols = st.columns(len(milestones) - 1)
        for i in range(len(milestones) - 1):
            delta = (milestones[i + 1]["date"] - milestones[i]["date"]).days
            label = f"{milestones[i]['label']} → {milestones[i + 1]['label']}"
            cols[i].metric(label, f"{delta} day{'s' if delta != 1 else ''}")

    # Planned vs Actual
    planned = parse_date(chosen_data.get("Planned Date of Delivery"))
    actual = parse_date(chosen_data.get("Actual Date of Delivery"))
    if planned and actual:
        delta = (actual - planned).days
        if delta == 0:
            verdict = "Delivered exactly on time."
        elif delta > 0:
            verdict = f"Delivered **{delta} day{'s' if delta != 1 else ''} late**."
        else:
            verdict = f"Delivered **{-delta} day{'s' if delta != -1 else ''} early**."
        st.info(verdict)

    # Links
    link_cols = [
        ("Raw footage", chosen_data.get("Raw Vid Link", "")),
        ("Final edit", chosen_data.get("Final Drive Link", "")),
        ("Script", chosen_data.get("Script Link", "")),
        ("Published post", chosen_data.get("Upload Link", "") or chosen_data.get("YT UPLOAD", "")),
    ]
    valid_links = [(label, url) for label, url in link_cols if url and url.startswith("http")]
    if valid_links:
        st.markdown("##### Quick links")
        link_html = "<div style='display:flex;gap:0.65rem;flex-wrap:wrap;margin-top:0.25rem;'>"
        for label, url in valid_links:
            link_html += (
                f'<a href="{url}" target="_blank" '
                f'style="text-decoration:none;background:#FFFFFF;border:1px solid {MU_LIGHT_GREY};'
                f'padding:0.45rem 0.9rem;border-radius:999px;color:{MU_BLACK};'
                f"font-family:'DM Sans',sans-serif;font-weight:500;font-size:0.8rem;"
                f'transition:all 0.2s ease;display:inline-block;">'
                f'{label} ↗</a>'
            )
        link_html += "</div>"
        st.markdown(link_html, unsafe_allow_html=True)


def style_plotly(fig):
    fig.update_layout(
        font=dict(family="DM Sans, sans-serif", color=MU_BLACK, size=12),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=60, r=20, t=40, b=70),  # more breathing room for labels
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.28,                  # push legend further below for label clearance
            xanchor="center",
            x=0.5,
            font=dict(family="DM Sans, sans-serif", color=MU_BLACK, size=11),
        ),
        xaxis=dict(
            tickangle=-30,            # rotate x-labels so they don't collide
            tickfont=dict(size=11),
            automargin=True,
        ),
        yaxis=dict(
            tickfont=dict(size=11),
            automargin=True,
        ),
        # 900ms eases — 50% smoother than the previous 600ms
        transition=dict(duration=900, easing="cubic-in-out"),
    )
    return fig


@st.cache_resource
def warm_caches():
    """Pre-warm the Postgres-backed caches on first app load so the user does
    not pay the round-trip cost on the very first interaction."""
    load_all_pod_snapshots()
    try:
        load_coverage_snapshot()
    except Exception:
        pass
    return True


def render_header():
    col_title, col_logo = st.columns([5, 1])
    with col_title:
        st.markdown(
            '<div class="mu-eyebrow">Creative Studio</div>',
            unsafe_allow_html=True,
        )
        st.title("Operations Dashboard")
        st.markdown(
            '<div class="mu-title-underline"></div>',
            unsafe_allow_html=True,
        )
    with col_logo:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=130)


def select_date_range(scope: str = "global"):
    """Date range with preset buttons (instant) + custom range inside a form
    that only applies on Submit. Default is Last 7 days."""
    today = date.today()

    # First-time default: Last 7 days (per MU brief, 2026-05-01).
    if "range_value" not in st.session_state:
        st.session_state.range_value = (today - timedelta(days=7), today)

    cur_start, cur_end = st.session_state.range_value
    is_active = lambda s, e: (cur_start == s and cur_end == e)

    # ---- Preset buttons (outside form so each click reruns instantly) ----
    pcols = st.columns([1.2, 1.2, 1.2, 1.2, 0.8])
    presets = [
        ("Last 7 days", today - timedelta(days=7), today, "qr_7"),
        ("This month",  date(today.year, today.month, 1), today, "qr_tm"),
        ("Last 30 days", today - timedelta(days=30), today, "qr_30"),
        ("YTD",          date(today.year, 1, 1), today, "qr_ytd"),
    ]
    for i, (label, ps, pe, key_root) in enumerate(presets):
        active = is_active(ps, pe)
        button_label = f"✓ {label}" if active else label
        if pcols[i].button(
            button_label,
            key=f"{key_root}_{scope}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
            st.session_state.range_value = (ps, pe)
            st.rerun()

    if pcols[4].button("↻", key=f"qr_refresh_{scope}",
                        use_container_width=True,
                        help="Refresh data from the database"):
        st.cache_data.clear()
        st.rerun()

    # ---- Custom range, batched inside a form so Apply confirms ----
    with st.form(key=f"date_form_{scope}", clear_on_submit=False, border=False):
        fcols = st.columns([2, 2, 1])
        draft_start = fcols[0].date_input(
            "From", value=cur_start, format="DD/MM/YYYY",
            label_visibility="collapsed",
        )
        draft_end = fcols[1].date_input(
            "To", value=cur_end, format="DD/MM/YYYY",
            label_visibility="collapsed",
        )
        applied = fcols[2].form_submit_button(
            "Apply custom", type="primary", use_container_width=True,
        )
        if applied:
            if draft_start > draft_end:
                draft_start, draft_end = draft_end, draft_start
            st.session_state.range_value = (draft_start, draft_end)
            st.rerun()

    st.caption(
        f"Showing data from **{cur_start:%d %b %Y}** to **{cur_end:%d %b %Y}**."
    )
    return cur_start, cur_end


def render_pod_card(pod_name: str, start_date: date, end_date: date,
                    open_state_key: str = "selected_pod",
                    show_open_button: bool = True):
    """One card on a vertical view. Clicking the open button stores the pod
    name into the given session_state key and reruns. Set show_open_button=False
    on cross-cutting views (e.g. Overview) where there is nowhere to drill."""
    df = prepare_pod_df(pod_name, start_date, end_date)

    if df is None:
        st.markdown(
            f"""
            <div class="mu-pod-card">
                <div class="mu-pod-name">{pod_name}</div>
                <div class="mu-pod-sub">Not yet snapshotted</div>
                <div class="mu-pod-stats">
                    <div class="mu-pod-stat">
                        <div class="mu-pod-stat-value">—</div>
                        <div class="mu-pod-stat-label">Active</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    df_range = df[df["in_range"]]
    has_upload = bool(PODS[pod_name]["upload_cols"])
    live_or_delivered_col = "Live" if has_upload else "Delivered"

    total = len(df_range)
    live = int((df_range["status"] == live_or_delivered_col).sum())

    # Build a horizontal status bar (segments proportional to status counts)
    order = pod_status_order(pod_name)
    counts = df_range["status"].value_counts().reindex(order).fillna(0).astype(int)
    total_for_bar = max(counts.sum(), 1)

    segments_html = ""
    for status_name in order:
        count = int(counts.get(status_name, 0))
        if count == 0:
            continue
        pct = count / total_for_bar * 100
        color = STATUS_COLOURS[status_name]
        segments_html += (
            f'<div class="mu-status-bar-seg" '
            f'style="flex:{count};background:{color};" '
            f'title="{status_name}: {count}"></div>'
        )

    legend_html = ""
    for status_name in order:
        count = int(counts.get(status_name, 0))
        if count == 0:
            continue
        color = STATUS_COLOURS[status_name]
        legend_html += (
            f'<span class="mu-status-dot" style="--dot-color:{color}">'
            f'<span style="width:8px;height:8px;border-radius:50%;background:{color};display:inline-block;margin-right:6px;"></span>'
            f'{status_name.split(",")[0]} · {count}'
            f'</span>'
        )

    pod_lead = PODS.get(pod_name, {}).get("lead", "TBD")
    card_html = f"""
    <div class="mu-pod-card">
        <div class="mu-pod-name">{pod_display(pod_name)}</div>
        <div class="mu-pod-sub">Lead: {pod_lead} · {total} active · {live} {live_or_delivered_col.lower()}</div>
        <div class="mu-pod-stats">
            <div class="mu-pod-stat">
                <div class="mu-pod-stat-value">{total}</div>
                <div class="mu-pod-stat-label">Active in range</div>
            </div>
            <div class="mu-pod-stat">
                <div class="mu-pod-stat-value">{live}</div>
                <div class="mu-pod-stat-label">{live_or_delivered_col}</div>
            </div>
        </div>
        <div class="mu-status-bar">{segments_html}</div>
        <div class="mu-status-legend">{legend_html}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    if show_open_button:
        btn_key = f"open_{open_state_key}_{pod_name}"
        if st.button(f"Open {pod_display(pod_name)} →", key=btn_key, use_container_width=True):
            st.session_state[open_state_key] = pod_name
            st.rerun()


def render_overall_summary(start_date: date, end_date: date):
    range_label = format_range(start_date, end_date)
    st.markdown(f"### All pods · {range_label}")

    # Aggregate metrics across all pods
    all_pod_dfs = {}
    for pod_name in PODS:
        df = prepare_pod_df(pod_name, start_date, end_date)
        if df is not None:
            all_pod_dfs[pod_name] = df[df["in_range"]]

    if not all_pod_dfs:
        st.warning(
            "No pods have been snapshotted yet. Run `python snapshot_all.py` "
            "from the terminal to pull every priority tab."
        )
        return

    total_active = sum(len(df) for df in all_pod_dfs.values())
    total_live = sum(
        int((df["status"] == ("Live" if PODS[p]["upload_cols"] else "Delivered")).sum())
        for p, df in all_pod_dfs.items()
    )
    total_in_editing = sum(
        int((df["status"] == "In Editing").sum())
        for df in all_pod_dfs.values()
    )
    total_shot = sum(
        int((df["status"] == "Shot, Awaiting Edit").sum())
        for df in all_pod_dfs.values()
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active across all pods", total_active)
    c2.metric("Live / Delivered", total_live)
    c3.metric("In editing", total_in_editing)
    c4.metric("Shot, awaiting edit", total_shot)

    st.markdown("### Pods")
    st.caption(
        "One card per priority pod. Click any pod to open its detail view."
    )

    # Render pods in a 2-column grid
    pod_list = list(PODS.keys())
    for i in range(0, len(pod_list), 2):
        cols = st.columns(2, gap="medium")
        for j, col in enumerate(cols):
            if i + j < len(pod_list):
                with col:
                    render_pod_card(
                        pod_list[i + j], start_date, end_date,
                        show_open_button=False,
                    )

    # Cross-pod comparison chart
    st.markdown("### Pipeline comparison")
    st.caption("Status distribution for each pod, stacked horizontally.")

    comparison_rows = []
    for pod_name, df in all_pod_dfs.items():
        for status_name in pod_status_order(pod_name):
            count = int((df["status"] == status_name).sum())
            if count > 0:
                comparison_rows.append({
                    "Pod": pod_name,
                    "Status": status_name,
                    "Count": count,
                })
    if comparison_rows:
        cmp_df = pd.DataFrame(comparison_rows)
        fig = px.bar(
            cmp_df,
            x="Count",
            y="Pod",
            color="Status",
            color_discrete_map=STATUS_COLOURS,
            orientation="h",
        )
        fig = style_plotly(fig)
        fig.update_layout(
            barmode="stack",
            height=max(320, 48 * len(all_pod_dfs) + 60),
            legend_title_text="",
            xaxis_title="",
            yaxis_title="",
        )
        st.plotly_chart(fig, use_container_width=True)


def render_pod_detail(pod_name: str, start_date: date, end_date: date):
    range_label = format_range(start_date, end_date)

    df = prepare_pod_df(pod_name, start_date, end_date)
    if df is None:
        st.error(
            f"No snapshot found for {pod_name}. Run `python snapshot_all.py` "
            "to pull every priority tab."
        )
        return

    snapshot_df = load_latest_snapshot(pod_name)
    captured_at = snapshot_df["captured_at"].iloc[0]
    pod_lead = PODS.get(pod_name, {}).get("lead", "TBD")
    st.markdown(f"### {pod_display(pod_name)} · {range_label}")
    st.caption(
        f"Lead: **{pod_lead}** · Snapshot captured {captured_at:%d %b %Y at %H:%M}"
    )

    df_month = df[df["in_range"]].reset_index(drop=True)

    has_upload = bool(PODS[pod_name]["upload_cols"])
    final_status = "Live" if has_upload else "Delivered"

    total = len(df_month)
    final_count = int((df_month["status"] == final_status).sum())
    delivered_pending = int(
        (df_month["status"] == "Delivered, Awaiting Upload").sum()
    )
    in_editing = int((df_month["status"] == "In Editing").sum())
    shot_awaiting = int((df_month["status"] == "Shot, Awaiting Edit").sum())

    # First-principles costing headline (Vision FY26-27 alignment) — top of page
    render_pod_headline(pod_name, df_month, start_date, end_date)
    st.markdown("---")
    st.markdown("##### Pipeline status")

    if has_upload:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Active in range", total)
        c2.metric("Live", final_count)
        c3.metric("Delivered, pending upload", delivered_pending)
        c4.metric("In editing", in_editing)
        c5.metric("Shot, awaiting edit", shot_awaiting)
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active in range", total)
        c2.metric("Delivered", final_count)
        c3.metric("In editing", in_editing)
        c4.metric("Shot, awaiting edit", shot_awaiting)

    if total == 0:
        st.warning(f"No videos have any date field in {range_label}.")
        return

    # Pod-level AI insights, sit above the data tabs so leadership can scan
    # the pod story before drilling into individual videos.
    render_pod_ai_insights(pod_name, start_date, end_date)

    (tab_calendar, tab_timeline, tab_status, tab_perf, tab_finance, tab_aop,
     tab_journey, tab_table, tab_notes) = st.tabs([
        "Calendar", "Timeline", "Status", "Performance", "Finance", "AOP",
        "Journey", "All Videos", "Notes",
    ])

    with tab_calendar:
        st.caption(
            "Each marker is a milestone on that day. "
            "Grey = Shoot, Yellow = Edit Start, Orange = Delivered, Cyan = Uploaded."
        )
        events = build_calendar_events(df_month, pod_name, start_date, end_date)
        initial_date = start_date.isoformat()
        calendar_options = {
            "headerToolbar": {
                "left": "today prev,next",
                "center": "title",
                "right": "dayGridMonth,dayGridWeek",
            },
            "initialView": "dayGridMonth",
            "initialDate": initial_date,
            "editable": False,
            "dayMaxEvents": 4,
            "height": 680,
        }
        calendar(
            events=events,
            options=calendar_options,
            key=f"cal_{pod_name}_{start_date.isoformat()}_{end_date.isoformat()}",
        )

    with tab_timeline:
        st.caption(
            "One bar per video, spanning its first to last milestone. "
            "Colour shows current status."
        )
        timeline_df = build_timeline_df(df_month, pod_name)
        if timeline_df.empty:
            st.info("No date-rich rows to plot this month.")
        else:
            fig = px.timeline(
                timeline_df,
                x_start="Start",
                x_end="End",
                y="Video",
                color="Status",
                color_discrete_map=STATUS_COLOURS,
                hover_data=["Lead"],
            )
            fig.update_yaxes(autorange="reversed")
            fig = style_plotly(fig)
            fig.update_layout(
                height=max(420, len(timeline_df) * 22),
                legend_title_text="",
            )
            st.plotly_chart(fig, use_container_width=True)

    with tab_status:
        order = pod_status_order(pod_name)
        status_counts = (
            df_month["status"]
            .value_counts()
            .reindex(order)
            .fillna(0)
            .astype(int)
            .reset_index()
        )
        status_counts.columns = ["Status", "Count"]
        status_counts = status_counts[status_counts["Count"] > 0]

        col_a, col_b = st.columns([1, 1])
        with col_a:
            fig = px.pie(
                status_counts,
                values="Count",
                names="Status",
                color="Status",
                color_discrete_map=STATUS_COLOURS,
                hole=0.6,
            )
            fig.update_traces(
                textinfo="label+value",
                textposition="outside",
                textfont=dict(family="DM Sans, sans-serif", size=12, color=MU_BLACK),
                marker=dict(line=dict(color="white", width=3)),
            )
            fig = style_plotly(fig)
            fig.update_layout(height=440, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            st.markdown("#### Drill down")
            options = status_counts["Status"].tolist()
            # st.pills is more clickable than st.radio; fall back gracefully.
            if hasattr(st, "pills"):
                status_choice = st.pills(
                    "status_picker",
                    options=options,
                    default=options[0] if options else None,
                    label_visibility="collapsed",
                )
            else:
                status_choice = st.radio(
                    "status_picker",
                    options=options,
                    label_visibility="collapsed",
                )
            if not status_choice:
                status_choice = options[0] if options else None

            matching = df_month[df_month["status"] == status_choice]
            st.caption(f"{len(matching)} video(s) in '{status_choice}'")
            if len(matching) > 0:
                drill = build_full_table(matching, pod_name)
                sort_col = (
                    "Planned Delivery"
                    if status_choice == "In Editing"
                    else "Video"
                )
                drill = drill.sort_values(
                    sort_col, ascending=True, na_position="last"
                )
                st.dataframe(
                    drill[[
                        "Video", "Lead",
                        "Planned Delivery", "Actual Delivery", "Upload Date",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )

    with tab_perf:
        st.caption(
            "Phase 1 performance metrics for the selected range. "
            "Numbers update as you change the date filter at the top of this tab."
        )
        render_pod_performance(df_month, pod_name)

    with tab_finance:
        st.caption(
            "Per-pod monthly cost = salary share + content expenses. "
            "Salary stays masked until you click the eye icon. Salary data is "
            "FY26 only (April 2025 to March 2026)."
        )
        try:
            render_pod_finance(pod_name, start_date, end_date, df_month)
        except Exception as e:
            import traceback
            st.error(f"Finance tab crashed: {type(e).__name__}: {e}")
            st.code(traceback.format_exc())

    with tab_aop:
        st.caption(
            "Annual Operating Plan attainment for FY27 (April 2026 to March 2027). "
            "Bars compare actuals against the annual target. The grey vertical line is the on-pace marker."
        )
        try:
            render_pod_aop(pod_name)
        except Exception as e:
            import traceback
            st.error(f"AOP tab crashed: {type(e).__name__}: {e}")
            st.code(traceback.format_exc())

    with tab_journey:
        st.caption(
            "Pick any video to see its full lifecycle laid out on a horizontal timeline. "
            "Hollow circle = planned delivery, solid circle = actual delivery."
        )
        render_video_journey(df_month, pod_name)

    with tab_notes:
        render_notes_editor(pod_name, pod_display(pod_name))

    with tab_table:
        st.caption(
            "Every video in the selected range, sortable. "
            "Click column headers to sort. Use the search box to filter."
        )
        full_table = build_full_table(df_month, pod_name)

        search = st.text_input(
            "Search",
            placeholder="Filter by video name, lead, type, or any text",
            label_visibility="collapsed",
        )
        if search:
            s = search.lower()
            mask = full_table.apply(
                lambda row: s in " ".join(str(v) for v in row.values).lower(),
                axis=1,
            )
            full_table = full_table[mask]
            st.caption(f"{len(full_table)} match(es)")

        st.dataframe(
            full_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Instagram / YouTube": st.column_config.LinkColumn(
                    "Instagram / YouTube", display_text="Open"
                ),
                "Raw Footage": st.column_config.LinkColumn(
                    "Raw Footage", display_text="Open"
                ),
                "Final Edit": st.column_config.LinkColumn(
                    "Final Edit", display_text="Open"
                ),
                "Script": st.column_config.LinkColumn(
                    "Script", display_text="Open"
                ),
            },
        )


def render_crew_hours(df_range: pd.DataFrame):
    """Total hours, Gantt-style utilization, and crosstabs."""
    records = []
    for _, row in df_range.iterrows():
        d = row["data"]
        duration = compute_shoot_duration(d)
        if duration <= 0:
            continue
        crew = parse_crew(d.get("who shot it", ""))
        if not crew:
            continue
        shoot_date = parse_date(d.get("Date"))
        t_from = parse_clock_time(d.get("Time (From)"))
        t_till = parse_clock_time(d.get("Time (Till)"))
        start_dt = end_dt = None
        if shoot_date and t_from and t_till:
            start_dt = datetime.combine(shoot_date, t_from)
            end_dt = datetime.combine(shoot_date, t_till)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
        for person in crew:
            records.append({
                "Crew": person,
                "Hours": duration,
                "Start": start_dt,
                "End": end_dt,
                "Date": d.get("Date", ""),
                "Subject": d.get("Shoot Subject", ""),
                "Department": (d.get("Department") or "").strip() or "(unspecified)",
                "Shoot Type": (d.get("Nature of Shoot Our Role") or "").strip() or "(unspecified)",
                "Lead": d.get("Shoot Lead", ""),
            })

    if not records:
        st.info(
            "No crew hours computed. Either the rows have no `who shot it` value "
            "or the time fields are empty/unparseable."
        )
        return

    crew_df = pd.DataFrame(records)
    by_person = (
        crew_df.groupby("Crew")["Hours"].sum()
        .reset_index()
        .sort_values("Hours", ascending=False)
    )
    by_person["Hours"] = by_person["Hours"].round(1)

    # Top section: total hours bar + table
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.markdown("#### Total hours per crew member")
        fig_bar = px.bar(by_person, x="Hours", y="Crew", orientation="h",
                         color_discrete_sequence=[MU_CYAN])
        fig_bar.update_yaxes(autorange="reversed")
        fig_bar = style_plotly(fig_bar)
        fig_bar.update_layout(
            height=max(280, len(by_person) * 32),
            xaxis_title="Hours", yaxis_title="",
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    with col_b:
        st.markdown("#### Totals")
        st.dataframe(by_person, use_container_width=True, hide_index=True)

    # Gantt-style: each shoot positioned by date + clock time
    gantt_df = crew_df.dropna(subset=["Start", "End"]).copy()
    if not gantt_df.empty:
        st.markdown("#### Crew utilisation timeline")
        st.caption(
            "Each bar is a shoot, positioned by date and time of day. "
            "Hover for subject, department, and shoot type."
        )
        # Order y-axis by total hours so heavy lifters are on top
        order = by_person["Crew"].tolist()
        type_colour_map = {t: colour_for(t) for t in gantt_df["Shoot Type"].unique()}
        fig_g = px.timeline(
            gantt_df, x_start="Start", x_end="End", y="Crew",
            color="Shoot Type", color_discrete_map=type_colour_map,
            hover_data=["Subject", "Department", "Date"],
            category_orders={"Crew": order},
        )
        fig_g.update_yaxes(autorange="reversed")
        fig_g = style_plotly(fig_g)
        fig_g.update_layout(
            height=max(380, len(by_person) * 38),
            xaxis_title="", yaxis_title="",
            legend_title_text="",
        )
        st.plotly_chart(fig_g, use_container_width=True)

    st.markdown("#### Crew × shoot type")
    pivot_type = pd.crosstab(
        crew_df["Crew"], crew_df["Shoot Type"],
        values=crew_df["Hours"], aggfunc="sum",
    ).round(1).fillna(0)
    if not pivot_type.empty:
        st.dataframe(pivot_type, use_container_width=True)

    st.markdown("#### Crew × department")
    pivot_dept = pd.crosstab(
        crew_df["Crew"], crew_df["Department"],
        values=crew_df["Hours"], aggfunc="sum",
    ).round(1).fillna(0)
    if not pivot_dept.empty:
        st.dataframe(pivot_dept, use_container_width=True)


def render_shoot_list(df: pd.DataFrame):
    """Compact list view of shoots."""
    rows = []
    for _, row in df.iterrows():
        d = row["data"]
        time_from = (d.get("Time (From)") or "").strip()
        time_till = (d.get("Time (Till)") or "").strip()
        time_str = (
            f"{time_from} to {time_till}" if time_from and time_till
            else time_from or time_till or ""
        )
        rows.append({
            "Date": d.get("Date", ""),
            "Subject": d.get("Shoot Subject", ""),
            "Department": d.get("Department", ""),
            "Time": time_str,
            "Location": d.get("Location", ""),
            "Lead": d.get("Shoot Lead", ""),
            "Crew": d.get("who shot it", ""),
            "Status": d.get("Status of Shoot", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_schedule(df_full: pd.DataFrame):
    """Calendar view of every shoot, plus a 'Today' highlight and quick lists."""
    today = date.today()
    df_full = df_full.copy()
    df_full["shoot_date"] = df_full["data"].apply(lambda d: parse_date(d.get("Date")))
    df_full = df_full[df_full["shoot_date"].notna()].reset_index(drop=True)

    if df_full.empty:
        st.info("No shoots with valid dates in the snapshot.")
        return

    # --- Build calendar events ---
    # Colour by Department (the closest equivalent to "pod" in this sheet),
    # so Divyam can see at a glance which team is consuming Coverage.
    # The title packs every useful detail because FullCalendar surfaces it
    # as the browser tooltip on mouse-over.
    events = []
    for _, row in df_full.iterrows():
        d = row["data"]
        shoot_date = row["shoot_date"]
        subject = (d.get("Shoot Subject") or "Untitled shoot").strip() or "Untitled shoot"
        dept = (d.get("Department") or "").strip()
        nature = (d.get("Nature of Shoot Our Role") or "").strip()
        lead = (d.get("Shoot Lead") or "").strip()
        requested_by = _get_first_nonempty(d, [
            "Who Requested", "Department POC", "Requested By", "Requestor",
        ])
        crew = (d.get("who shot it") or "").strip()
        location = (d.get("Location") or "").strip()
        time_from = (d.get("Time (From)") or "").strip()
        time_till = (d.get("Time (Till)") or "").strip()

        t_from = parse_clock_time(time_from)
        t_till = parse_clock_time(time_till)
        if t_from and t_till:
            start = datetime.combine(shoot_date, t_from).isoformat()
            end_dt = datetime.combine(shoot_date, t_till)
            if end_dt <= datetime.combine(shoot_date, t_from):
                end_dt += timedelta(days=1)
            end = end_dt.isoformat()
            time_str = f"{time_from} – {time_till}"
        else:
            start = shoot_date.isoformat()
            end = shoot_date.isoformat()
            time_str = time_from or time_till or "All-day"

        dept_label = dept if dept else "(no dept)"
        # Single-line title (multi-line breaks streamlit-calendar event rendering).
        # Hover detail goes via extendedProps which FullCalendar exposes in
        # the native title attribute that browsers render as the tooltip.
        title = f"{dept_label} · {subject}"
        tooltip_bits = [subject]
        if dept_label:
            tooltip_bits.append(f"Dept: {dept_label}")
        tooltip_bits.append(f"Time: {time_str}")
        if location:
            tooltip_bits.append(f"Location: {location}")
        if lead:
            tooltip_bits.append(f"Lead: {lead}")
        if crew:
            tooltip_bits.append(f"Crew: {crew}")
        if requested_by:
            tooltip_bits.append(f"Requested by: {requested_by}")
        if nature:
            tooltip_bits.append(f"Type: {nature}")
        tooltip = " | ".join(tooltip_bits)

        colour = colour_for(dept) if dept else MU_GREY_3
        events.append({
            "title": title,
            "start": start,
            "end": end,
            "backgroundColor": colour,
            "borderColor": colour,
            "textColor": "#FFFFFF",
            "extendedProps": {
                "tooltip": tooltip,
                "Lead": lead, "Type": nature, "Department": dept,
                "Crew": crew, "RequestedBy": requested_by, "Location": location,
            },
        })

    # Calendar widget itself
    initial_date = today.isoformat()
    calendar_options = {
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
        },
        "initialView": "dayGridMonth",
        "initialDate": initial_date,
        "editable": False,
        "dayMaxEvents": 4,
        "height": 720,
        "nowIndicator": True,
        "slotMinTime": "06:00:00",
        "slotMaxTime": "23:00:00",
    }
    # Quick badge row above the calendar
    today_count = (df_full["shoot_date"] == today).sum()
    next7_count = (
        (df_full["shoot_date"] > today)
        & (df_full["shoot_date"] <= today + timedelta(days=7))
    ).sum()
    bcol1, bcol2, bcol3 = st.columns(3)
    bcol1.metric("Today", int(today_count))
    bcol2.metric("Next 7 days", int(next7_count))
    bcol3.metric("Total in 2026", len(df_full))

    try:
        calendar(events=events, options=calendar_options, key="coverage_calendar_v3")
    except Exception as e:
        st.error(
            "Calendar widget failed to render. Error detail: "
            f"{type(e).__name__}: {e}"
        )


def _get_first_nonempty(d: dict, candidates: list) -> str:
    """Returns the first non-empty value among candidate column names."""
    for c in candidates:
        v = str(d.get(c, "") or "").strip()
        if v:
            return v
    return ""


def render_shoot_types(df_range: pd.DataFrame):
    """Two pies (shoot types + departments) plus a crosstab."""
    # Try a few common variants for each column so subtle naming differences
    # in the Coverage sheet do not silently empty the chart.
    types_series = df_range["data"].apply(
        lambda d: _get_first_nonempty(d, [
            "Nature of Shoot Our Role", "Nature of Shoot",
            "Type of Shoot", "Shoot Type",
        ])
    )
    types_filled = types_series[types_series != ""]

    depts_raw = df_range["data"].apply(
        lambda d: _get_first_nonempty(d, [
            "Department", "Requesting Department", "Dept", "department",
        ])
    )
    depts_filled = depts_raw[depts_raw != ""]
    depts_for_pie = depts_raw.replace("", "(no dept recorded)")

    if types_filled.empty and depts_filled.empty:
        st.warning(
            "Neither shoot-type nor department columns have any values "
            "in this range. Either the columns are named differently in the "
            "Coverage sheet or the cells are empty. Check the sheet."
        )
        return

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### Shoot type mix")
        if types_filled.empty:
            st.info(
                "No `Nature of Shoot Our Role` values recorded in this range. "
                "Either the column is empty in the sheet or named differently."
            )
        else:
            type_counts = types_filled.value_counts().reset_index()
            type_counts.columns = ["Type", "Count"]
            type_colour_map = {t: colour_for(t) for t in type_counts["Type"]}
            fig1 = px.pie(
                type_counts, values="Count", names="Type",
                hole=0.55, color="Type",
                color_discrete_map=type_colour_map,
            )
            fig1.update_traces(
                textinfo="label+percent",
                textfont=dict(family="DM Sans, sans-serif", size=12, color=MU_BLACK),
                marker=dict(line=dict(color="white", width=3)),
            )
            fig1 = style_plotly(fig1)
            fig1.update_layout(height=420, showlegend=False)
            st.plotly_chart(fig1, use_container_width=True)
            st.caption(f"{len(types_filled)} of {len(df_range)} shoots have a type recorded.")

    with col_b:
        st.markdown("#### Requesting departments")
        if depts_filled.empty:
            st.info(
                "No `Department` values recorded in this range. The column may "
                "be empty in the sheet, or named differently."
            )
        else:
            dept_counts = depts_filled.value_counts().reset_index()
            dept_counts.columns = ["Department", "Count"]
            dept_colour_map = {d: colour_for(d) for d in dept_counts["Department"]}
            fig2 = px.pie(
                dept_counts, values="Count", names="Department",
                hole=0.55, color="Department",
                color_discrete_map=dept_colour_map,
            )
            fig2.update_traces(
                textinfo="label+percent",
                textfont=dict(family="DM Sans, sans-serif", size=12, color=MU_BLACK),
                marker=dict(line=dict(color="white", width=3)),
            )
            fig2 = style_plotly(fig2)
            fig2.update_layout(height=420, showlegend=False)
            st.plotly_chart(fig2, use_container_width=True)
            st.caption(f"{len(depts_filled)} of {len(df_range)} shoots have a department recorded.")

    # Crosstab beneath
    pairs = []
    for _, row in df_range.iterrows():
        d = row["data"]
        st_type = _get_first_nonempty(d, [
            "Nature of Shoot Our Role", "Nature of Shoot",
            "Type of Shoot", "Shoot Type",
        ])
        dept = _get_first_nonempty(d, [
            "Department", "Requesting Department", "Dept", "department",
        ]) or "(no dept recorded)"
        if st_type:
            pairs.append({"Shoot Type": st_type, "Department": dept})
    if pairs:
        cross = pd.DataFrame(pairs)
        ct = pd.crosstab(cross["Shoot Type"], cross["Department"])
        st.markdown("#### Shoot type × requesting department")
        st.dataframe(ct, use_container_width=True)


def render_coverage(start_date: date, end_date: date):
    range_label = format_range(start_date, end_date)

    df = load_coverage_snapshot()
    if df.empty:
        st.error(
            "No Coverage snapshot found in the database yet. "
            "Two steps to fix: (1) share the Coverage sheet with the service "
            "account, (2) run `python snapshot_all.py` from the terminal."
        )
        return

    df["shoot_date"] = df["data"].apply(lambda d: parse_date(d.get("Date")))

    captured_at = df["captured_at"].iloc[0]
    st.markdown(f"### Coverage · {range_label}")
    st.caption(
        f"Lead: **{COVERAGE_LEAD}** · EP: {COVERAGE_EP} (Non Fiction) · "
        f"Snapshot {captured_at:%d %b %Y at %H:%M}"
    )

    df_range = df[
        df["shoot_date"].apply(
            lambda d: d is not None and start_date <= d <= end_date
        )
    ].copy()

    total_shoots = len(df_range)
    total_shoot_hours = sum(
        compute_shoot_duration(row["data"]) for _, row in df_range.iterrows()
    )
    unique_depts = df_range["data"].apply(
        lambda d: (d.get("Department") or "").strip()
    )
    unique_depts = unique_depts[unique_depts != ""].nunique()
    unique_crew = set()
    for _, row in df_range.iterrows():
        for person in parse_crew(row["data"].get("who shot it", "")):
            unique_crew.add(person)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Shoots in range", total_shoots)
    c2.metric("Total shoot hours", f"{total_shoot_hours:.0f}")
    c3.metric("Departments served", unique_depts)
    c4.metric("Crew members involved", len(unique_crew))

    if total_shoots == 0:
        st.warning(f"No shoots dated in {range_label}.")
        return

    tab_crew, tab_schedule, tab_types = st.tabs([
        "Crew Hours", "Schedule", "Shoot Types"
    ])

    with tab_crew:
        st.caption(
            "Each person in `who shot it` is credited with the full shoot "
            "duration. Two crew on a 4-hour shoot = 4 hours each."
        )
        render_crew_hours(df_range)

    with tab_schedule:
        st.caption(
            "Calendar of every shoot. Each event is coloured by the requesting "
            "department. Click a day or switch to Week / Day view for time-of-day detail."
        )
        render_schedule(df)

    with tab_types:
        st.caption(
            "Frequency of each `Nature of Shoot Our Role` value within the "
            "selected range, plus a department crosstab."
        )
        render_shoot_types(df_range)


YT_POD_NAMES = [
    "YT - Podcasts (Series C)",
    "YT - Off Campus",
    "YT - Masters Of The Market",
    "YT - Family Business",
]

YT_SHOW_LABELS = {
    "YT - Podcasts (Series C)": "Series C",
    "YT - Off Campus": "Off Campus",
    "YT - Masters Of The Market": "Masters of the Market",
    "YT - Family Business": "Family Business",
}


def render_youtube_cumulative(start_date: date, end_date: date):
    """One-page cumulative view across all four YouTube shows.
    Aggregate metrics, show selector, combined timeline, and a table
    with a show column."""
    range_label = format_range(start_date, end_date)
    st.markdown(f"### YouTube · {range_label}")
    st.caption(
        f"Lead: **Ishika Aggarwal (TOFU)** · "
        f"Aggregating {len(YT_POD_NAMES)} shows on one page: "
        f"{', '.join(YT_SHOW_LABELS.values())}. "
        f"Use the dropdown below to filter to a single show."
    )

    # Load each pod, tag with its show label, concat
    all_dfs = []
    for pod in YT_POD_NAMES:
        df = prepare_pod_df(pod, start_date, end_date)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["show"] = YT_SHOW_LABELS.get(pod, pod)
        df["_pod_name"] = pod
        all_dfs.append(df)

    if not all_dfs:
        st.warning(
            "No YouTube data available. Run `python snapshot_all.py` to "
            "populate the database, then refresh."
        )
        return

    combined = pd.concat(all_dfs, ignore_index=True)
    in_range_full = combined[combined["in_range"]].reset_index(drop=True)

    # Show selector
    sel_col1, sel_col2 = st.columns([2, 5])
    show_options = ["All shows"] + list(YT_SHOW_LABELS.values())
    selected_show = sel_col1.selectbox("Show", show_options, index=0)
    df_view = (
        in_range_full
        if selected_show == "All shows"
        else in_range_full[in_range_full["show"] == selected_show]
    )

    total = len(df_view)
    live = int((df_view["status"] == "Live").sum())
    delivered = int((df_view["status"] == "Delivered, Awaiting Upload").sum())
    in_editing = int((df_view["status"] == "In Editing").sum())
    shot = int((df_view["status"] == "Shot, Awaiting Edit").sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active in range", total)
    c2.metric("Live", live)
    c3.metric("Delivered, pending upload", delivered)
    c4.metric("In editing", in_editing)
    c5.metric("Shot, awaiting edit", shot)

    if total == 0:
        st.warning(f"No videos for the selected show(s) in {range_label}.")
        return

    tab_breakdown, tab_calendar, tab_timeline, tab_status, tab_table = st.tabs([
        "By show", "Calendar", "Timeline", "Status", "All Videos",
    ])

    with tab_breakdown:
        st.caption("Side-by-side comparison across the four YouTube shows.")
        # Stacked bar: each show, status segments
        rows = []
        for show_label in YT_SHOW_LABELS.values():
            show_df = in_range_full[in_range_full["show"] == show_label]
            for status in STATUS_ORDER_UPLOAD:
                count = int((show_df["status"] == status).sum())
                if count > 0:
                    rows.append({"Show": show_label, "Status": status, "Count": count})
        if rows:
            cmp_df = pd.DataFrame(rows)
            fig = px.bar(
                cmp_df, x="Count", y="Show", color="Status",
                color_discrete_map=STATUS_COLOURS, orientation="h",
            )
            fig = style_plotly(fig)
            fig.update_layout(
                barmode="stack",
                height=max(280, 60 * len(YT_SHOW_LABELS) + 60),
                xaxis_title="", yaxis_title="",
                legend_title_text="",
            )
            st.plotly_chart(fig, use_container_width=True)

    with tab_calendar:
        st.caption(
            "Every milestone across the selected shows. Each event "
            "title shows the show name first."
        )
        events = []
        for _, row in df_view.iterrows():
            d = row["data"]
            show = row["show"]
            pod = row["_pod_name"]
            video_name = (d.get("Video Name") or "").strip() or "(no name)"
            milestone_cols = [
                ("Date of Shoot", "Shoot"),
                ("Edit Start Date", "Edit Start"),
                ("Actual Date of Delivery", "Delivered"),
            ]
            for upload_col in PODS.get(pod, {}).get("upload_cols", []):
                milestone_cols.append((upload_col, "Uploaded"))

            for col, phase in milestone_cols:
                event_date = parse_date(d.get(col))
                if event_date and start_date <= event_date <= end_date:
                    text_colour = "#171717" if phase == "Edit Start" else "#FFFFFF"
                    events.append({
                        "title": f"{show}\n{phase}: {video_name}",
                        "start": event_date.isoformat(),
                        "end": event_date.isoformat(),
                        "backgroundColor": EVENT_COLOURS[phase],
                        "borderColor": EVENT_COLOURS[phase],
                        "textColor": text_colour,
                    })

        calendar_options = {
            "headerToolbar": {
                "left": "today prev,next",
                "center": "title",
                "right": "dayGridMonth,dayGridWeek",
            },
            "initialView": "dayGridMonth",
            "initialDate": start_date.isoformat(),
            "editable": False,
            "dayMaxEvents": 4,
            "height": 680,
        }
        calendar(events=events, options=calendar_options,
                 key=f"yt_cal_{selected_show}_{start_date}_{end_date}")

    with tab_timeline:
        st.caption("One bar per video, coloured by current status.")
        rows = []
        for _, row in df_view.iterrows():
            d = row["data"]
            pod = row["_pod_name"]
            dates = [parse_date(d.get(c)) for c in pod_date_columns(pod)]
            dates = [x for x in dates if x]
            if not dates:
                continue
            start = min(dates)
            end_ = max(dates)
            if start == end_:
                end_ = start + timedelta(days=1)
            video_name = (d.get("Video Name") or "").strip() or "(no name)"
            rows.append({
                "Video": f"[{row['show']}] {video_name}"[:80],
                "Start": start, "End": end_,
                "Status": row["status"],
                "Show": row["show"],
            })
        if rows:
            tdf = pd.DataFrame(rows)
            fig = px.timeline(
                tdf, x_start="Start", x_end="End", y="Video",
                color="Status", color_discrete_map=STATUS_COLOURS,
                hover_data=["Show"],
            )
            fig.update_yaxes(autorange="reversed")
            fig = style_plotly(fig)
            fig.update_layout(
                height=max(420, len(tdf) * 22),
                legend_title_text="",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No date-rich rows to plot.")

    with tab_status:
        order = STATUS_ORDER_UPLOAD
        status_counts = (
            df_view["status"].value_counts()
            .reindex(order).fillna(0).astype(int).reset_index()
        )
        status_counts.columns = ["Status", "Count"]
        status_counts = status_counts[status_counts["Count"] > 0]
        if not status_counts.empty:
            fig = px.pie(
                status_counts, values="Count", names="Status",
                color="Status", color_discrete_map=STATUS_COLOURS,
                hole=0.55,
            )
            fig.update_traces(
                textinfo="label+value", textposition="outside",
                marker=dict(line=dict(color="white", width=3)),
            )
            fig = style_plotly(fig)
            fig.update_layout(height=440)
            st.plotly_chart(fig, use_container_width=True)

    with tab_table:
        st.caption("Every YouTube video in the range, with its show.")
        rows = []
        for _, row in df_view.iterrows():
            d = row["data"]
            pod = row["_pod_name"]
            upload_cols = PODS.get(pod, {}).get("upload_cols", [])
            primary_upload = upload_cols[0] if upload_cols else None
            rows.append({
                "Show": row["show"],
                "Video": d.get("Video Name", ""),
                "Status": row["status"],
                "Lead": d.get("Lead", ""),
                "Shoot Date": d.get("Date of Shoot", ""),
                "Edit Start": d.get("Edit Start Date", ""),
                "Planned Delivery": d.get("Planned Date of Delivery", ""),
                "Actual Delivery": d.get("Actual Date of Delivery", ""),
                "Upload Date": d.get(primary_upload, "") if primary_upload else "",
                "YouTube link": d.get("YT UPLOAD", "") or d.get("Upload Link", ""),
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True,
            column_config={
                "YouTube link": st.column_config.LinkColumn(
                    "YouTube", display_text="Open"
                ),
            },
        )


# ===========================================================================
# Phase 3: AOP attainment — deliverables vs annual targets
# ===========================================================================

AOP_SHEET_ID = "16tKPWj33VN1Y7PGRf1LqNyYHOw6gLIJErG3f6nfY5AU"

# FY27 starts April 2026.
FY27_START = date(2026, 4, 1)
FY27_END = date(2027, 3, 31)

# Per-pod deliverables (subset of ROI Summary that maps to our production pods).
# `count_filter` can be a dict to filter rows by a column value (e.g. {"Type": "Marquee"}).
POD_AOP_TARGETS = {
    "Brand/Ad films": [
        {"deliverable": "Marquee Brand Films", "annual_target": 4,
         "frequency": "1/quarter", "count_filter": None,
         "note": "Filter by Type column once Marquee/Micro tagging is consistent."},
        {"deliverable": "Micro Brand Films", "annual_target": 8,
         "frequency": "2/quarter", "count_filter": None,
         "note": "Filter by Type column once Marquee/Micro tagging is consistent."},
    ],
    "Perf Ads": [
        {"deliverable": "Performance Ads", "annual_target": 120,
         "frequency": "10/month", "count_filter": None},
    ],
    # YouTube TOFU shares one cumulative target across all 4 shows:
    "YT - Podcasts (Series C)": [
        {"deliverable": "YouTube Views (cumulative target)",
         "annual_target": "12M views/year", "frequency": "1M/month",
         "count_filter": None,
         "note": "View counts require YouTube API integration (Phase 5)."},
    ],
}

# Aggregate AOP rows for the Overview AOP section.
ROI_SUMMARY_DELIVERABLES = []  # populated lazily from snapshot


@st.cache_data(ttl=600)
def _load_roi_summary() -> list:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = 'ROI Summary'
                  and s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where tab_name = 'ROI Summary' and sheet_id = %s
                      order by captured_at desc limit 1
                  )
                order by sr.row_number
                """,
                (AOP_SHEET_ID, AOP_SHEET_ID),
            )
            rows = [r[0] for r in cur.fetchall() if r[0]]
            return [r for r in rows if (r.get("Deliverable") or "").strip()]
    finally:
        conn.close()


def _count_pod_deliveries_in_fy(pod_name: str, fy_start: date, fy_end: date,
                                  count_filter: Optional[dict] = None) -> int:
    """Count rows in this pod that reached final status with a date in FY27."""
    df = _prepare_pod_status(pod_name)
    if df is None or df.empty:
        return 0
    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))
    final_status = "Live" if has_upload else "Delivered"
    upload_cols = PODS.get(pod_name, {}).get("upload_cols", [])
    delivery_cols = (DELIVERED_COLS if not has_upload else upload_cols)

    count = 0
    for _, row in df.iterrows():
        d = row["data"]
        if compute_status(d, pod_name) != final_status:
            continue
        # Date that marks "completion": upload date for upload pods, delivery date otherwise
        completion_date = None
        for col in delivery_cols:
            cd = parse_date(d.get(col))
            if cd:
                completion_date = cd
                break
        if not completion_date or not (fy_start <= completion_date <= fy_end):
            continue
        # Optional column filter
        if count_filter:
            ok = True
            for k, v in count_filter.items():
                if str(d.get(k, "")).strip().lower() != str(v).strip().lower():
                    ok = False
                    break
            if not ok:
                continue
        count += 1
    return count


def _aop_pace_status(actual: int, target: int, fy_progress: float) -> tuple:
    """Returns (label, colour, expected_actual_at_this_point)."""
    if target <= 0:
        return ("No target", MU_GREY_3, 0)
    expected = target * fy_progress
    ratio = actual / expected if expected > 0 else (1.0 if actual > 0 else 0.0)
    if ratio >= 1.0:
        return ("On pace", "#10B981", expected)
    if ratio >= 0.7:
        return ("Slightly behind", MU_YELLOW, expected)
    return ("Behind pace", MU_ORANGE, expected)


def render_pod_aop(pod_name: str):
    """Per-pod AOP attainment — progress bars vs FY27 annual target."""
    targets = POD_AOP_TARGETS.get(pod_name, [])
    if not targets:
        st.info(
            f"No AOP target found in the ROI Summary for {pod_display(pod_name)}. "
            "If this pod should have one, add an entry to `POD_AOP_TARGETS` in "
            "`dashboard.py`."
        )
        return

    today = date.today()
    if today < FY27_START:
        st.info(
            f"FY27 starts {FY27_START:%d %b %Y}. AOP attainment will populate once we are inside FY27."
        )
        fy_progress = 0.0
        days_into_fy = 0
    else:
        clamped_today = min(today, FY27_END)
        days_into_fy = (clamped_today - FY27_START).days + 1
        total_fy_days = (FY27_END - FY27_START).days + 1
        fy_progress = days_into_fy / total_fy_days

    st.caption(
        f"FY27 progress: **{fy_progress*100:.1f}%** "
        f"({days_into_fy} of {(FY27_END - FY27_START).days + 1} days). "
        f"On-pace target = annual × FY progress."
    )

    for t in targets:
        deliverable = t["deliverable"]
        annual_target = t["annual_target"]
        frequency = t["frequency"]

        if not isinstance(annual_target, int):
            # Non-numeric target (e.g. "12M views/year") — show informational card
            st.markdown(f"**{deliverable}** — {annual_target} ({frequency})")
            if t.get("note"):
                st.caption(t["note"])
            st.markdown("---")
            continue

        actual = _count_pod_deliveries_in_fy(pod_name, FY27_START, FY27_END,
                                              t.get("count_filter"))
        label, colour, expected = _aop_pace_status(actual, annual_target, fy_progress)
        pct = min(actual / annual_target, 1.0) if annual_target > 0 else 0
        expected_pct = min(fy_progress, 1.0)

        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            st.markdown(f"**{deliverable}** &nbsp;<span style='color:{colour};font-weight:600;font-size:0.85rem;'>● {label}</span>",
                        unsafe_allow_html=True)
            # Custom dual-bar progress: actual + expected mark
            bar_html = f"""
            <div style="position:relative;height:14px;background:{MU_LIGHT_GREY};border-radius:7px;overflow:hidden;margin:0.4rem 0;">
              <div style="position:absolute;inset:0 auto 0 0;width:{pct*100:.1f}%;background:{colour};transition:width 0.4s ease;"></div>
              <div style="position:absolute;top:0;bottom:0;left:{expected_pct*100:.1f}%;width:2px;background:{MU_BLACK};opacity:0.55;" title="Expected pace marker"></div>
            </div>
            """
            st.markdown(bar_html, unsafe_allow_html=True)
        with c2:
            st.metric("Shipped", actual)
        with c3:
            st.metric("Target", annual_target)

        if t.get("note"):
            st.caption(t["note"])


def render_overview_costing(start_date: date, end_date: date):
    """Cross-pod costing summary for the Overview tab."""
    months = _months_in_range(start_date, end_date)
    if not months:
        st.info("Date range does not cover any month.")
        return

    salary_visible = st.session_state.get("salary_visible", False)

    rows = []
    grand_salary = 0.0
    grand_expense = 0.0
    grand_shipped = 0
    for pod in PODS:
        salary = sum(_pod_salary_for_month(pod, y, m) for (y, m) in months)
        expense = sum(_pod_expenses_for_month(pod, y, m) for (y, m) in months)
        df = prepare_pod_df(pod, start_date, end_date)
        has_upload = bool(PODS[pod]["upload_cols"])
        final_status = "Live" if has_upload else "Delivered"
        shipped = 0
        if df is not None and not df.empty:
            df_in = df[df["in_range"]]
            shipped = int((df_in["status"] == final_status).sum())
        cost = salary + expense
        target = (POD_MONTHLY_TARGETS.get(pod) or 0) * len(months)
        rows.append({
            "Pod": pod_display(pod),
            "Target": target if target else None,
            "Shipped": shipped,
            "Salary": salary,
            "Expenses": expense,
            "Total spend": cost,
            "Cost per video": (cost / shipped) if shipped else None,
        })
        grand_salary += salary
        grand_expense += expense
        grand_shipped += shipped

    grand_total = grand_salary + grand_expense
    grand_cpv = (grand_total / grand_shipped) if grand_shipped else None

    # Top tiles — salary tile only renders when the eye is on, otherwise
    # we drop down to three tiles so nothing overflows.
    if salary_visible:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total spend (all pods)", _money_compact(grand_total))
        c2.metric("Salary share", _money_compact(grand_salary))
        c3.metric("Content expenses share", _money_compact(grand_expense))
        c4.metric("Avg cost per video", _money_compact(grand_cpv) if grand_cpv else "—")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total content expenses", _money_compact(grand_expense))
        c2.metric("Videos shipped", grand_shipped)
        c3.metric("Cost per video (excl. salary)",
                  _money_compact((grand_expense / grand_shipped) if grand_shipped else None) if grand_shipped else "—")

    st.caption(
        f"Spend window: {format_range(start_date, end_date)} "
        f"({len(months)} month{'s' if len(months)!=1 else ''}). "
        f"{grand_shipped} videos shipped across all pods."
    )

    # Per-pod table
    st.markdown("##### Per-pod breakdown")
    df = pd.DataFrame(rows)
    df_display = df.copy()
    df_display["Salary"] = df_display["Salary"].apply(
        lambda v: _money_compact(v) if salary_visible else "•••"
    )
    df_display["Expenses"] = df_display["Expenses"].apply(_money_compact)
    df_display["Total spend"] = df_display["Total spend"].apply(_money_compact)
    df_display["Cost per video"] = df_display["Cost per video"].apply(
        lambda v: _money_compact(v) if v else "—"
    )
    df_display["Target"] = df_display["Target"].apply(
        lambda v: int(v) if v else "—"
    )
    st.dataframe(df_display, use_container_width=True, hide_index=True)

    # Per-pod spend bar
    st.markdown("##### Spend per pod")
    bar_df = df[["Pod", "Salary", "Expenses"]].copy()
    if not salary_visible:
        bar_df["Salary"] = 0
    bar_long = bar_df.melt(id_vars=["Pod"], var_name="Component",
                            value_name="Amount")
    fig = px.bar(
        bar_long, x="Pod", y="Amount", color="Component",
        color_discrete_map={"Salary": MU_CYAN, "Expenses": MU_ORANGE},
        barmode="stack",
    )
    fig.update_layout(yaxis_title="₹", xaxis_title="", legend_title_text="")
    fig = style_plotly(fig)
    fig.update_layout(height=360)
    st.plotly_chart(fig, use_container_width=True, key="overview_costing_bar")


def render_overview_aop():
    """All ROI Summary deliverables in one place, with progress where computable."""
    deliverables = _load_roi_summary()
    if not deliverables:
        st.info("AOP not snapshotted yet. Run `python snapshot_all.py`.")
        return

    today = date.today()
    if today >= FY27_START:
        clamped = min(today, FY27_END)
        fy_progress = ((clamped - FY27_START).days + 1) / ((FY27_END - FY27_START).days + 1)
    else:
        fy_progress = 0.0
    st.caption(f"FY27 progress: **{fy_progress*100:.1f}%**.")

    # Group by Sub-Division
    grouped = {}
    for d in deliverables:
        sd = (d.get("Sub-Division") or "Unassigned").strip() or "Unassigned"
        grouped.setdefault(sd, []).append(d)

    for sd, items in grouped.items():
        st.markdown(f"#### {sd}")
        for d in items:
            deliverable = d.get("Deliverable", "")
            target_str = d.get("Target", "")
            annual_str = d.get("Annual Total", "")
            owner = d.get("Owner", "")
            st.markdown(
                f"- **{deliverable}** — target **{target_str}** "
                f"(annual: {annual_str}) · owner: *{owner}*"
            )
        st.markdown("")


# ===========================================================================
# Phase 2: Finance — per-pod monthly cost (salary + expenses), eye-icon toggle
# ===========================================================================

SALARIES_SHEET_ID_FOR_FINANCE = "1eok2NGU7gzhM7sGraFcyeqO-AMyyQXzj4AxhV-bXUrw"

# FY26 = April 2025 → March 2026. Map fiscal-month label to calendar (year, month).
FY26_MONTHS = {
    "April ": (2025, 4),  # note trailing space — exactly as the sheet has it
    "May":    (2025, 5),
    "June":   (2025, 6),
    "July":   (2025, 7),
    "Aug":    (2025, 8),
    "Sept":   (2025, 9),
    "Oct":    (2025, 10),
    "Nov":    (2025, 11),
    "Dec":    (2025, 12),
    "Jan":    (2026, 1),
    "Feb":    (2026, 2),
    "March":  (2026, 3),
}

# Map our dashboard pod (PODS key) to the salary + expense pod labels in the
# Salaries & Expenses FY26 sheet. Some pods share a "Youtube" salary line
# split across 4 shows; salary_share=0.25 means each show gets 1/4.
POD_FINANCE_MAP = {
    "Builders.mu": {
        "salary_pods": ["Builders.mu / Student Stories"],
        "salary_share": 1.0,
        "expense_pods": ["Builders.mu / Student Stories"],
    },
    "Brand/Ad films": {
        "salary_pods": ["Brand/Ad Films"],
        "salary_share": 1.0,
        "expense_pods": ["Brand Films"],
    },
    "PGP Bharat IG": {
        "salary_pods": ["PGP Bharat"],
        "salary_share": 1.0,
        "expense_pods": ["PGP Bharat"],
    },
    "Perf Ads": {
        "salary_pods": [],  # no dedicated salary entry yet
        "salary_share": 1.0,
        "expense_pods": ["Performance Ads"],
    },
    "YT - Podcasts (Series C)": {
        "salary_pods": ["Youtube"],
        "salary_share": 0.25,
        "expense_pods": ["YT - Series C"],
    },
    "YT - Off Campus": {
        "salary_pods": ["Youtube"],
        "salary_share": 0.25,
        "expense_pods": ["YT - Offcampus"],
    },
    "YT - Masters Of The Market": {
        "salary_pods": ["Youtube"],
        "salary_share": 0.25,
        "expense_pods": [],
    },
    "YT - Family Business": {
        "salary_pods": ["Youtube"],
        "salary_share": 0.25,
        "expense_pods": [],
    },
}


def _money_to_float(s) -> float:
    """Parse '₹808,885.00' or '162,917' or '  -  ' to a float."""
    if s is None:
        return 0.0
    s = str(s).strip().replace("\u20b9", "").replace(",", "").strip()
    if not s or s in ("-", "N/A", "na"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _months_in_range(start: date, end: date) -> list:
    """Return list of (year, month) tuples that the date range overlaps."""
    months = []
    cur_year, cur_month = start.year, start.month
    while (cur_year, cur_month) <= (end.year, end.month):
        months.append((cur_year, cur_month))
        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1
    return months


@st.cache_data(ttl=300)
def _load_pod_wise_salary() -> dict:
    """Returns {pod_label: {fiscal_month_col: float}}."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = 'Pod-Wise Salary | MoM Split'
                  and s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where tab_name = 'Pod-Wise Salary | MoM Split'
                        and sheet_id = %s
                      order by captured_at desc limit 1
                  )
                order by sr.row_number
                """,
                (SALARIES_SHEET_ID_FOR_FINANCE, SALARIES_SHEET_ID_FOR_FINANCE),
            )
            rows = [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()

    out = {}
    for r in rows:
        pod = (r.get("Pod Name") or "").strip()
        if not pod or pod.lower() == "grand total":
            continue
        out[pod] = {}
        for fy_col in FY26_MONTHS:
            sum_col = f"SUM of {fy_col}"
            out[pod][fy_col] = _money_to_float(r.get(sum_col, "0"))
    return out


@st.cache_data(ttl=300)
def _load_salary_employees() -> list:
    """Per-employee salary rows for the eye-icon reveal table."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = 'Salary Data'
                  and s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where tab_name = 'Salary Data'
                        and sheet_id = %s
                      order by captured_at desc limit 1
                  )
                order by sr.row_number
                """,
                (SALARIES_SHEET_ID_FOR_FINANCE, SALARIES_SHEET_ID_FOR_FINANCE),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


@st.cache_data(ttl=300)
def _load_content_expenses() -> list:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = 'Content Expenses'
                  and s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where tab_name = 'Content Expenses'
                        and sheet_id = %s
                      order by captured_at desc limit 1
                  )
                order by sr.row_number
                """,
                (SALARIES_SHEET_ID_FOR_FINANCE, SALARIES_SHEET_ID_FOR_FINANCE),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def _pod_salary_for_month(pod_name: str, year: int, month: int) -> float:
    """Sum salary for our pod for a single calendar month, applying salary_share."""
    fin = POD_FINANCE_MAP.get(pod_name)
    if not fin or not fin["salary_pods"]:
        return 0.0
    # Find the FY26 column matching this calendar (year, month)
    fy_col = next(
        (c for c, ym in FY26_MONTHS.items() if ym == (year, month)),
        None,
    )
    if fy_col is None:
        return 0.0  # outside FY26 range

    pod_wise = _load_pod_wise_salary()
    total = 0.0
    for sp in fin["salary_pods"]:
        # Match by stripping spaces (sheet uses leading/trailing spaces)
        for sheet_pod, monthly in pod_wise.items():
            if sheet_pod.strip() == sp.strip():
                total += monthly.get(fy_col, 0.0) * fin["salary_share"]
    return total


def _pod_expenses_for_month(pod_name: str, year: int, month: int) -> float:
    """Sum content expenses for our pod for a calendar month, by Date of Shoot."""
    fin = POD_FINANCE_MAP.get(pod_name)
    if not fin or not fin["expense_pods"]:
        return 0.0
    expense_pod_labels = {p.strip() for p in fin["expense_pods"]}

    rows = _load_content_expenses()
    total = 0.0
    for r in rows:
        pod_val = (r.get("Pod") or "").strip()
        if pod_val not in expense_pod_labels:
            continue
        # Date column may be 'Date of Shoot' or 'Date of Payment'
        d = parse_date(r.get("Date of Shoot")) or parse_date(r.get("Date of Payment"))
        if d and d.year == year and d.month == month:
            total += _money_to_float(
                r.get("Amount with GST") or r.get("Total (Without GST)")
            )
    return total


def render_pod_finance(pod_name: str, start_date: date, end_date: date,
                        df_range: pd.DataFrame):
    """Phase 2 financial view: per-month cost, cost per content piece,
    salary breakdown behind eye icon."""
    fin_cfg = POD_FINANCE_MAP.get(pod_name)
    if not fin_cfg:
        st.info(f"No finance mapping configured for {pod_display(pod_name)}.")
        return

    months = _months_in_range(start_date, end_date)
    if not months:
        st.info("Date range does not cover any month.")
        return

    # Aggregate per month
    monthly = []
    for (y, m) in months:
        salary = _pod_salary_for_month(pod_name, y, m)
        expense = _pod_expenses_for_month(pod_name, y, m)
        monthly.append({
            "Month": datetime(y, m, 1).strftime("%b %Y"),
            "year": y, "month": m,
            "Salary": salary,
            "Expenses": expense,
            "Total": salary + expense,
        })

    total_salary = sum(m["Salary"] for m in monthly)
    total_expense = sum(m["Expenses"] for m in monthly)
    total_cost = total_salary + total_expense

    # Count content pieces shipped in range (Live or Delivered)
    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))
    final_status = "Live" if has_upload else "Delivered"
    if df_range is not None and not df_range.empty:
        shipped = int((df_range["status"] == final_status).sum())
    else:
        shipped = 0

    cost_per_video = (total_cost / shipped) if shipped else None

    # Eye icon toggle
    if "salary_visible" not in st.session_state:
        st.session_state.salary_visible = False

    def fmt_money(x):
        if x is None or x == 0:
            return "—"
        if x >= 1_00_00_000:
            return f"₹{x/1_00_00_000:.2f} Cr"
        if x >= 1_00_000:
            return f"₹{x/1_00_000:.2f} L"
        return f"₹{x:,.0f}"

    def mask_money(x):
        return "₹•••,•••" if x else "—"

    # Headline cards
    salary_str = fmt_money(total_salary) if st.session_state.salary_visible else mask_money(total_salary)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cost (range)", fmt_money(total_cost))
    c2.metric("Salary share", salary_str)
    c3.metric("Expenses share", fmt_money(total_expense))
    c4.metric(
        "Cost per content piece",
        fmt_money(cost_per_video) if cost_per_video else "—",
        help=f"Total cost ÷ {shipped} {final_status.lower()} pieces in range."
    )

    # Eye toggle button
    btn_label = "🙈 Hide salary" if st.session_state.salary_visible else "👁️ Show salary"
    if st.button(btn_label, key=f"salary_toggle_{pod_name}"):
        st.session_state.salary_visible = not st.session_state.salary_visible
        st.rerun()

    if not fin_cfg["salary_pods"]:
        st.warning(
            f"{pod_display(pod_name)} has no dedicated salary entry in the "
            "Salaries sheet. Salary share is shown as zero. Expenses still count."
        )

    # Monthly trend
    if len(monthly) > 1:
        st.markdown("#### Monthly cost trend")
        m_df = pd.DataFrame(monthly)
        m_df["Salary_show"] = m_df["Salary"] if st.session_state.salary_visible else 0
        chart_df = pd.melt(
            m_df,
            id_vars=["Month"],
            value_vars=(["Salary_show", "Expenses"] if st.session_state.salary_visible else ["Expenses"]),
            var_name="Component", value_name="Amount",
        )
        chart_df["Component"] = chart_df["Component"].replace({"Salary_show": "Salary"})
        fig = px.bar(
            chart_df, x="Month", y="Amount", color="Component",
            barmode="stack",
            color_discrete_map={"Salary": MU_CYAN, "Expenses": MU_ORANGE},
        )
        fig = style_plotly(fig)
        fig.update_layout(height=320, yaxis_title="₹", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    # Per-employee breakdown (only if salary visible)
    if st.session_state.salary_visible and fin_cfg["salary_pods"]:
        st.markdown("#### Salary breakdown by employee")
        st.caption(
            "Showing CTC for employees whose Pod Name matches this pod in "
            "the Salaries sheet. Salary-share factor applies for shared pods (YT)."
        )
        sal_pod_set = {sp.strip() for sp in fin_cfg["salary_pods"]}
        employees = _load_salary_employees()
        rows = []
        share = fin_cfg["salary_share"]
        for emp in employees:
            pn = (emp.get("Pod Name") or "").strip()
            if pn not in sal_pod_set:
                continue
            try:
                fixed = _money_to_float(emp.get(" FIXED CTC ", "0"))
                total_ctc = _money_to_float(emp.get(" TOTAL CTC ", "0"))
            except Exception:
                fixed = total_ctc = 0
            rows.append({
                "Name": emp.get("NAME", ""),
                "Role": emp.get("ROLE", ""),
                "Pod": pn,
                "Fixed CTC (annual)": fixed,
                "Total CTC (annual)": total_ctc,
                "This pod's share": share,
                "Pod-effective annual": total_ctc * share,
            })
        if rows:
            ed = pd.DataFrame(rows).sort_values("Total CTC (annual)", ascending=False)
            st.dataframe(ed, use_container_width=True, hide_index=True,
                         column_config={
                             "Fixed CTC (annual)": st.column_config.NumberColumn(format="₹%d"),
                             "Total CTC (annual)": st.column_config.NumberColumn(format="₹%d"),
                             "Pod-effective annual": st.column_config.NumberColumn(format="₹%d"),
                         })


def _money_compact(x):
    """Compact money for headline tiles: ₹1.2 Cr / ₹85 L / ₹4,000."""
    if x is None or x == 0:
        return "—"
    if x >= 1_00_00_000:
        return f"₹{x/1_00_00_000:.2f} Cr"
    if x >= 1_00_000:
        return f"₹{x/1_00_000:.1f} L"
    return f"₹{x:,.0f}"


def render_pod_headline(pod_name: str, df_in_range: pd.DataFrame,
                         start_date: date, end_date: date):
    """First-principles costing view sitting above the tabs on each pod page.
    Five tiles: Target, Shipped, Spend, Cost per video, Views (Phase 5)."""
    months = _months_in_range(start_date, end_date)
    monthly_target = POD_MONTHLY_TARGETS.get(pod_name)
    target = (monthly_target * len(months)) if monthly_target else None

    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))
    final_status = "Live" if has_upload else "Delivered"
    shipped = int((df_in_range["status"] == final_status).sum()) if not df_in_range.empty else 0

    total_salary = sum(_pod_salary_for_month(pod_name, y, m) for (y, m) in months)
    total_expense = sum(_pod_expenses_for_month(pod_name, y, m) for (y, m) in months)
    total_spend = total_salary + total_expense

    cost_per_video = (total_spend / shipped) if shipped else None

    # Cost-per-view target (Das Paisa principle from MU's brief):
    # short form (Instagram, YT Shorts) target = ₹0.10/view
    # long form (YouTube long, podcast) target = ₹1/view
    is_long_form_pod = pod_name.startswith("YT - ") and "Shorts" not in pod_name
    cpv_target = 1.0 if is_long_form_pod else 0.10

    salary_visible = st.session_state.get("salary_visible", False)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Target this range",
        target if target is not None else "Not set",
        help=(f"{monthly_target} per month × {len(months)} months in range. "
              "Source: Vision FY26-27.") if monthly_target else "Add a number to POD_MONTHLY_TARGETS in dashboard.py."
    )
    delta = None
    if target is not None:
        gap = shipped - target
        delta = f"{gap:+d} vs target"
    c2.metric(f"{final_status} this range", shipped, delta=delta,
              delta_color="normal" if (target is None or shipped >= target) else "inverse")
    # Spend tile — never asks for the eye on the headline; expenses always shown.
    c3.metric(
        "Content expenses",
        _money_compact(total_expense) if total_expense else "—",
        help="Direct content expenses for this range (excludes salary unless eye toggle is on)."
    )
    c4.metric(
        "Cost per video",
        _money_compact(cost_per_video) if cost_per_video else "—",
        help=(f"Total spend ÷ {shipped} {final_status.lower()} videos. "
              + ("Includes salary." if salary_visible else "Excludes salary; toggle on Finance tab to include."))
    )
    fmt_form = "long-form" if is_long_form_pod else "short-form"
    c5.metric(
        "Target CPV",
        f"₹{cpv_target:.2f} / view",
        help=(f"Das Paisa principle: ₹0.10/view target for short form, ₹1.00/view for long form. "
              f"This pod treated as {fmt_form}. Actual CPV pending YT/IG analytics integration.")
    )


def compute_pod_performance(df_in_range: pd.DataFrame, pod_name: str) -> dict:
    """Phase 1 metrics: TAT, slippage, pipeline conversion, on-time delivery."""
    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))
    upload_cols = PODS.get(pod_name, {}).get("upload_cols", [])
    final_status = "Live" if has_upload else "Delivered"

    tats_shoot_to_live = []
    tats_edit_to_delivery = []
    tats_shoot_to_edit = []
    slippage_days = []  # negative = early, positive = late
    items_with_shoot = 0
    items_at_final = 0

    for _, row in df_in_range.iterrows():
        d = row["data"]
        shoot = parse_date(_first_filled_str(d, ["Date of Shoot", "Shoot Date"]))
        edit_start = parse_date(_first_filled_str(d, ["Edit Start Date", "Edit Date"]))
        planned = parse_date(_first_filled_str(d, [
            "Planned Date of Delivery", "Tentative Date Of Delivery",
            "Tentative date of delivery",
        ]))
        actual = parse_date(_first_filled_str(d, [
            "Actual Date of Delivery", "Date of Delivery",
        ]))
        upload = None
        for col in (["Date of Upload"] + upload_cols):
            u = parse_date(d.get(col))
            if u:
                upload = u
                break

        if shoot:
            items_with_shoot += 1
        if compute_status(d, pod_name) == final_status:
            items_at_final += 1

        if shoot and upload:
            tats_shoot_to_live.append((upload - shoot).days)
        if edit_start and actual:
            tats_edit_to_delivery.append((actual - edit_start).days)
        if shoot and edit_start:
            tats_shoot_to_edit.append((edit_start - shoot).days)
        if planned and actual:
            slippage_days.append((actual - planned).days)

    def avg(xs):
        return (sum(xs) / len(xs)) if xs else None

    on_time = sum(1 for s in slippage_days if s <= 0)
    on_time_rate = (on_time / len(slippage_days)) if slippage_days else None
    conversion = (items_at_final / items_with_shoot) if items_with_shoot else None

    return {
        "shipped": items_at_final,
        "items_with_shoot": items_with_shoot,
        "avg_shoot_to_live": avg(tats_shoot_to_live),
        "avg_edit_to_delivery": avg(tats_edit_to_delivery),
        "avg_shoot_to_edit": avg(tats_shoot_to_edit),
        "slippage_days": slippage_days,
        "on_time_rate": on_time_rate,
        "conversion_rate": conversion,
        "n_shoot_to_live": len(tats_shoot_to_live),
        "n_edit_to_delivery": len(tats_edit_to_delivery),
        "n_shoot_to_edit": len(tats_shoot_to_edit),
        "n_slippage": len(slippage_days),
    }


def _first_filled_str(d: dict, keys: list) -> str:
    for k in keys:
        v = str(d.get(k, "") or "").strip()
        if v:
            return v
    return ""


def _fmt_days(value):
    if value is None:
        return "—"
    return f"{value:.1f}d"


def _fmt_pct(value):
    if value is None:
        return "—"
    return f"{value*100:.0f}%"


def render_pod_performance(df_range: pd.DataFrame, pod_name: str):
    """Performance tab: TAT averages, slippage histogram, conversion + on-time."""
    if df_range.empty:
        st.info("No videos in this range to compute performance.")
        return

    m = compute_pod_performance(df_range, pod_name)
    final_status = "Live" if PODS.get(pod_name, {}).get("upload_cols") else "Delivered"

    # Top-line KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(f"{final_status} this period", m["shipped"])
    k2.metric(
        "On-time delivery rate",
        _fmt_pct(m["on_time_rate"]),
        help=f"Of {m['n_slippage']} videos with both planned and actual delivery dates, % delivered on or before planned."
    )
    k3.metric(
        "Pipeline conversion",
        _fmt_pct(m["conversion_rate"]),
        help=f"{m['shipped']} of {m['items_with_shoot']} shot videos reached {final_status}."
    )
    k4.metric(
        "Avg slippage",
        _fmt_days(sum(m["slippage_days"]) / len(m["slippage_days"]) if m["slippage_days"] else None),
        help="Average days late (positive) or early (negative) versus planned delivery."
    )

    st.markdown("#### Average turnaround time")
    t1, t2, t3 = st.columns(3)
    t1.metric(
        "Shoot → Edit start", _fmt_days(m["avg_shoot_to_edit"]),
        help=f"Across {m['n_shoot_to_edit']} videos with both dates filled."
    )
    t2.metric(
        "Edit start → Delivered", _fmt_days(m["avg_edit_to_delivery"]),
        help=f"Across {m['n_edit_to_delivery']} videos with both dates filled."
    )
    t3.metric(
        "Shoot → Live", _fmt_days(m["avg_shoot_to_live"]),
        help=f"Across {m['n_shoot_to_live']} videos with both dates filled."
    )

    if m["slippage_days"]:
        st.markdown("#### Delivery slippage distribution")
        st.caption("Each bar is a count of videos with that day-delta. Negative is early, positive is late.")
        slip_df = pd.DataFrame({"days": m["slippage_days"]})
        fig = px.histogram(
            slip_df, x="days", nbins=max(8, min(20, len(m["slippage_days"]))),
            color_discrete_sequence=[MU_CYAN],
        )
        fig.update_layout(
            xaxis_title="Days late (+) / early (−)",
            yaxis_title="Count of videos",
            bargap=0.08,
        )
        fig.add_vline(x=0, line_dash="dash", line_color=MU_GREY_3,
                      annotation_text="On time")
        fig = style_plotly(fig)
        fig.update_layout(height=320)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough Planned-vs-Actual delivery data to render the slippage chart.")


def render_pod_in_vertical(pod_name: str, start_date: date, end_date: date,
                            state_key: str):
    """Dispatch a pod to the right detail renderer."""
    if pod_name == "Coverage":
        render_coverage(start_date, end_date)
    elif pod_name == "YouTube":
        render_youtube_cumulative(start_date, end_date)
    elif pod_name == "Newsletters":
        render_newsletters_placeholder(start_date, end_date)
    elif pod_name == "ORM":
        render_orm_placeholder(start_date, end_date)
    elif pod_name == "PR":
        render_pr_placeholder(start_date, end_date)
    elif pod_name == "Partnerships":
        render_partnerships_placeholder(start_date, end_date)
    elif pod_name == "Creator Campaigns":
        render_influencer_marketing(start_date, end_date)
    elif pod_name in PODS:
        render_pod_detail(pod_name, start_date, end_date)
    else:
        # Placeholder pod (e.g. Moonshots) — show a friendly empty state.
        st.markdown(f"### {pod_display(pod_name)}")
        st.caption(f"Lead: **{pod_lead(pod_name)}**")
        st.info(
            "No data source connected for this pod yet. "
            "When the relevant sheet exists, share it with the service account "
            "and add it to `snapshot_all.py`."
        )


def _placeholder_pod(title: str, lead: str, note: str, sheet_url: str = ""):
    """Common rendering for pods whose data source exists but is not yet
    snapshotted into the database."""
    st.markdown(f"### {title}")
    st.caption(f"Lead: **{lead}**")
    st.info(note)
    if sheet_url:
        st.markdown(
            f'<a href="{sheet_url}" target="_blank" '
            f'style="text-decoration:none;background:#FFFFFF;border:1px solid {MU_LIGHT_GREY};'
            f'padding:0.5rem 1rem;border-radius:999px;color:{MU_BLACK};'
            f"font-family:'DM Sans',sans-serif;font-weight:500;font-size:0.85rem;"
            f'display:inline-block;margin-top:0.5rem;">'
            f'Open the source sheet ↗</a>',
            unsafe_allow_html=True,
        )


NEWSLETTERS_SHEET_ID = "1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94"
ORM_SHEET_ID = "1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE"


@st.cache_data(ttl=300)
def _load_newsletter_tab(tab_name: str) -> list:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = %s and s.sheet_id = %s
                  and s.id = (select id from snapshots where tab_name=%s and sheet_id=%s order by captured_at desc limit 1)
                  and not sr.is_divider
                order by sr.row_number
                """,
                (tab_name, NEWSLETTERS_SHEET_ID, tab_name, NEWSLETTERS_SHEET_ID),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


@st.cache_data(ttl=300)
def _load_orm_tab(tab_name: str) -> list:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = %s and s.sheet_id = %s
                  and s.id = (select id from snapshots where tab_name=%s and sheet_id=%s order by captured_at desc limit 1)
                  and not sr.is_divider
                order by sr.row_number
                """,
                (tab_name, ORM_SHEET_ID, tab_name, ORM_SHEET_ID),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def _parse_count(s) -> Optional[float]:
    """Parse numeric strings like '41,895', '1.2 Mn', '32%', returns float."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("-", "N/A"):
        return None
    s = s.replace(",", "")
    is_pct = "%" in s
    s = s.replace("%", "").strip()
    multiplier = 1.0
    if s.lower().endswith(" mn") or s.lower().endswith("mn"):
        multiplier = 1_000_000.0
        s = s.lower().replace("mn", "").strip()
    elif s.lower().endswith(" k") or s.lower().endswith("k"):
        multiplier = 1_000.0
        s = s.lower().replace("k", "").strip()
    try:
        return float(s) * multiplier / (100.0 if is_pct else 1.0)
    except ValueError:
        return None


def _parse_newsletter_date(s):
    """Parse '28th January, 2026' or '23rd March, 2026' style dates."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Strip ordinal suffixes (st, nd, rd, th)
    import re
    s_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for fmt in ["%d %B, %Y", "%d %B %Y", "%d %b, %Y", "%d %b %Y", "%d/%m/%Y"]:
        try:
            return datetime.strptime(s_clean, fmt).date()
        except ValueError:
            continue
    return None


def _render_one_newsletter(name: str, lead: str, tab_name: str):
    rows = _load_newsletter_tab(tab_name)
    if not rows:
        st.info(f"No data snapshotted for {name}. Run `python snapshot_all.py`.")
        return

    # Build trend data
    trend = []
    latest = None
    for r in rows:
        d = _parse_newsletter_date(r.get("Newsletter Deployment Date"))
        if not d:
            continue
        sent = _parse_count(r.get("Processed (Sent)"))
        delivered = _parse_count(r.get("Delivered"))
        opens = _parse_count(r.get("Absolute Opens"))
        open_rate = _parse_count(r.get("Open rates"))
        clicks = _parse_count(r.get("Absolute Clicks"))
        unsub = _parse_count(r.get("Unsubscribers"))
        trend.append({
            "Date": d, "Sent": sent or 0, "Delivered": delivered or 0,
            "Opens": opens or 0, "Open rate": (open_rate or 0) * 100,
            "Clicks": clicks or 0, "Unsubscribers": unsub or 0,
            "Subject": r.get("Subject Line (#1)") or r.get("Subject Line") or "",
        })

    if not trend:
        st.info(f"{name} has rows but no parseable deployment dates.")
        return

    trend_df = pd.DataFrame(trend).sort_values("Date")
    latest_row = trend_df.iloc[-1]

    # KPI tiles
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest sent", f"{int(latest_row['Sent']):,}")
    c2.metric("Latest delivered", f"{int(latest_row['Delivered']):,}")
    c3.metric("Latest open rate", f"{latest_row['Open rate']:.1f}%")
    c4.metric("Latest unsubscribes", f"{int(latest_row['Unsubscribers']):,}")
    st.caption(f"Lead: **{lead}** · Latest send: {latest_row['Date']:%d %b %Y} · "
               f"Subject: *{latest_row['Subject']}*")

    # Trend chart: open rate over time
    fig = px.line(
        trend_df, x="Date", y="Open rate", markers=True,
        color_discrete_sequence=[MU_CYAN],
    )
    fig.update_layout(
        yaxis_title="Open rate (%)", xaxis_title="",
        yaxis=dict(range=[0, max(trend_df['Open rate'].max() * 1.2, 50)]),
    )
    fig = style_plotly(fig)
    fig.update_layout(height=300)
    st.plotly_chart(fig, use_container_width=True, key=f"nl_open_{tab_name}")

    # Sent + Delivered trend
    fig2 = px.bar(
        trend_df, x="Date", y=["Sent", "Delivered"], barmode="overlay",
        color_discrete_map={"Sent": MU_LIGHT_GREY, "Delivered": MU_CYAN},
    )
    fig2.update_layout(yaxis_title="Recipients", xaxis_title="", legend_title_text="")
    fig2 = style_plotly(fig2)
    fig2.update_layout(height=280)
    st.plotly_chart(fig2, use_container_width=True, key=f"nl_send_{tab_name}")

    # Recent issues table
    st.markdown("##### Recent issues")
    display = trend_df.tail(10).iloc[::-1].copy()
    display["Date"] = display["Date"].apply(lambda d: d.strftime("%d %b %Y"))
    display["Open rate"] = display["Open rate"].apply(lambda v: f"{v:.1f}%")
    display["Sent"] = display["Sent"].apply(lambda v: f"{int(v):,}")
    display["Delivered"] = display["Delivered"].apply(lambda v: f"{int(v):,}")
    display["Opens"] = display["Opens"].apply(lambda v: f"{int(v):,}")
    st.dataframe(display[["Date", "Subject", "Sent", "Delivered", "Opens", "Open rate"]],
                 use_container_width=True, hide_index=True)


def render_newsletters_placeholder(start_date: date, end_date: date):
    """Three sub-tabs: Paradox Weekly, Swati's Memo, Nandini's Newsletter."""
    st.markdown("### Newsletters")
    st.caption("Lead: **Ananya Dengri** (Brand). All three newsletters in one place.")
    render_retainer_card("Newsletters", len(_months_in_range(start_date, end_date)))

    nl_tabs = st.tabs(["Paradox Weekly (PM)", "Swati's Memo", "Nandini's Newsletter"])
    with nl_tabs[0]:
        _render_one_newsletter("Paradox Weekly", "Pratham Mittal", "MU newsletters")
    with nl_tabs[1]:
        _render_one_newsletter("Swati's Memo", "Swati Ganeti", "Swati NL")
    with nl_tabs[2]:
        _render_one_newsletter("Nandini's Newsletter", "Nandini Seth", "Nandini NL")


def render_orm_placeholder(start_date: date, end_date: date):
    """Two sub-tabs: Reddit, Quora."""
    st.markdown("### ORM (Online Reputation Management)")
    st.caption("Lead: **Akash P K** (Brand) · Retainer: Inagiffy.")
    render_retainer_card("ORM", len(_months_in_range(start_date, end_date)))

    orm_tabs = st.tabs(["Reddit", "Quora"])

    with orm_tabs[0]:
        rows = _load_orm_tab("Reddit")
        if not rows:
            st.info("No Reddit data snapshotted yet.")
        else:
            data = []
            for r in rows:
                m = (r.get("Month'Yr") or "").strip()
                if not m:
                    continue
                data.append({
                    "Month": m,
                    "Views": _parse_count(r.get("Views")) or 0,
                    "Engagement": _parse_count(r.get("Engagement")) or 0,
                    "Positive %": _parse_count(r.get("Positive Sentiment %")) or 0,
                    "Negative %": _parse_count(r.get("Negative Sentiment %")) or 0,
                    "Neutral %": _parse_count(r.get("Neutral Sentiment %")) or 0,
                })
            if data:
                df = pd.DataFrame(data)
                latest = df.iloc[-1]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Latest month views", f"{int(latest['Views']):,}")
                c2.metric("Latest engagement", f"{int(latest['Engagement']):,}")
                c3.metric("Positive sentiment", f"{latest['Positive %']*100:.1f}%")
                c4.metric("Negative sentiment", f"{latest['Negative %']*100:.1f}%")

                fig = px.line(df, x="Month", y="Views", markers=True,
                              color_discrete_sequence=[MU_CYAN])
                fig = style_plotly(fig)
                fig.update_layout(height=300, yaxis_title="Views", xaxis_title="")
                st.plotly_chart(fig, use_container_width=True, key="orm_reddit_views")

                # Sentiment stacked area
                sent_df = df[["Month", "Positive %", "Neutral %", "Negative %"]].copy()
                for c in ["Positive %", "Neutral %", "Negative %"]:
                    sent_df[c] = sent_df[c] * 100
                sent_long = sent_df.melt(id_vars=["Month"], var_name="Sentiment", value_name="Percent")
                fig2 = px.area(sent_long, x="Month", y="Percent", color="Sentiment",
                               color_discrete_map={
                                   "Positive %": "#10B981",
                                   "Neutral %": MU_GREY_2,
                                   "Negative %": MU_ORANGE,
                               })
                fig2 = style_plotly(fig2)
                fig2.update_layout(height=280, yaxis_title="% share", xaxis_title="",
                                   legend_title_text="")
                st.plotly_chart(fig2, use_container_width=True, key="orm_reddit_sent")

    with orm_tabs[1]:
        rows = _load_orm_tab("Quora")
        if not rows:
            st.info("No Quora data snapshotted yet.")
        else:
            data = []
            for r in rows:
                m = (r.get("Month'Yr") or "").strip()
                if not m:
                    continue
                data.append({
                    "Month": m,
                    "Views": _parse_count(r.get("Views")) or 0,
                    "Answers Posted": _parse_count(r.get("Answers Posted")) or 0,
                    "Followers": _parse_count(r.get("Folowers")) or 0,
                    "Comments": _parse_count(r.get("Comments")) or 0,
                })
            if data:
                df = pd.DataFrame(data)
                latest = df.iloc[-1]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Latest month views", f"{int(latest['Views']):,}")
                c2.metric("Answers posted", f"{int(latest['Answers Posted']):,}")
                c3.metric("Followers", f"{int(latest['Followers']):,}")
                c4.metric("Comments", f"{int(latest['Comments']):,}")

                fig = px.line(df, x="Month", y="Views", markers=True,
                              color_discrete_sequence=[MU_YELLOW])
                fig = style_plotly(fig)
                fig.update_layout(height=300, yaxis_title="Views", xaxis_title="")
                st.plotly_chart(fig, use_container_width=True, key="orm_quora_views")

                fig2 = px.line(df, x="Month", y="Followers", markers=True,
                               color_discrete_sequence=[MU_CYAN])
                fig2 = style_plotly(fig2)
                fig2.update_layout(height=280, yaxis_title="Followers", xaxis_title="")
                st.plotly_chart(fig2, use_container_width=True, key="orm_quora_followers")


def render_pr_placeholder(start_date: date, end_date: date):
    st.markdown("### PR (Public Relations)")
    st.caption("Lead: **Akash P K** · Retainer: **Aim High India**.")
    render_retainer_card("PR", len(_months_in_range(start_date, end_date)))
    st.info(
        "PR sheet is connected (`Tr4HPLouJsXRHt...`). Pulls the most-recent "
        "fortnightly tab. Ask MU for which tab to snapshot regularly and the "
        "publications-per-month-tiered view will populate."
    )


def render_partnerships_placeholder(start_date: date, end_date: date):
    _placeholder_pod(
        "Partnerships",
        "Ananya Dengri (Brand)",
        "Brand-to-brand collaborations (distinct from creator deals, which "
        "live under Influencer Marketing). No data source connected yet.",
    )


# ============================================================================
# Retainer attribution — Inagiffy → ORM + Newsletters; Aim High → PR
# ============================================================================
# Monthly retainer costs (₹). Update these when MU confirms exact rates.
RETAINER_MONTHLY = {
    "ORM": {
        "vendor": "Inagiffy Solutions",
        "monthly_cost": 400000,  # ₹4L/month
        "scope": "Reddit + Quora ORM management",
    },
    "Newsletters": {
        "vendor": "Inagiffy Solutions",
        "monthly_cost": 100000,  # ₹1L/month
        "scope": "Newsletter ops + ESP management for all three newsletters",
    },
    "PR": {
        "vendor": "Aim High India",
        "monthly_cost": 200000,  # ₹2L/month — estimate, MU to confirm
        "scope": "PR strategy, press outreach, publication tracking",
    },
}


def render_retainer_card(area_key: str, months_in_range: int):
    """Compact retainer attribution tile for ORM / Newsletters / PR pages."""
    info = RETAINER_MONTHLY.get(area_key)
    if not info:
        return
    total = info["monthly_cost"] * max(months_in_range, 1)
    st.markdown(
        f"""
        <div class="mu-pod-card" style="margin-bottom:1rem;">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem;">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{MU_ORANGE};"></span>
            <span style="font-family:'DM Sans',sans-serif;font-weight:700;font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;color:{MU_BLACK};">
              Retainer cost in range
            </span>
          </div>
          <div style="display:flex;gap:1.5rem;flex-wrap:wrap;">
            <div>
              <div style="font-family:'DM Sans',sans-serif;font-weight:700;font-size:1.7rem;color:{MU_BLACK};letter-spacing:-0.02em;">
                ₹{total/100000:.1f} L
              </div>
              <div style="font-family:'DM Sans',sans-serif;font-size:0.72rem;color:{MU_GREY_3};letter-spacing:0.08em;text-transform:uppercase;">
                Total ({months_in_range} {"month" if months_in_range==1 else "months"})
              </div>
            </div>
            <div>
              <div style="font-family:'DM Sans',sans-serif;font-weight:600;font-size:1.05rem;color:{MU_BLACK};">{info['vendor']}</div>
              <div style="font-family:'DM Sans',sans-serif;font-size:0.78rem;color:{MU_GREY_3};line-height:1.45;">
                ₹{info['monthly_cost']/100000:.1f} L / month · {info['scope']}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# Influencer Marketing view — campaign cards, sortable, click-through detail
# ============================================================================

INFLUENCER_SHEET_ID = "1RCMD8DHsIVBnwrIfl_2qgvt0LQZaUG2eoDFLwwuHano"


@st.cache_data(ttl=1800)
def _load_influencer_collabs() -> list:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.tab_name = '2025 Collabs' and s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where tab_name = '2025 Collabs' and sheet_id = %s
                      order by captured_at desc limit 1
                  )
                  and not sr.is_divider
                order by sr.row_number
                """,
                (INFLUENCER_SHEET_ID, INFLUENCER_SHEET_ID),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def _parse_influencer_date(s):
    """Influencer dates look like 'Wednesday, 30 April' (no year). Assume 2025."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    import re
    s_clean = re.sub(r"^[A-Za-z]+,\s*", "", s).strip()  # drop weekday
    s_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s_clean)
    if not re.search(r"\d{4}", s_clean):
        s_clean += " 2025"
    for fmt in ["%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(s_clean, fmt).date()
        except ValueError:
            continue
    return None


def _campaign_record(row: dict) -> dict:
    """Normalise one row from 2025 Collabs into a card-friendly record."""
    name = (row.get("Name ") or row.get("Name") or "").strip() or "(no name)"
    return {
        "Name": name,
        "Agency": (row.get("Agency") or "").strip(),
        "Platform": (row.get("Platform") or "").strip(),
        "Status": (row.get("Status") or "").strip(),
        "SPOC": (row.get("Executed by MU : SPOC") or "").strip(),
        "Vertical": (row.get("Executed for : Vertical") or "").strip(),
        "Course": (row.get("Executed for : Course") or "").strip(),
        "Date": _parse_influencer_date(row.get("Date of Publish")),
        "Date raw": (row.get("Date of Publish") or "").strip(),
        "Spend": _money_to_float(row.get("Budget Spent")),
        "Views": int(_parse_count(row.get("No. of Views")) or 0),
        "Impressions": int(_parse_count(row.get("No. of Impression")) or 0),
        "Likes": int(_parse_count(row.get("Likes")) or 0),
        "Comments": int(_parse_count(row.get("Comments")) or 0),
        "Shares": int(_parse_count(row.get("Shares")) or 0),
        "Saves": int(_parse_count(row.get("Saves")) or 0),
        "CPV": _money_to_float(row.get("Cost Per View")),
        "Engagement": (row.get("Engagement Rate") or "").strip(),
        "Score SPOC": (row.get("Campaign Score (by the SPOC)") or "").strip(),
        "Score PnL": (row.get("Campaign Score (by the P&L Holder)") or "").strip(),
        "Link": (row.get("Link of Published Post") or row.get("Link") or "").strip(),
        "UTM": (row.get("UTM (if any)") or "").strip(),
        "POC": (row.get("Agency POC") or "").strip(),
        "Email": (row.get("Email ID") or "").strip(),
        "Remarks": (row.get("Remarks/Learnings by Khushi") or row.get("Remarks") or "").strip(),
    }


def render_influencer_marketing(start_date: date, end_date: date):
    """Marquee creator-campaign view with sortable cards and click-through."""
    st.markdown("### Influencer Marketing")
    st.caption(
        "Lead: **Khushi Nahar** · Marquee creator campaigns from agencies "
        "(Monk-E, YAAS, Finnet, Orlina). Sort, filter, click any campaign to drill into detail."
    )

    rows = _load_influencer_collabs()
    if not rows:
        st.warning("No Influencer Marketing data in the database. Run `python snapshot_all.py`.")
        return

    campaigns = [_campaign_record(r) for r in rows]
    # Filter to date range (campaigns without a date are kept, marked N/A)
    in_range = []
    no_date = []
    for c in campaigns:
        if c["Date"] is None:
            no_date.append(c)
        elif start_date <= c["Date"] <= end_date:
            in_range.append(c)

    use_set = in_range if in_range else (no_date if no_date else campaigns)

    # Headline KPIs
    total_spend = sum(c["Spend"] for c in use_set)
    total_views = sum(c["Views"] for c in use_set)
    total_impr = sum(c["Impressions"] for c in use_set)
    avg_cpv = (total_spend / total_views) if total_views else None
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Campaigns", len(use_set))
    k2.metric("Total spend", _money_compact(total_spend))
    k3.metric("Total views", f"{total_views:,}")
    k4.metric("Total impressions", f"{total_impr:,}")
    k5.metric("Avg cost per view", f"₹{avg_cpv:.2f}" if avg_cpv else "—")

    # Filters and sort
    fcol1, fcol2, fcol3 = st.columns(3)
    agencies = sorted({c["Agency"] for c in use_set if c["Agency"]})
    platforms = sorted({c["Platform"] for c in use_set if c["Platform"]})
    sel_agency = fcol1.multiselect("Filter by agency", agencies, default=[])
    sel_platform = fcol2.multiselect("Filter by platform", platforms, default=[])
    sort_by = fcol3.selectbox(
        "Sort by",
        ["Views (most popular)", "Spend (highest)", "CPV (lowest first)",
         "Date (most recent)"],
    )

    filtered = [
        c for c in use_set
        if (not sel_agency or c["Agency"] in sel_agency)
        and (not sel_platform or c["Platform"] in sel_platform)
    ]

    if sort_by == "Views (most popular)":
        filtered.sort(key=lambda c: c["Views"], reverse=True)
    elif sort_by == "Spend (highest)":
        filtered.sort(key=lambda c: c["Spend"], reverse=True)
    elif sort_by == "CPV (lowest first)":
        filtered.sort(key=lambda c: c["CPV"] if c["CPV"] > 0 else 9e9)
    else:
        filtered.sort(key=lambda c: c["Date"] or date(1900, 1, 1), reverse=True)

    st.caption(f"Showing **{len(filtered)}** of {len(use_set)} campaigns.")

    # Campaign open state (stays in session)
    open_key = "influencer_open_campaign"
    open_idx = st.session_state.get(open_key)

    if open_idx is not None and 0 <= open_idx < len(filtered):
        c = filtered[open_idx]
        if st.button("← Back to all campaigns", key="back_inf_campaign"):
            st.session_state[open_key] = None
            st.rerun()
        _render_campaign_detail(c)
        return

    # Cards grid (2 columns)
    for i in range(0, len(filtered), 2):
        cols = st.columns(2, gap="medium")
        for j, col in enumerate(cols):
            if i + j < len(filtered):
                with col:
                    _render_campaign_card(filtered[i + j], i + j, open_key)


def _render_campaign_card(c: dict, idx: int, open_key: str):
    platform_colour = colour_for(c["Platform"]) if c["Platform"] else MU_GREY_3
    cpv_label = f"₹{c['CPV']:.2f}/view" if c['CPV'] > 0 else "—"
    views_label = f"{c['Views']:,}" if c['Views'] else "—"
    spend_label = _money_compact(c["Spend"]) if c["Spend"] else "—"
    date_label = c["Date"].strftime("%d %b %Y") if c["Date"] else c["Date raw"] or "No date"

    card_html = f"""
    <div class="mu-pod-card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.6rem;margin-bottom:0.6rem;">
        <div>
          <div class="mu-pod-name">{c['Name']}</div>
          <div class="mu-pod-sub">{c['Agency'] or 'No agency'} · {date_label}</div>
        </div>
        <div style="background:{platform_colour};color:#FFFFFF;padding:0.25rem 0.65rem;border-radius:999px;font-family:'DM Sans',sans-serif;font-size:0.7rem;font-weight:600;letter-spacing:0.05em;white-space:nowrap;">
          {c['Platform'] or 'N/A'}
        </div>
      </div>
      <div class="mu-pod-stats">
        <div class="mu-pod-stat">
          <div class="mu-pod-stat-value">{views_label}</div>
          <div class="mu-pod-stat-label">Views</div>
        </div>
        <div class="mu-pod-stat">
          <div class="mu-pod-stat-value">{spend_label}</div>
          <div class="mu-pod-stat-label">Spend</div>
        </div>
        <div class="mu-pod-stat">
          <div class="mu-pod-stat-value">{cpv_label}</div>
          <div class="mu-pod-stat-label">CPV</div>
        </div>
      </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
    if st.button(f"Open campaign →", key=f"open_inf_{idx}", use_container_width=True):
        st.session_state[open_key] = idx
        st.rerun()


def _render_campaign_detail(c: dict):
    date_label = c["Date"].strftime("%A, %d %B %Y") if c["Date"] else (c["Date raw"] or "Date not recorded")
    platform_colour = colour_for(c["Platform"]) if c["Platform"] else MU_GREY_3

    # Header
    st.markdown(
        f"""
        <div style="margin:0.5rem 0 1.5rem 0;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;">
            <div>
              <div style="font-family:'DM Sans',sans-serif;font-weight:700;font-size:1.8rem;color:{MU_BLACK};letter-spacing:-0.02em;line-height:1.15;">
                {c['Name']}
              </div>
              <div style="font-family:'DM Sans',sans-serif;font-size:0.92rem;color:{MU_GREY_3};margin-top:0.3rem;">
                {c['Agency']} · {date_label} · SPOC: {c['SPOC'] or 'N/A'}
              </div>
            </div>
            <div style="background:{platform_colour};color:#FFFFFF;padding:0.4rem 1rem;border-radius:999px;font-family:'DM Sans',sans-serif;font-weight:600;font-size:0.85rem;">
              {c['Platform'] or 'N/A'} · {c['Status'] or 'No status'}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # KPI row 1: views/spend/cpv
    cv1, cv2, cv3, cv4 = st.columns(4)
    cv1.metric("Views", f"{c['Views']:,}" if c['Views'] else "—")
    cv2.metric("Impressions", f"{c['Impressions']:,}" if c['Impressions'] else "—")
    cv3.metric("Spend", _money_compact(c['Spend']) if c['Spend'] else "—")
    cv4.metric("Cost per view", f"₹{c['CPV']:.2f}" if c['CPV'] > 0 else "—")

    # KPI row 2: engagement
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Likes", f"{c['Likes']:,}")
    e2.metric("Comments", f"{c['Comments']:,}")
    e3.metric("Shares", f"{c['Shares']:,}")
    e4.metric("Saves", f"{c['Saves']:,}")

    if c["Engagement"]:
        st.caption(f"Engagement rate: **{c['Engagement']}**")

    # Scores
    if c["Score SPOC"] or c["Score PnL"]:
        st.markdown("##### Campaign scores")
        s1, s2 = st.columns(2)
        s1.metric("SPOC score", c["Score SPOC"] or "—")
        s2.metric("P&L Holder score", c["Score PnL"] or "—")

    # Links
    links = []
    if c["Link"] and c["Link"].startswith("http"):
        links.append(("Published post", c["Link"]))
    if c["UTM"] and c["UTM"].startswith("http"):
        links.append(("UTM tracker", c["UTM"]))
    if links:
        st.markdown("##### Links")
        link_html = "<div style='display:flex;gap:0.6rem;flex-wrap:wrap;margin-top:0.25rem;'>"
        for label, url in links:
            link_html += (
                f'<a href="{url}" target="_blank" '
                f'style="text-decoration:none;background:#FFFFFF;border:1px solid {MU_LIGHT_GREY};'
                f'padding:0.5rem 1rem;border-radius:999px;color:{MU_BLACK};'
                f"font-family:'DM Sans',sans-serif;font-weight:500;font-size:0.85rem;display:inline-block;\">"
                f'{label} ↗</a>'
            )
        link_html += "</div>"
        st.markdown(link_html, unsafe_allow_html=True)

    # Embedded preview for Instagram links
    if c["Link"] and "instagram.com/reel" in c["Link"]:
        st.markdown("##### Preview")
        st.markdown(
            f'<iframe src="{c["Link"]}embed/" '
            f'width="400" height="700" frameborder="0" '
            f'scrolling="no" allowtransparency="true" '
            f'style="border-radius:12px;"></iframe>',
            unsafe_allow_html=True,
        )

    # Agency contact
    if c["POC"] or c["Email"]:
        st.markdown("##### Agency contact")
        st.caption(f"{c['POC']} · {c['Email']}")

    # Remarks
    if c["Remarks"]:
        st.markdown("##### Khushi's notes")
        st.write(c["Remarks"])


def render_vertical(sd: str, v: str, start_date: date, end_date: date):
    """Header + pod cards (or empty state) for a single vertical."""
    v_data = ORG[sd]["verticals"][v]
    pods = v_data.get("pods", [])

    # Header line: EP / Lead / headcount
    meta_parts = []
    if v_data.get("ep"):
        meta_parts.append(f"EP: **{v_data['ep']}**")
    if v_data.get("creative_director"):
        meta_parts.append(f"CD: **{v_data['creative_director']}**")
    if v_data.get("lead"):
        meta_parts.append(f"Lead: **{v_data['lead']}**")
    if v_data.get("headcount"):
        meta_parts.append(f"{v_data['headcount']} people")
    if meta_parts:
        st.caption(" · ".join(meta_parts))

    if not pods:
        st.info(v_data.get("note", "No data sources integrated yet."))
        return

    # Per-vertical "open pod" state
    state_key = (
        f"open_pod_{sd}_{v}"
        .replace(" ", "_").replace("/", "_").replace("-", "_")
    )
    if state_key not in st.session_state:
        st.session_state[state_key] = None

    open_pod = st.session_state[state_key]

    if open_pod:
        # Detail view with back button
        if st.button(
            f"← Back to {v}",
            key=f"back_{state_key}",
            use_container_width=False,
        ):
            st.session_state[state_key] = None
            st.rerun()
        render_pod_in_vertical(open_pod, start_date, end_date, state_key)
    else:
        # Pod cards in a 2-column grid
        for i in range(0, len(pods), 2):
            cols = st.columns(2, gap="medium")
            for j, col in enumerate(cols):
                if i + j < len(pods):
                    pod = pods[i + j]
                    with col:
                        if pod == "Coverage":
                            _render_coverage_card(state_key)
                        elif pod in PODS:
                            render_pod_card(pod, start_date, end_date,
                                            open_state_key=state_key)
                        else:
                            _render_placeholder_card(pod, state_key)


def _render_coverage_card(state_key: str):
    """A pod card for Coverage (different data source from production pods)."""
    card_html = f"""
    <div class="mu-pod-card">
        <div class="mu-pod-name">Coverage</div>
        <div class="mu-pod-sub">Lead: {COVERAGE_LEAD} · Shoot scheduling desk</div>
        <div class="mu-pod-stats">
            <div class="mu-pod-stat">
                <div class="mu-pod-stat-value">→</div>
                <div class="mu-pod-stat-label">Open to view</div>
            </div>
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
    if st.button(f"Open Coverage →",
                 key=f"open_{state_key}_coverage",
                 use_container_width=True):
        st.session_state[state_key] = "Coverage"
        st.rerun()


def _render_placeholder_card(pod_name: str, state_key: str):
    """Card for pods that have no data source connected yet."""
    card_html = f"""
    <div class="mu-pod-card">
        <div class="mu-pod-name">{pod_display(pod_name)}</div>
        <div class="mu-pod-sub">Lead: {pod_lead(pod_name)} · No data yet</div>
        <div class="mu-pod-stats">
            <div class="mu-pod-stat">
                <div class="mu-pod-stat-value">—</div>
                <div class="mu-pod-stat-label">Awaiting sheet</div>
            </div>
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
    if st.button(f"Open {pod_display(pod_name)} →",
                 key=f"open_{state_key}_{pod_name}",
                 use_container_width=True):
        st.session_state[state_key] = pod_name
        st.rerun()


def render_subdept(sd: str, start_date: date, end_date: date):
    """Top-level metrics + vertical tabs for a sub-department."""
    sd_data = ORG[sd]

    # Sub-dept header
    h_parts = []
    if sd_data.get("headcount_now") is not None:
        h_parts.append(f"{sd_data['headcount_now']} people today")
    if sd_data.get("headcount_target"):
        h_parts.append(f"FY27 target: {sd_data['headcount_target']}")
    if h_parts:
        st.caption(" · ".join(h_parts))

    # Building states get a friendly placeholder, no verticals
    if sd_data.get("status") == "building" or not sd_data.get("verticals"):
        st.info(sd_data.get("note", "Building. No team yet."))
        return

    # Vertical tabs inside the sub-dept
    vertical_keys = list(sd_data["verticals"].keys())
    v_tabs = st.tabs(vertical_keys)
    for j, v in enumerate(vertical_keys):
        with v_tabs[j]:
            render_vertical(sd, v, start_date, end_date)


def _aggregate_pod_metrics(start_date: date, end_date: date) -> dict:
    """Returns a dict with cross-pod totals plus a list of in-progress items."""
    totals = {
        "active": 0, "live": 0, "delivered_pending": 0,
        "in_editing": 0, "shot_awaiting": 0,
        "in_progress_items": [],  # list of dicts
    }
    for pod_name in PODS:
        df = prepare_pod_df(pod_name, start_date, end_date)
        if df is None:
            continue
        df_in = df[df["in_range"]]
        has_upload = bool(PODS[pod_name]["upload_cols"])
        live_status = "Live" if has_upload else "Delivered"
        totals["active"] += len(df_in)
        totals["live"] += int((df_in["status"] == live_status).sum())
        totals["delivered_pending"] += int(
            (df_in["status"] == "Delivered, Awaiting Upload").sum()
        )
        totals["in_editing"] += int((df_in["status"] == "In Editing").sum())
        totals["shot_awaiting"] += int(
            (df_in["status"] == "Shot, Awaiting Edit").sum()
        )

        # In-progress items (anything not Live/Delivered final)
        not_final = df_in[df_in["status"] != live_status]
        for _, row in not_final.iterrows():
            d = row["data"]
            totals["in_progress_items"].append({
                "Pod": pod_display(pod_name),
                "Video": (d.get("Video Name") or "").strip() or "(unnamed)",
                "Lead": d.get("Lead", ""),
                "Status": row["status"],
                "Planned Delivery": d.get("Planned Date of Delivery", ""),
                "Last Touched": (
                    d.get("Last Update")
                    or d.get("Edit Start Date")
                    or d.get("Date of Shoot")
                    or ""
                ),
            })
    return totals


def render_cross_pod_activity(start_date: date, end_date: date,
                               totals: dict):
    """Single table of every in-progress video across every production pod."""
    items = totals["in_progress_items"]
    if not items:
        st.info("Nothing in progress across the production pods for this range.")
        return

    df = pd.DataFrame(items).sort_values(["Status", "Pod", "Video"])
    st.dataframe(
        df, use_container_width=True, hide_index=True,
    )


def render_upcoming_shoots_overview():
    """Mini calendar of the next 14 days of Coverage shoots, no tables."""
    cov = load_coverage_snapshot()
    if cov.empty:
        st.info(
            "No Coverage snapshot loaded. Make sure the Coverage sheet is shared "
            "with the service account and run `python snapshot_all.py`."
        )
        return

    today = date.today()
    horizon = today + timedelta(days=14)
    cov = cov.copy()
    cov["shoot_date"] = cov["data"].apply(lambda d: parse_date(d.get("Date")))
    cov = cov[cov["shoot_date"].notna()]
    upcoming = cov[(cov["shoot_date"] >= today) & (cov["shoot_date"] <= horizon)]

    if upcoming.empty:
        st.info("No upcoming shoots in the next 14 days.")
        return

    events = []
    for _, row in upcoming.iterrows():
        d = row["data"]
        shoot_date = row["shoot_date"]
        subject = (d.get("Shoot Subject") or "Untitled shoot").strip() or "Untitled shoot"
        dept = (d.get("Department") or "").strip()
        lead = (d.get("Shoot Lead") or "").strip()
        t_from = parse_clock_time(d.get("Time (From)"))
        t_till = parse_clock_time(d.get("Time (Till)"))
        if t_from and t_till:
            start_iso = datetime.combine(shoot_date, t_from).isoformat()
            end_dt = datetime.combine(shoot_date, t_till)
            if end_dt <= datetime.combine(shoot_date, t_from):
                end_dt += timedelta(days=1)
            end_iso = end_dt.isoformat()
        else:
            start_iso = shoot_date.isoformat()
            end_iso = shoot_date.isoformat()
        dept_label = dept if dept else "(no dept)"
        title = f"{dept_label} · {subject}"
        colour = colour_for(dept) if dept else MU_GREY_3
        events.append({
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "backgroundColor": colour,
            "borderColor": colour,
            "textColor": "#FFFFFF",
            "extendedProps": {
                "Lead": lead, "Department": dept, "Subject": subject,
            },
        })

    options = {
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,listWeek",
        },
        "initialView": "dayGridMonth",
        "initialDate": today.isoformat(),
        "editable": False,
        "dayMaxEvents": 3,
        "height": 520,
    }
    try:
        calendar(events=events, options=options, key="overview_upcoming_calendar")
    except Exception as e:
        st.error(f"Calendar widget failed: {type(e).__name__}: {e}")


def _notes_filename(pod_key: str) -> Path:
    """Resolve a pod tab name (or '_overall') to a meeting-notes filepath."""
    notes_dir = Path(__file__).parent / "meeting_notes"
    notes_dir.mkdir(exist_ok=True)
    fname = (
        pod_key
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("-", "")
        .replace(".", "_")
    )
    return notes_dir / f"{fname}.md"


def _load_meeting_notes(pod_key: str = "_overall") -> str:
    """Load latest meeting notes for a pod. Returns first ~2000 chars."""
    candidate = _notes_filename(pod_key)
    if not candidate.exists():
        return ""
    try:
        text = candidate.read_text(encoding="utf-8")
        return text[:2000]
    except Exception:
        return ""


def _save_meeting_notes(pod_key: str, content: str) -> bool:
    """Write notes to disk. Returns True on success."""
    candidate = _notes_filename(pod_key)
    try:
        candidate.write_text(content or "", encoding="utf-8")
        return True
    except Exception:
        return False


def render_notes_editor(pod_key: str, label: str):
    """Inline text editor for meeting notes. No Markdown knowledge needed."""
    current = _load_meeting_notes(pod_key)
    state_key = f"notes_text_{pod_key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = current

    st.markdown(f"#### Notes for {label}")
    st.caption(
        "Type or paste any context that should weigh into the AI insights for "
        "this section. Save and refresh the AI insights tab to see Claude weave "
        "your notes into wins / risks / today. Plain text. No formatting required."
    )
    new_text = st.text_area(
        f"Meeting notes — {label}",
        value=st.session_state[state_key],
        height=240,
        key=f"notes_textarea_{pod_key}",
        label_visibility="collapsed",
        placeholder=(
            "Example:\n"
            "28 Apr — Decided to push the Goa storyline to next month; "
            "Aryan back on 5 May. Two videos slipped past planned delivery "
            "this week. Top priority: ship the Founder Stories backlog."
        ),
    )
    cols = st.columns([1, 1, 4])
    if cols[0].button("Save notes", key=f"notes_save_{pod_key}", type="primary"):
        if _save_meeting_notes(pod_key, new_text):
            st.session_state[state_key] = new_text
            # Invalidate AI cache so the next insights refresh picks up the notes
            try:
                _generate_ai_insights.clear()
                _generate_pod_insights.clear()
            except Exception:
                pass
            st.success("Saved. The next AI insights refresh will use these notes.")
        else:
            st.error("Could not write the notes file. Check folder permissions.")
    if cols[1].button("Discard changes", key=f"notes_reset_{pod_key}"):
        st.session_state[state_key] = current
        st.rerun()

    if current:
        st.caption(f"Last saved file: `meeting_notes/{_notes_filename(pod_key).name}` "
                   f"({len(current)} chars).")


def _previous_month_range(start_date: date, end_date: date) -> tuple:
    """Approximate "previous period" — same length, immediately before start."""
    span = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start, prev_end


def _gather_snapshot_for_ai(start_date: date, end_date: date) -> str:
    """Rich JSON for Claude: per-pod current + previous period, AOP context,
    Coverage signals, and the most recent overall meeting notes."""
    today = date.today()
    prev_start, prev_end = _previous_month_range(start_date, end_date)

    summary = {
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "previous_period_for_comparison": f"{prev_start.isoformat()} to {prev_end.isoformat()}",
        "today": today.isoformat(),
        "fy27_progress_pct": round(
            ((min(today, FY27_END) - FY27_START).days + 1)
            / ((FY27_END - FY27_START).days + 1) * 100, 1
        ) if today >= FY27_START else 0.0,
        "pods": {},
    }

    for pod in PODS:
        df = prepare_pod_df(pod, start_date, end_date)
        prev_df = prepare_pod_df(pod, prev_start, prev_end)
        if df is None:
            summary["pods"][pod_display(pod)] = {"status": "no snapshot"}
            continue
        df_in = df[df["in_range"]]
        prev_in = prev_df[prev_df["in_range"]] if prev_df is not None else pd.DataFrame()

        has_upload = bool(PODS[pod]["upload_cols"])
        final_status = "Live" if has_upload else "Delivered"

        in_editing_sample = []
        for _, row in df_in[df_in["status"] == "In Editing"].head(4).iterrows():
            d = row["data"]
            in_editing_sample.append({
                "video": (d.get("Video Name") or "").strip()[:60],
                "edit_start": d.get("Edit Start Date", ""),
                "planned_delivery": d.get("Planned Date of Delivery", ""),
            })

        # AOP context for this pod
        aop_targets = POD_AOP_TARGETS.get(pod, [])
        aop_context = []
        if today >= FY27_START:
            fy_progress = (
                ((min(today, FY27_END) - FY27_START).days + 1)
                / ((FY27_END - FY27_START).days + 1)
            )
            for t in aop_targets:
                if isinstance(t["annual_target"], int):
                    actual = _count_pod_deliveries_in_fy(
                        pod, FY27_START, FY27_END, t.get("count_filter")
                    )
                    expected = t["annual_target"] * fy_progress
                    aop_context.append({
                        "deliverable": t["deliverable"],
                        "annual_target": t["annual_target"],
                        "actual_so_far": actual,
                        "expected_at_this_point": round(expected, 1),
                        "delta_vs_pace": actual - round(expected, 1),
                    })

        summary["pods"][pod_display(pod)] = {
            "lead": pod_lead(pod),
            "active_in_range": int(len(df_in)),
            "status_counts": {k: int(v) for k, v in df_in["status"].value_counts().to_dict().items()},
            "shipped_in_range": int((df_in["status"] == final_status).sum()),
            "shipped_previous_period": int((prev_in["status"] == final_status).sum()) if not prev_in.empty else 0,
            "in_editing_sample": in_editing_sample,
            "aop_targets": aop_context,
        }

    # Coverage signals
    try:
        cov = load_coverage_snapshot()
        if not cov.empty:
            cov_today = sum(
                1 for _, r in cov.iterrows()
                if parse_date(r["data"].get("Date")) == today
            )
            cov_next7 = sum(
                1 for _, r in cov.iterrows()
                if parse_date(r["data"].get("Date"))
                and today <= parse_date(r["data"].get("Date")) <= today + timedelta(days=7)
            )
            summary["coverage"] = {
                "shoots_today": cov_today,
                "shoots_next_7_days": cov_next7,
            }
    except Exception:
        pass

    notes = _load_meeting_notes("_overall")
    if notes:
        summary["meeting_notes_overall"] = notes

    return json.dumps(summary, indent=2, default=str)


LEADERSHIP_SYSTEM_PROMPT = (
    "You are the chief-of-staff to Divyam Goenka, AD-Brand at Masters' Union "
    "Creative Studio. Divyam is leadership; he reads dashboards in 30 seconds "
    "and acts. Your job is to make him remember EVERYTHING the moment he reads "
    "your insight, ask the right question of his team, and spend his energy on "
    "the few things that matter this week.\n\n"
    "Your output must be:\n"
    "- **Punchy.** Short sentences. No filler. No 'it appears that' or 'we see'.\n"
    "- **Specific.** Always reference the pod and the number.\n"
    "- **Decision-ready.** Every line should be either a celebration, a risk, "
    "or a prompt to act.\n"
    "- **Markdown bullets.** Each bullet starts with '-' and is one sentence "
    "of 10-20 words.\n"
    "- **AOP-aware.** When the data has `aop_targets`, compare actuals against "
    "the annual target's expected pace. Call out misses by name and number "
    "(e.g. 'Performance Ads is 7 behind pace; need 12 in May to catch up').\n"
    "- **Memory of last period.** When `shipped_previous_period` is provided, "
    "compare and call out trend changes ('Builders.mu shipped 4, down from "
    "7 last period — what changed?').\n"
    "- **Meeting-notes aware.** When `meeting_notes_*` is provided, weave "
    "decisions and commitments from those notes into wins/risks/today. If a "
    "decision from the notes is not reflected in the data, FLAG it.\n"
    "- **British English.** No em dashes. No corporate jargon "
    "('synergy', 'leverage', 'circle back', 'bandwidth').\n"
    "- **Pod naming.** Always 'Builders.mu', 'Bharat.mu', 'Brand/Ad Films', "
    "'Performance Ads' (not 'Perf Ads'), and so on. Never 'MU' for the org."
)


@st.cache_data(ttl=3600, show_spinner="Generating leadership insights...")
def _generate_ai_insights(snapshot_json: str) -> dict:
    """Cached LLM call. Cache key is the snapshot JSON, so identical state
    does not re-spend tokens. Returns four markdown-bullet sections."""
    import anthropic
    import re

    client = anthropic.Anthropic()

    user_prompt = (
        "Current state of the Creative Studio (JSON):\n\n"
        f"{snapshot_json}\n\n"
        "Produce four sections, each a short markdown bullet list (2-4 bullets):\n\n"
        "1. **wins** - What is working right now. Things that went live, "
        "pods hitting their stride, deliveries landing on time.\n\n"
        "2. **risks** - What is at risk. Items overdue, pods quiet, edit "
        "pipelines backing up, planned-vs-actual slippage.\n\n"
        "3. **today** - What needs Divyam's attention TODAY. Coverage shoots "
        "happening, meetings to push for, decisions to make.\n\n"
        "4. **note** - One short paragraph (40-60 words) Divyam can drop "
        "into a slack message to the team or read aloud in a leadership meeting.\n\n"
        "Return raw JSON with keys: wins, risks, today, note.\n"
        "wins/risks/today values are markdown bullet strings (each bullet "
        "starting with '- ' on its own line).\n"
        "note value is a single string (no bullets).\n"
        "No commentary, no markdown fences around the JSON, just the object."
    )

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=LEADERSHIP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {
            "wins": "- (Could not parse Claude's response.)",
            "risks": text[:300],
            "today": "",
            "note": "",
        }


@st.cache_data(ttl=3600, show_spinner="Generating pod insights...")
def _generate_pod_insights(pod_name: str, snapshot_json: str) -> dict:
    """Per-pod AI insights. Smaller, sharper, focused on one pod."""
    import anthropic
    import re

    client = anthropic.Anthropic()

    user_prompt = (
        f"Current state of the **{pod_name}** pod (JSON):\n\n"
        f"{snapshot_json}\n\n"
        "Produce three sections, each a short markdown bullet list:\n\n"
        "1. **wins** (1-3 bullets) - What this pod has shipped or is shipping well.\n\n"
        "2. **risks** (1-3 bullets) - What is slipping, stuck, or missing in this pod.\n\n"
        "3. **action** (1-2 bullets) - The next concrete thing the pod lead should do.\n\n"
        "Each bullet starts with '- ' on its own line.\n"
        "If the pod has very little data, say so honestly in one bullet.\n"
        "Return raw JSON with keys: wins, risks, action. Values are markdown bullet strings.\n"
        "No commentary, no fences, just the JSON object."
    )

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1200,
        system=LEADERSHIP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {
            "wins": "- (Could not parse Claude's response.)",
            "risks": text[:300],
            "action": "",
        }


def _render_insight_card(title: str, body_md: str, accent: str = MU_CYAN):
    """Card with a coloured title bar and markdown-rendered body so bullets
    look like proper bullets, not literal '-' characters."""
    body_html = body_md.replace("\n", "<br/>") if body_md else "(no insight)"
    # Convert simple markdown bullets to HTML list items for nicer rendering
    if body_md and "- " in body_md:
        items = []
        for line in body_md.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                items.append(f"<li>{line[2:].strip()}</li>")
            elif line:
                items.append(f"<li>{line}</li>")
        if items:
            body_html = f"<ul style='margin:0;padding-left:1.1rem;'>{''.join(items)}</ul>"

    st.markdown(f"""
        <div class="mu-pod-card" style="position:relative;">
            <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.65rem;">
                <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{accent};"></span>
                <span style="font-family:'DM Sans',sans-serif;font-weight:700;font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;color:{MU_BLACK};">
                    {title}
                </span>
            </div>
            <div style="font-family:'DM Sans',sans-serif;font-size:0.92rem;line-height:1.55;color:{MU_BLACK};">
                {body_html}
            </div>
        </div>
    """, unsafe_allow_html=True)


def render_ai_insights_placeholder(start_date: Optional[date] = None,
                                    end_date: Optional[date] = None):
    """Leadership-grade AI insights — now button-triggered so opening the tab
    does not block on a 5-15s Claude call. Result persists in session state
    until the user clicks Refresh or changes the date range."""
    if start_date is None or end_date is None:
        rv = st.session_state.get("range_value")
        if rv:
            start_date, end_date = rv
        else:
            today = date.today()
            start_date, end_date = date(today.year, today.month, 1), today

    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if not api_key_present:
        st.warning(
            "**AI insights are dormant.** Add `ANTHROPIC_API_KEY=...` to `.env` "
            "and restart Streamlit. Cards below will then populate live."
        )
        for title, accent in [("Wins", "#10B981"), ("Risks", MU_ORANGE),
                              ("Today's priorities", MU_YELLOW),
                              ("Strategic note", MU_CYAN)]:
            _render_insight_card(title, "_Awaiting API key_", accent=accent)
        return

    # Cache the last-generated insights in session_state so flipping back
    # to this tab does not re-trigger the Claude call.
    range_key = f"{start_date.isoformat()}_{end_date.isoformat()}"
    cache_key = f"overview_ai_insights_{range_key}"
    last_run_key = f"overview_ai_insights_run_at_{range_key}"

    cols = st.columns([1, 1, 4])
    generate = cols[0].button(
        "✨ Generate insights",
        type="primary",
        key=f"gen_overview_{range_key}",
    )
    if cache_key in st.session_state:
        if cols[1].button("Refresh", key=f"refresh_overview_{range_key}"):
            del st.session_state[cache_key]
            try:
                _generate_ai_insights.clear()
            except Exception:
                pass
            generate = True
        cols[2].caption(
            f"Last generated: {st.session_state.get(last_run_key, 'unknown')} · "
            f"range {format_range(start_date, end_date)}"
        )

    if generate:
        with st.spinner("Asking Claude (5-10 seconds)..."):
            try:
                snapshot_json = _gather_snapshot_for_ai(start_date, end_date)
                insights = _generate_ai_insights(snapshot_json)
                st.session_state[cache_key] = insights
                st.session_state[last_run_key] = datetime.now().strftime("%H:%M:%S")
            except Exception as e:
                st.error(
                    f"Could not reach Claude. Error: {type(e).__name__}: {e}"
                )
                return

    insights = st.session_state.get(cache_key)
    if not insights:
        st.info(
            "Click **✨ Generate insights** to ask Claude for a leadership "
            "summary. Result stays cached on this page until you click Refresh."
        )
        return

    wins = insights.get("wins", "")
    risks = insights.get("risks", "")
    today_md = insights.get("today", "")
    note = insights.get("note", "")

    c1, c2 = st.columns(2, gap="medium")
    with c1:
        _render_insight_card("Wins", wins, accent="#10B981")
    with c2:
        _render_insight_card("Risks", risks, accent=MU_ORANGE)
    c3, c4 = st.columns(2, gap="medium")
    with c3:
        _render_insight_card("Today's priorities", today_md, accent=MU_YELLOW)
    with c4:
        _render_insight_card("Strategic note", note, accent=MU_CYAN)


def render_pod_ai_insights(pod_name: str, start_date: date, end_date: date):
    """Per-pod AI insights — wins, risks, action. Now AOP-aware, period-aware,
    and meeting-notes-aware so insights actually move Divyam's decisions."""
    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key_present:
        return

    df = prepare_pod_df(pod_name, start_date, end_date)
    if df is None:
        return
    df_in = df[df["in_range"]]

    # Previous period
    prev_start, prev_end = _previous_month_range(start_date, end_date)
    prev_df = prepare_pod_df(pod_name, prev_start, prev_end)
    prev_in = prev_df[prev_df["in_range"]] if prev_df is not None else pd.DataFrame()

    has_upload = bool(PODS.get(pod_name, {}).get("upload_cols"))
    final_status = "Live" if has_upload else "Delivered"

    # In-editing sample
    sample_in_editing = []
    for _, row in df_in[df_in["status"] == "In Editing"].head(8).iterrows():
        d = row["data"]
        sample_in_editing.append({
            "video": (d.get("Video Name") or "").strip()[:60],
            "edit_start": d.get("Edit Start Date", ""),
            "planned_delivery": d.get("Planned Date of Delivery", ""),
        })

    # AOP context
    today = date.today()
    aop_targets = POD_AOP_TARGETS.get(pod_name, [])
    aop_context = []
    if today >= FY27_START:
        fy_progress = (
            ((min(today, FY27_END) - FY27_START).days + 1)
            / ((FY27_END - FY27_START).days + 1)
        )
        for t in aop_targets:
            if isinstance(t["annual_target"], int):
                actual = _count_pod_deliveries_in_fy(
                    pod_name, FY27_START, FY27_END, t.get("count_filter")
                )
                expected = t["annual_target"] * fy_progress
                aop_context.append({
                    "deliverable": t["deliverable"],
                    "annual_target": t["annual_target"],
                    "actual_so_far": actual,
                    "expected_at_this_point": round(expected, 1),
                    "behind_by": round(expected - actual, 1) if actual < expected else 0,
                })

    # Meeting notes specific to this pod
    notes = _load_meeting_notes(pod_name)

    pod_payload = json.dumps({
        "pod": pod_display(pod_name),
        "lead": pod_lead(pod_name),
        "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        "previous_period": f"{prev_start.isoformat()} to {prev_end.isoformat()}",
        "active_in_range": int(len(df_in)),
        "status_counts": {k: int(v) for k, v in df_in["status"].value_counts().to_dict().items()},
        "shipped_in_range": int((df_in["status"] == final_status).sum()),
        "shipped_previous_period": int((prev_in["status"] == final_status).sum()) if not prev_in.empty else 0,
        "in_editing_sample": sample_in_editing,
        "aop_targets": aop_context,
        "meeting_notes_for_this_pod": notes if notes else None,
    }, indent=2, default=str)

    # Button-triggered to avoid blocking pod page load on a 5-10s Claude call.
    range_key = f"{start_date.isoformat()}_{end_date.isoformat()}"
    cache_key = f"pod_ai_{pod_name}_{range_key}"

    st.markdown("#### AI insights")
    btn_cols = st.columns([1, 1, 4])
    generate = btn_cols[0].button(
        "✨ Generate", type="primary", key=f"gen_pod_{pod_name}_{range_key}",
    )
    if cache_key in st.session_state:
        if btn_cols[1].button("Refresh", key=f"refresh_pod_{pod_name}_{range_key}"):
            del st.session_state[cache_key]
            try:
                _generate_pod_insights.clear()
            except Exception:
                pass
            generate = True

    if generate:
        with st.spinner("Asking Claude..."):
            try:
                st.session_state[cache_key] = _generate_pod_insights(
                    pod_display(pod_name), pod_payload
                )
            except Exception as e:
                st.warning(f"Pod-level AI insight failed: {type(e).__name__}: {e}")
                st.markdown("---")
                return

    insights = st.session_state.get(cache_key)
    if insights:
        c1, c2, c3 = st.columns(3, gap="medium")
        with c1:
            _render_insight_card("Wins", insights.get("wins", ""), accent="#10B981")
        with c2:
            _render_insight_card("Risks", insights.get("risks", ""), accent=MU_ORANGE)
        with c3:
            _render_insight_card("Action", insights.get("action", ""), accent=MU_CYAN)
    else:
        st.caption(
            "Click ✨ Generate to ask Claude for pod-specific Wins / Risks / Action. "
            "Result is cached on this page; click Refresh for a new call."
        )
    st.markdown("---")


def render_sheet_library():
    """Compact table of every sheet Divyam is likely to open."""
    if not SHEET_LIBRARY:
        st.info("No sheets configured in the library yet.")
        return
    df = pd.DataFrame(SHEET_LIBRARY)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Open", display_text="Open ↗"),
        },
    )
    st.caption(
        "Add or edit sheets in the `SHEET_LIBRARY` list at the top of "
        "`dashboard.py`. One line per sheet."
    )


def render_overview(start_date: date, end_date: date):
    """Divyam's always-on view. Pulls cross-pod activity, upcoming shoots,
    AI placeholders, and the sheet library together on one page."""
    range_label = format_range(start_date, end_date)
    st.markdown(f"### Where the studio is right now")
    st.caption(f"Range: {range_label}")

    # Aggregate metrics
    totals = _aggregate_pod_metrics(start_date, end_date)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active across pods", totals["active"])
    c2.metric("Live / Delivered", totals["live"])
    c3.metric("Delivered, pending upload", totals["delivered_pending"])
    c4.metric("In editing", totals["in_editing"])
    c5.metric("Shot, awaiting edit", totals["shot_awaiting"])

    # Inner sections as tabs — Costing first since that's what Pratham/Divyam open with.
    (sec_costing, sec_activity, sec_shoots, sec_ai, sec_aop, sec_notes,
     sec_sheets, sec_pods) = st.tabs([
        "Costing",
        "What's happening now",
        "Upcoming shoots",
        "AI insights",
        "AOP attainment",
        "Notes",
        "Sheet library",
        "Pod cards",
    ])

    with sec_costing:
        st.caption(
            "Total spend across all production pods for the selected range. "
            "Salary share + content expenses, broken down per pod and per month."
        )
        render_overview_costing(start_date, end_date)

    with sec_activity:
        st.caption(
            "Every video currently in progress across the production pods, "
            "rolled into one list."
        )
        render_cross_pod_activity(start_date, end_date, totals)

    with sec_shoots:
        st.caption(
            "Next 14 days of Coverage shoots, on a calendar. "
            "Events coloured by requesting department."
        )
        try:
            render_upcoming_shoots_overview()
        except Exception as e:
            import traceback
            st.error(f"Upcoming-shoots calendar failed: {type(e).__name__}: {e}")
            st.code(traceback.format_exc())

    with sec_ai:
        render_ai_insights_placeholder(start_date, end_date)

    with sec_aop:
        st.caption(
            "All deliverables from the FY27 Annual Operating Plan, grouped by sub-division. "
            "Per-pod progress bars live inside each pod's AOP tab."
        )
        render_overview_aop()

    with sec_notes:
        render_notes_editor("_overall", "Overall (cross-pod)")

    with sec_sheets:
        render_sheet_library()

    with sec_pods:
        st.caption(
            "Glance at every production pod's pipeline shape. "
            "To drill in, switch to the corresponding sub-department tab."
        )
        # Reuse the existing overall summary (cards grid)
        render_overall_summary(start_date, end_date)

    # Footer: totals
    st.markdown('<div class="mu-gradient-strip"></div>', unsafe_allow_html=True)
    f1, f2 = st.columns(2)
    f1.metric(
        "Total content (Fiction + Non-Fiction) this range",
        totals["live"] + totals["delivered_pending"],
        help="Counts items that reached Live or Delivered, Awaiting Upload "
             "in the selected range."
    )
    f2.metric(
        "Total spend this range",
        "Coming soon",
        help="Spend tracking will switch on once the financial sheet is wired in."
    )


def main():
    # Favicon prefers the small scribble icon when present; falls back to the
    # full logo otherwise. To use the scribble, save the image MU sent
    # (just the brushstroke mark, no text) as
    # assets/masters_union_scribble.png — no code change needed.
    favicon_path = SCRIBBLE_PATH if SCRIBBLE_PATH.exists() else (
        LOGO_PATH if LOGO_PATH.exists() else None
    )
    st.set_page_config(
        page_title="Masters' Union — Creative Studio Operations",
        page_icon=str(favicon_path) if favicon_path else None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    # Pre-warm the data caches so the first tab feels instant.
    warm_caches()

    render_header()

    # Top-level navigation: Overview + every sub-department as a tab.
    top_tab_labels = ["Overview"] + list(ORG.keys())
    top_tabs = st.tabs(top_tab_labels)

    with top_tabs[0]:
        start_date, end_date = select_date_range(scope="overview")
        render_overview(start_date, end_date)

    for i, sd in enumerate(ORG.keys(), start=1):
        with top_tabs[i]:
            sd_scope = sd.lower().replace(" ", "_").replace("&", "and")
            start_date, end_date = select_date_range(scope=sd_scope)
            render_subdept(sd, start_date, end_date)

    st.markdown(
        '<div class="mu-gradient-strip"></div>'
        '<div class="mu-footer-text">Masters\' Union · Creative Studio</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

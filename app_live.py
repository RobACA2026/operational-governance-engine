import json
import os
import time
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from governance_engine import GovernanceEngine


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    layout="wide",
    page_title="Operational Governance Portal",
)


COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
)

DEFAULT_PRICE = 65000.0
REFRESH_INTERVAL_MS = 30000
HISTORY_LIMIT = 120


# ============================================================
# 2. PAGE STYLING
# ============================================================

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        background-color: #0f172a;
        color: #f8fafc;
    }

    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stRadio p,
    [data-testid="stSidebar"] .stCheckbox span,
    [data-testid="stSidebar"] .stSubheader {
        color: #f8fafc !important;
    }

    div.stButton > button:first-child {
        background-color: #2563eb;
        color: white;
        font-weight: 600;
        border-radius: 6px;
        border: none;
        transition: background-color 0.2s ease;
    }

    div.stButton > button:first-child:hover {
        background-color: #1d4ed8;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 3. UTILITY FUNCTIONS
# ============================================================

def utc_timestamp():
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def log_admin_event(event_type, details):
    entry = {
        "timestamp": utc_timestamp(),
        "event_type": event_type,
        "details": details,
        "actor": (
            "Administrator"
            if st.session_state.admin_authenticated
            else "Operator"
        ),
    }

    with open(
        "compliance_audit.jsonl",
        "a",
        encoding="utf-8",
    ) as file:
        file.write(json.dumps(entry, sort_keys=True) + "\n")


def append_order_history(
    action,
    price,
    status,
    reason,
    warnings=None,
):
    st.session_state.order_history.append(
        {
            "Timestamp": utc_timestamp(),
            "Action": action,
            "Price": float(price),
            "Status": status,
            "Reason": reason,
            "Warnings": warnings or [],
        }
    )

    if len(st.session_state.order_history) > HISTORY_LIMIT:
        st.session_state.order_history = (
            st.session_state.order_history[-HISTORY_LIMIT:]
        )


def append_governance_history(
    price,
    rademacher,
    dissipativity,
    action=None,
    reason=None,
    status=None,
):
    st.session_state.governance_history.append(
        {
            "Timestamp": utc_timestamp(),
            "Price": float(price),
            "Rademacher": float(rademacher),
            "Dissipativity": float(dissipativity),
            "Action": action,
            "Reason": reason,
            "Status": status,
        }
    )

    if len(st.session_state.governance_history) > HISTORY_LIMIT:
        st.session_state.governance_history = (
            st.session_state.governance_history[-HISTORY_LIMIT:]
        )


def read_jsonl(path):
    if not os.path.exists(path):
        return []

    rows = []

    try:
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue

                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    return rows


# ============================================================
# 4. LIVE COINGECKO PRICE
# ============================================================

@st.cache_data(ttl=5)
def fetch_coingecko_btc_price():
    try:
        response = requests.get(
            COINGECKO_PRICE_URL,
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            timeout=5,
        )

        response.raise_for_status()
        data = response.json()
        
        price = float(data["bitcoin"]["usd"])

        if price <= 0:
            return None

        return price

    except (
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
    ):
        return None

# ============================================================
# 5. SESSION STATE INITIALIZATION
# ============================================================

if "engine" not in st.session_state:
    st.session_state.engine = GovernanceEngine(
        csv_path="btc-usd-max.csv",
        rademacher_limit=0.05,
        dissipativity_soft_limit=30.0,
        dissipativity_alpha=0.05,
        friction_bps=5.0,
        max_buy_price=68000.0,
        buy_cash_pct=20.0,
        min_sell_price=62000.0,
        sell_pct=20.0,
        buy_active=True,
        sell_active=True,
        initial_cash=80000.0,
        initial_btc=0.3077,
    )

if "current_price" not in st.session_state:
    st.session_state.current_price = DEFAULT_PRICE

if "last_successful_price" not in st.session_state:
    st.session_state.last_successful_price = DEFAULT_PRICE

if "price_tick_history" not in st.session_state:
    st.session_state.price_tick_history = []

if "agent_active" not in st.session_state:
    st.session_state.agent_active = False

if "equity_history" not in st.session_state:
    st.session_state.equity_history = []

if "governance_history" not in st.session_state:
    st.session_state.governance_history = []

if "order_history" not in st.session_state:
    st.session_state.order_history = []

if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False

if "portfolio_cash" not in st.session_state:
    st.session_state.portfolio_cash = 80000.0

if "portfolio_btc" not in st.session_state:
    st.session_state.portfolio_btc = 0.3077

if "last_tick_timestamp" not in st.session_state:
    st.session_state.last_tick_timestamp = 0.0

if "last_live_error" not in st.session_state:
    st.session_state.last_live_error = None

if "compliance_frameworks" not in st.session_state:
    st.session_state.compliance_frameworks = {
        "EU AI Act": {
            "applicability": "Mandatory",
            "status": "Compliant",
            "details": (
                "High-risk financial operations "
                "and automated trading"
            ),
        },
        "NIST AI RMF": {
            "applicability": "Recommended",
            "status": "Active",
            "details": (
                "Govern, Map, Measure, "
                "and Manage functions"
            ),
        },
        "UK Framework": {
            "applicability": "Voluntary",
            "status": "Aligned",
            "details": (
                "Safety, transparency, "
                "and accountability standards"
            ),
        },
        "ISO/IEC 42001": {
            "applicability": "Global",
            "status": "Certified",
            "details": (
                "Artificial intelligence "
                "management systems"
            ),
        },
    }


engine = st.session_state.engine


# ============================================================
# 6. REFRESH CONTROL
# ============================================================

refresh_count = st_autorefresh(
    interval=REFRESH_INTERVAL_MS,
    limit=None,
    key="portal_refresh_counter",
)


# ============================================================
# 7. SIDEBAR: AUTHENTICATION
# ============================================================

st.sidebar.title("Operational Governance Control")

if st.sidebar.button(
    "Refresh Metrics",
    key="sidebar_manual_refresh",
):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader(
    "1. Authentication and Market Price"
)

user_role = st.sidebar.selectbox(
    "User Role",
    ["Operator", "Administrator"],
    key="auth_user_role",
)

if user_role == "Administrator":
    if not st.session_state.admin_authenticated:
        try:
            admin_password = st.secrets.get("ADMIN_PASSWORD", "Bolivia")
        except Exception:
            admin_password = "Bolivia"

        admin_pass_input = st.sidebar.text_input(
            "Enter Admin Password",
            type="password",
            key="sidebar_admin_pwd",
        )

        if st.sidebar.button("Verify Admin Password"):
            if admin_password and admin_pass_input == admin_password:
                st.session_state.admin_authenticated = True
                log_admin_event(
                    "ADMIN_AUTH_SUCCESS",
                    "Administrator session unlocked.",
                )
                st.sidebar.success(
                    "Administrator unlocked."
                )
                st.rerun()
            else:
                st.sidebar.error(
                    "Administrator authentication failed."
                )

        is_admin_active = False

    else:
        st.sidebar.success("Administrator active.")

        if st.sidebar.button(
            "Lock Admin Session",
            key="lock_admin_session",
        ):
            st.session_state.admin_authenticated = False
            log_admin_event(
                "ADMIN_AUTH_LOCK",
                "Administrator session locked.",
            )
            st.rerun()

        is_admin_active = True

else:
    is_admin_active = False


# ============================================================
# 8. SIDEBAR: PRICE SOURCE
# ============================================================

price_source = st.sidebar.radio(
    "Price Source",
    ["Manual Override", "CoinGecko API"],
    key="price_source_radio",
)

if price_source == "CoinGecko API":
    live_price = fetch_coingecko_btc_price()

    if live_price is not None:
        st.session_state.current_price = live_price
        st.session_state.last_successful_price = live_price
        st.session_state.last_live_error = None
        st.sidebar.success("Synced with CoinGecko.")
    else:
        st.session_state.last_live_error = (
            "CoinGecko price unavailable."
        )
        st.sidebar.warning(
            "CoinGecko unavailable. "
            "Using the last successful price."
        )

    current_price = float(
        st.session_state.last_successful_price
    )

    st.sidebar.number_input(
        "Current Market Price ($)",
        value=current_price,
        step=10.0,
        format="%.2f",
        disabled=True,
        key="coingecko_price_display",
    )

else:
    manual_price = st.sidebar.number_input(
        "Current Market Price ($)",
        value=float(st.session_state.current_price),
        min_value=0.01,
        step=10.0,
        format="%.2f",
        disabled=not is_admin_active,
        key="manual_market_price",
    )

    if is_admin_active:
        st.session_state.current_price = manual_price

    current_price = float(
        st.session_state.current_price
    )


if (
    not st.session_state.price_tick_history
    or st.session_state.price_tick_history[-1]
    != current_price
):
    st.session_state.price_tick_history.append(
        current_price
    )

    if (
        len(st.session_state.price_tick_history)
        > HISTORY_LIMIT
    ):
        st.session_state.price_tick_history.pop(0)


# ============================================================
# 9. SIDEBAR: UPSTREAM OPERATIONAL LIMITS
# ============================================================

st.sidebar.markdown("---")
st.sidebar.subheader(
    "2. Upstream Agent Boundary Limits"
)

st.sidebar.markdown("**Buy Boundaries**")

new_max_buy = st.sidebar.number_input(
    "Max Buy Price ($)",
    value=float(engine.max_buy_price),
    min_value=0.01,
    step=100.0,
    key="limit_max_buy",
    disabled=not is_admin_active,
)

new_buy_cash_pct = st.sidebar.slider(
    "Buy Cash Allocation (%)",
    min_value=1.0,
    max_value=100.0,
    value=float(engine.buy_cash_pct),
    step=1.0,
    key="limit_buy_cash_pct",
    disabled=not is_admin_active,
)

buy_active = st.sidebar.checkbox(
    "Enable Automated Buy",
    value=engine.buy_active,
    key="limit_buy_active",
    disabled=not is_admin_active,
)

st.sidebar.markdown("**Sell Boundaries**")

new_min_sell = st.sidebar.number_input(
    "Min Sell Floor ($)",
    value=float(engine.min_sell_price),
    min_value=0.01,
    step=100.0,
    key="limit_min_sell",
    disabled=not is_admin_active,
)

new_sell_pct = st.sidebar.slider(
    "Sell Percentage (%)",
    min_value=1.0,
    max_value=100.0,
    value=float(engine.sell_pct),
    step=1.0,
    key="limit_sell_pct",
    disabled=not is_admin_active,
)

sell_active = st.sidebar.checkbox(
    "Enable Automated Sell",
    value=engine.sell_active,
    key="limit_sell_active",
    disabled=not is_admin_active,
)

if st.sidebar.button(
    "Update Boundary Limits",
    key="update_limits_btn",
):
    if not is_admin_active:
        st.sidebar.error(
            "Administrator access required."
        )
    else:
        engine.update_limits(
            {
                "max_buy_price": new_max_buy,
                "buy_cash_pct": new_buy_cash_pct,
                "min_sell_price": new_min_sell,
                "sell_pct": new_sell_pct,
                "buy_active": buy_active,
                "sell_active": sell_active,
            }
        )

        log_admin_event(
            "BOUNDARY_LIMIT_UPDATE",
            (
                f"Max Buy: ${new_max_buy:,.2f}; "
                f"Buy Allocation: {new_buy_cash_pct}%; "
                f"Min Sell: ${new_min_sell:,.2f}; "
                f"Sell Allocation: {new_sell_pct}%"
            ),
        )

        st.sidebar.success(
            "Operational boundaries updated."
        )
        st.rerun()


# ============================================================
# 10. SIDEBAR: SOFT-CONTROL LIMITS
# ============================================================

st.sidebar.markdown("---")
st.sidebar.subheader(
    "3. Theory-First Soft Controls"
)

rademacher_limit = st.sidebar.slider(
    "Rademacher Soft Limit",
    min_value=0.0001,
    max_value=1.0,
    value=float(engine.rademacher_limit),
    step=0.0001,
    format="%.4f",
    disabled=not is_admin_active,
    key="rademacher_limit_slider",
)

dissipativity_threshold = st.sidebar.slider(
    "Dissipativity Soft Limit V(x)",
    min_value=0.1,
    max_value=100.0,
    value=float(engine.dissipativity.soft_limit),
    step=0.1,
    disabled=not is_admin_active,
    key="dissipativity_slider",
)

if st.sidebar.button(
    "Update Soft Control Limits",
    key="update_soft_limits_btn",
):
    if not is_admin_active:
        st.sidebar.error(
            "Administrator access required."
        )
    else:
        engine.set_soft_limits(
            rademacher_limit=rademacher_limit,
            dissipativity_soft_limit=(
                dissipativity_threshold
            ),
        )

        engine.append_audit_record(
            event_type="SOFT_CONTROL_LIMIT_UPDATE",
            actor="Administrator",
            payload={
                "rademacher_limit": rademacher_limit,
                "dissipativity_soft_limit": (
                    dissipativity_threshold
                ),
            },
        )

        st.sidebar.success(
            "Soft-control limits updated."
        )
        st.rerun()


# ============================================================
# 11. SIDEBAR: HISTORICAL REPLAY
# ============================================================

st.sidebar.markdown("---")
st.sidebar.subheader(
    "4. Historical Replay Stress Test"
)

stress_profile = st.sidebar.selectbox(
    "Stress Profile",
    [
        "Nominal",
        "High Volatility",
        "Liquidity Crunch",
        "Adverse Drawdown",
    ],
    key="stress_profile_select",
)

time_horizon = st.sidebar.selectbox(
    "Data Time Horizon",
    [
        "1 Day",
        "1 Week",
        "1 Month",
        "3 Months",
        "3 Years",
    ],
    index=4,
    key="time_horizon_select",
)

if st.sidebar.button(
    "Run Three-Year Replay",
    key="run_stress_test_btn",
):
    try:
        replay_result = engine.run_step_2_replay(
            profile=stress_profile,
            duration=time_horizon,
        )

        replay_rad = replay_result["rademacher"]

        engine.append_audit_record(
            event_type="HISTORICAL_REPLAY_EXECUTION",
            actor=(
                "Administrator"
                if is_admin_active
                else "Operator"
            ),
            payload={
                "profile": stress_profile,
                "duration": time_horizon,
                "result": replay_result,
            },
        )

        st.sidebar.success(
            "Historical replay completed."
        )

        st.sidebar.caption(
            f"Rademacher: "
            f"{replay_rad['estimate']:.6f}"
        )
        st.sidebar.caption(
            f"Observations: "
            f"{replay_rad['num_observations']}"
        )
        st.sidebar.caption(
            f"Hypotheses: "
            f"{replay_rad['num_hypotheses']}"
        )
        st.sidebar.caption(
            f"Simulations: "
            f"{replay_rad['num_simulations']}"
        )

    except Exception as exc:
        st.sidebar.error(
            f"Replay failed: {exc}"
        )


# ============================================================
# 12. LIVE STATE AND METRIC CALCULATION
# ============================================================

engine.cash = st.session_state.portfolio_cash
engine.btc_balance = st.session_state.portfolio_btc

live_dissipativity = (
    engine.update_live_dissipativity(
        current_price=current_price,
        proposed_order_qty=0.0,
    )
)

safe_metrics = engine.current_safe_metrics()

rad_val = safe_metrics["rademacher"]["estimate"]
rad_limit = safe_metrics["rademacher"]["limit"]
rad_standard_error = (
    safe_metrics["rademacher"]["standard_error"]
)

dis_val = live_dissipativity["V_x"]
dis_limit = live_dissipativity["soft_limit"]
dis_warning_limit = live_dissipativity["warning_limit"]


# ============================================================
# 13. PAGE HEADER AND TOP METRICS
# ============================================================

st.title("Operational Governance Portal")

st.markdown(
    "Multi-plane compliance engine combining upstream control "
    "bounds, core runtime execution, and side-band observability."
)

if st.session_state.last_live_error:
    st.warning(
        st.session_state.last_live_error
    )

top_col1, top_col2, top_col3, top_col4 = st.columns(4)

with top_col1:
    st.metric(
        "Current BTC Market Price",
        f"${current_price:,.2f}",
    )

with top_col2:
    total_portfolio_value = (
        st.session_state.portfolio_cash
        + (
            st.session_state.portfolio_btc
            * current_price
        )
    )

    st.metric(
        "Total Portfolio Value",
        f"${total_portfolio_value:,.2f}",
    )

with top_col3:
    st.metric(
        "Active BTC Position",
        f"{st.session_state.portfolio_btc:.4f} BTC",
    )

with top_col4:
    var_estimate = total_portfolio_value * 0.045

    st.metric(
        "Estimated 1D VaR (95%)",
        f"${var_estimate:,.2f}",
    )


# ============================================================
# 14. AGENT STATUS
# ============================================================

current_time_str = utc_timestamp()

if not st.session_state.agent_active:
    st.markdown(
        """
        <div style="
            padding: 16px;
            background-color: #f8d7da;
            border: 2px solid #dc3545;
            border-radius: 6px;
            text-align: center;
            margin-bottom: 16px;
        ">
            <h2 style="
                color: #721c24;
                margin: 0;
                font-size: 1.5rem;
            ">
                AGENT CURRENTLY INACTIVE
            </h2>
            <p style="
                color: #721c24;
                margin: 4px 0 0 0;
                font-size: 0.95rem;
            ">
                Runtime execution engine is idle.
                Click run to start monitoring.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div style="
            padding: 16px;
            background-color: #d4edda;
            border: 2px solid #28a745;
            border-radius: 6px;
            text-align: center;
            margin-bottom: 16px;
        ">
            <h2 style="
                color: #155724;
                margin: 0;
                font-size: 1.5rem;
            ">
                AGENT CURRENTLY ACTIVE
            </h2>
            <p style="
                color: #155724;
                margin: 4px 0 0 0;
                font-size: 0.95rem;
            ">
                Runtime execution engine is monitoring
                active boundaries.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# 15. EMERGENCY SAFETY CONSOLE
# ============================================================

st.markdown("### Master Emergency Safety Console")

em_top_col1, em_top_col2 = st.columns([3, 1])

with em_top_col1:
    st.info(
        "Primary safety interlock is armed. "
        "Use the emergency flatten switch for "
        "capital preservation during extreme "
        "market dislocations."
    )

with em_top_col2:
    if st.button(
        "EMERGENCY KILL-SWITCH",
        disabled=(
            not st.session_state.agent_active
        ),
        key="elevated_master_flatten_btn",
        use_container_width=True,
    ):
        if not is_admin_active:
            st.error(
                "Administrator access required."
            )
        else:
            log_admin_event(
                "EMERGENCY_KILL_SWITCH",
                "Emergency flatten switch engaged.",
            )

            if st.session_state.portfolio_btc > 0:
                st.session_state.portfolio_cash += (
                    st.session_state.portfolio_btc
                    * current_price
                )
                st.session_state.portfolio_btc = 0.0

            engine.cash = (
                st.session_state.portfolio_cash
            )
            engine.btc_balance = (
                st.session_state.portfolio_btc
            )

            st.session_state.agent_active = False

            append_order_history(
                action="Emergency Flatten",
                price=current_price,
                status="EXECUTED",
                reason="Emergency kill-switch executed.",
            )

            append_governance_history(
                price=current_price,
                rademacher=rad_val,
                dissipativity=dis_val,
                action="Emergency Flatten",
                reason=(
                    "Emergency kill-switch executed."
                ),
                status="EXECUTED",
            )

            engine.append_audit_record(
                event_type="EMERGENCY_KILL_SWITCH",
                actor="Administrator",
                payload={
                    "price": current_price,
                    "cash_after": (
                        st.session_state.portfolio_cash
                    ),
                    "btc_after": (
                        st.session_state.portfolio_btc
                    ),
                },
            )

            st.warning(
                "Emergency flatten executed."
            )
            st.rerun()


# ============================================================
# 16. SOFT-CONTROL STATUS BANNER
# ============================================================

rademacher_warning = rad_val >= rad_limit
dissipativity_warning = dis_val >= dis_limit

if rademacher_warning or dissipativity_warning:
    alert_bg = "#fff3cd"
    alert_border = "#f59e0b"
    alert_color = "#92400e"
    alert_title = (
        "SOFT-LIMIT WARNING: "
        "OPERATOR REVIEW REQUIRED"
    )
else:
    alert_bg = "#d4edda"
    alert_border = "#28a745"
    alert_color = "#155724"
    alert_title = (
        "STATUS NORMAL: "
        "WITHIN RISK NORMS"
    )

st.markdown(
    f"""
    <div style="
        padding: 12px;
        background-color: {alert_bg};
        border: 2px solid {alert_border};
        border-radius: 6px;
        text-align: center;
        margin-bottom: 16px;
    ">
        <h4 style="
            color: {alert_color};
            margin: 0;
            font-size: 1.1rem;
        ">
            {alert_title}
        </h4>
        <p style="
            color: {alert_color};
            margin: 4px 0 0 0;
            font-size: 0.90rem;
        ">
            Rademacher: {rad_val:.6f}
            / soft limit {rad_limit:.6f}
            | Dissipativity V(x): {dis_val:.4f}
            / soft limit {dis_limit:.4f}
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 17. THEORY-FIRST METRICS
# ============================================================

st.subheader(
    "Theory-First Bounds and Live Stress Measures"
)

bound_col1, bound_col2, bound_col3, bound_col4 = (
    st.columns(4)
)

with bound_col1:
    st.metric(
        "Rademacher Estimate",
        f"{rad_val:.6f}",
        delta=(
            f"Limit distance: "
            f"{rad_limit - rad_val:.6f}"
        ),
    )

with bound_col2:
    st.metric(
        "Rademacher Soft Limit",
        f"{rad_limit:.6f}",
        delta=(
            f"SE: "
            f"{rad_standard_error:.6f}"
        ),
    )

with bound_col3:
    st.metric(
        "Dissipativity V(x)",
        f"{dis_val:.4f}",
        delta=(
            f"Limit distance: "
            f"{dis_limit - dis_val:.4f}"
        ),
    )

with bound_col4:
    st.metric(
        "Dissipativity Utilization",
        f"{(dis_val / dis_limit) * 100:.1f}%",
        delta=(
            f"Warning at "
            f"{(dis_warning_limit / dis_limit) * 100:.0f}%"
        ),
    )


# ============================================================
# 18. RUNTIME EXECUTION ENGINE
# ============================================================

st.subheader("Operational Execution Engine")

st.info(
    f"Hard boundaries: Max Buy Price <= "
    f"${engine.max_buy_price:,.2f} "
    f"({engine.buy_cash_pct}% cash) | "
    f"Min Sell Floor >= "
    f"${engine.min_sell_price:,.2f} "
    f"({engine.sell_pct}% holdings)"
)

if not st.session_state.agent_active:
    if st.button(
        "Run Agent on Current Limits",
        type="primary",
        key="prominent_auto_strategy_tick_btn",
        use_container_width=True,
    ):
        if not is_admin_active:
            st.error(
                "Administrator access required."
            )
        else:
            engine.cash = (
                st.session_state.portfolio_cash
            )
            engine.btc_balance = (
                st.session_state.portfolio_btc
            )

            result = (
                engine.run_automated_strategy_tick(
                    current_price=current_price,
                    stress_profile=stress_profile,
                    risk_tier="Low Risk (Retail)",
                )
            )

            st.session_state.portfolio_cash = engine.cash
            st.session_state.portfolio_btc = (
                engine.btc_balance
            )
            st.session_state.agent_active = True
            st.session_state.last_tick_timestamp = (
                time.time()
            )

            status = result.get(
                "status",
                "UNKNOWN",
            )

            reason = result.get(
                "reason",
                "No reason returned.",
            )

            warnings = result.get(
                "warnings",
                [],
            )

            append_order_history(
                action=(
                    "Automated Tick"
                    if status
                    in {
                        "EXECUTED",
                        "EXECUTED_WITH_WARNINGS",
                    }
                    else "Automated Tick Blocked"
                ),
                price=current_price,
                status=status,
                reason=reason,
                warnings=warnings,
            )

            append_governance_history(
                price=current_price,
                rademacher=rad_val,
                dissipativity=dis_val,
                action="Automated Tick",
                reason=reason,
                status=status,
            )

            st.success(
                f"Agent activated: {status}"
            )

            if warnings:
                st.warning(
                    "Soft warnings: "
                    + "; ".join(warnings)
                )

            st.rerun()

else:
    tick_interval_seconds = 600
    current_epoch = time.time()

    if (
        current_epoch
        - st.session_state.last_tick_timestamp
        >= tick_interval_seconds
    ):
        engine.cash = (
            st.session_state.portfolio_cash
        )
        engine.btc_balance = (
            st.session_state.portfolio_btc
        )

        result = (
            engine.run_automated_strategy_tick(
                current_price=current_price,
                stress_profile=stress_profile,
                risk_tier="Low Risk (Retail)",
            )
        )

        st.session_state.portfolio_cash = engine.cash
        st.session_state.portfolio_btc = (
            engine.btc_balance
        )
        st.session_state.last_tick_timestamp = (
            current_epoch
        )

        status = result.get(
            "status",
            "UNKNOWN",
        )

        reason = result.get(
            "reason",
            "No reason returned.",
        )

        warnings = result.get(
            "warnings",
            [],
        )

        append_order_history(
            action="Automated Tick",
            price=current_price,
            status=status,
            reason=reason,
            warnings=warnings,
        )

        append_governance_history(
            price=current_price,
            rademacher=rad_val,
            dissipativity=dis_val,
            action="Automated Tick",
            reason=reason,
            status=status,
        )

        st.rerun()

    else:
        append_governance_history(
            price=current_price,
            rademacher=rad_val,
            dissipativity=dis_val,
            action=None,
            reason=None,
            status="MONITORING",
        )


# ============================================================
# 19. COMPLIANCE MATRIX
# ============================================================

st.markdown("---")

with st.expander(
    "Global AI Compliance and Regulatory Audit Matrix",
    expanded=True,
):
    st.markdown(
        "Configure applicability and status for each "
        "framework. Administrator mode is required "
        "for changes."
    )

    applicability_options = [
        "Mandatory",
        "Recommended",
        "Voluntary",
        "Global",
        "Not Applicable",
    ]

    status_options = [
        "Compliant",
        "Active",
        "Aligned",
        "Certified",
        "Non-Compliant",
        "Pending Audit",
        "N/A",
    ]

    updated_frameworks = {}

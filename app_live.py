from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st

from governance_engine import GovernanceEngine


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("operational-governance-app")


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "btc-usd-max.csv"
AUDIT_PATH = BASE_DIR / "audit_chain.jsonl"

COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
)

PRICE_SOURCE_LIVE = "COINGECKO"
PRICE_SOURCE_FALLBACK = "LAST_SUCCESSFUL_PRICE"
PRICE_SYMBOL = "bitcoin"

PRICE_CACHE_TTL_SECONDS = 15


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@st.cache_data(
    ttl=PRICE_CACHE_TTL_SECONDS,
    show_spinner=False,
)
def fetch_coingecko_btc_price() -> float:
    """
    Fetch the current Bitcoin/USD price from CoinGecko.

    This uses the keyless public API and does not require
    an API key.
    """

    response = requests.get(
        COINGECKO_PRICE_URL,
        params={
            "ids": "bitcoin",
            "vs_currencies": "usd",
        },
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "operational-governance-engine/1.0"
            ),
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError(
            f"Unexpected CoinGecko response: {data}"
        )

    bitcoin_data = data.get("bitcoin")

    if not isinstance(bitcoin_data, dict):
        raise ValueError(
            f"Missing bitcoin data: {data}"
        )

    if "usd" not in bitcoin_data:
        raise ValueError(
            f"Missing USD price: {data}"
        )

    price = float(bitcoin_data["usd"])

    if not np.isfinite(price) or price <= 0:
        raise ValueError(
            f"Invalid CoinGecko price: {price}"
        )

    return price


def get_current_price() -> Tuple[Optional[float], str, Optional[str]]:
    """
    Get a live CoinGecko price, falling back to the
    last successful price in the current session.
    """

    try:
        current_price = fetch_coingecko_btc_price()

        st.session_state.last_successful_price = (
            current_price
        )
        st.session_state.last_price_timestamp = (
            utc_timestamp()
        )
        st.session_state.last_price_error = None

        return (
            current_price,
            PRICE_SOURCE_LIVE,
            None,
        )

    except Exception as exc:
        logger.warning(
            "CoinGecko price request failed: %s",
            exc,
        )

        previous_price = st.session_state.get(
            "last_successful_price"
        )
        previous_timestamp = st.session_state.get(
            "last_price_timestamp"
        )

        if previous_price is not None:
            return (
                float(previous_price),
                PRICE_SOURCE_FALLBACK,
                previous_timestamp,
            )

        return None, "UNAVAILABLE", str(exc)


def append_local_audit_record(
    record: Dict[str, Any],
) -> None:
    """
    Append a UI event to a local JSONL file.

    GovernanceEngine maintains the cryptographic audit
    chain for engine events. This function records UI-level
    events separately.
    """

    try:
        with open(
            AUDIT_PATH,
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )

    except OSError as exc:
        logger.warning(
            "Unable to append UI audit record: %s",
            exc,
        )


def initialize_session_state() -> None:
    """
    Initialize Streamlit session-state values.
    """

    if "engine" not in st.session_state:
        st.session_state.engine = None

    if "last_successful_price" not in st.session_state:
        st.session_state.last_successful_price = None

    if "last_price_timestamp" not in st.session_state:
        st.session_state.last_price_timestamp = None

    if "last_price_error" not in st.session_state:
        st.session_state.last_price_error = None

    if "last_admissibility_result" not in st.session_state:
        st.session_state.last_admissibility_result = None

    if "last_strategy_result" not in st.session_state:
        st.session_state.last_strategy_result = None

    if "last_replay_result" not in st.session_state:
        st.session_state.last_replay_result = None


def initialize_engine() -> GovernanceEngine:
    """
    Initialize GovernanceEngine exactly once per session.
    """

    if st.session_state.engine is None:
        if not CSV_PATH.exists():
            raise FileNotFoundError(
                f"Required CSV file not found: {CSV_PATH}"
            )

        st.session_state.engine = GovernanceEngine(
            csv_path=str(CSV_PATH),
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

    return st.session_state.engine


def render_header(
    current_price: Optional[float],
    price_source: str,
    price_timestamp: Optional[str],
) -> None:
    st.title("Operational Governance Engine")

    st.caption(
        "AI-assisted operational governance, market "
        "monitoring, risk controls, and auditability."
    )

    if current_price is None:
        st.error(
            "No current Bitcoin price is available."
        )
        return

    if price_source == PRICE_SOURCE_LIVE:
        st.success(
            f"Price Source: CoinGecko | "
            f"BTC/USD: ${current_price:,.2f}"
        )

    elif price_source == PRICE_SOURCE_FALLBACK:
        st.warning(
            f"Price Source: Last successful price | "
            f"BTC/USD: ${current_price:,.2f}"
        )

        if price_timestamp:
            st.caption(
                f"Last successful price timestamp: "
                f"{price_timestamp}"
            )

    else:
        st.error(
            "Price source unavailable."
        )


def render_rademacher_metrics(
    engine: GovernanceEngine,
) -> None:
    estimate = engine.rademacher_estimate
    limit = engine.rademacher_limit
    standard_error = engine.rademacher_standard_error
    distance = limit - estimate
    utilization = (
        estimate / limit
        if limit > 0
        else float("inf")
    )

    st.subheader("Rademacher Complexity")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Estimate",
            f"{estimate:.6f}",
        )

    with col2:
        st.metric(
            "Soft Limit",
            f"{limit:.6f}",
        )

    with col3:
        st.metric(
            "Standard Error",
            f"{standard_error:.6f}",
        )

    with col4:
        st.metric(
            "Distance to Limit",
            f"{distance:.6f}",
        )

    st.progress(
        min(max(utilization, 0.0), 1.0),
        text=f"Utilization: {utilization:.2%}",
    )

    if estimate >= limit:
        st.warning(
            "Rademacher soft limit reached or exceeded."
        )
    else:
        st.success(
            "Rademacher complexity is within the configured "
            "soft limit."
        )


def render_dissipativity_metrics(
    engine: GovernanceEngine,
) -> None:
    state = engine.dissipativity.snapshot()

    st.subheader("Dissipativity State")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "V(x)",
            f"{state['V_x']:.4f}",
        )

    with col2:
        st.metric(
            "Soft Limit",
            f"{state['soft_limit']:.4f}",
        )

    with col3:
        st.metric(
            "Warning Limit",
            f"{state['warning_limit']:.4f}",
        )

    with col4:
        st.metric(
            "Utilization",
            f"{state['utilization']:.2%}",
        )

    if state["soft_breach"]:
        st.error(
            "Dissipativity soft limit exceeded."
        )
    elif state["utilization"] >= 0.80:
        st.warning(
            "Dissipativity state is approaching its soft limit."
        )
    else:
        st.success(
            "Dissipativity state is within limits."
        )

    with st.expander("Dissipativity details"):
        st.json(state)


def render_portfolio_metrics(
    engine: GovernanceEngine,
    current_price: Optional[float],
) -> None:
    st.subheader("Portfolio State")

    btc_balance = float(engine.btc_balance)
    cash = float(engine.cash)

    portfolio_value = None

    if current_price is not None:
        portfolio_value = cash + (
            btc_balance * current_price
        )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Cash",
            f"${cash:,.2f}",
        )

    with col2:
        st.metric(
            "BTC Balance",
            f"{btc_balance:.8f}",
        )

    with col3:
        if portfolio_value is None:
            st.metric(
                "Portfolio Value",
                "Unavailable",
            )
        else:
            st.metric(
                "Portfolio Value",
                f"${portfolio_value:,.2f}",
            )


def render_governance_summary(
    engine: GovernanceEngine,
) -> None:
    st.subheader("Governance Summary")

    safe_metrics = engine.current_safe_metrics()

    with st.expander("Complete safe metrics"):
        st.json(safe_metrics)

    with st.expander("Engine configuration"):
        st.json(
            {
                "csv_path": engine.csv_path,
                "rademacher_limit": (
                    engine.rademacher_limit
                ),
                "dissipativity_soft_limit": (
                    engine.dissipativity_threshold
                ),
                "max_buy_price": engine.max_buy_price,
                "buy_cash_pct": engine.buy_cash_pct,
                "min_sell_price": engine.min_sell_price,
                "sell_pct": engine.sell_pct,
                "buy_active": engine.buy_active,
                "sell_active": engine.sell_active,
                "initial_cash": engine.cash,
                "initial_btc": engine.btc_balance,
            }
        )


def render_admissibility_controls(
    engine: GovernanceEngine,
    current_price: float,
    price_source: str,
) -> None:
    st.subheader("Admissibility Check")

    proposed_order_qty = st.number_input(
        "Proposed order quantity in BTC",
        min_value=0.0,
        value=0.0,
        step=0.0001,
        format="%.8f",
        key="admissibility_order_qty",
    )

    risk_tier = st.selectbox(
        "Risk tier",
        options=[
            "Low Risk (Retail)",
            "High Risk (Leveraged Derivative)",
        ],
        key="admissibility_risk_tier",
    )

    stress_profile = st.selectbox(
        "Stress profile",
        options=[
            "Nominal",
            "High Volatility",
            "Liquidity Crunch",
            "Adverse Drawdown",
        ],
        key="admissibility_stress_profile",
    )

    if st.button(
        "Run admissibility check",
        type="primary",
        key="run_admissibility_check",
    ):
        try:
            result = engine.admissibility_check(
                current_price=current_price,
                proposed_order_qty=proposed_order_qty,
                action="execute_spot_trade",
                risk_tier=risk_tier,
                stress_profile=stress_profile,
            )

            st.session_state.last_admissibility_result = (
                result
            )

            append_local_audit_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": "UI_ADMISSIBILITY_CHECK",
                    "source": price_source,
                    "symbol": PRICE_SYMBOL,
                    "price": current_price,
                    "risk_tier": risk_tier,
                    "stress_profile": stress_profile,
                    "proposed_order_qty": (
                        proposed_order_qty
                    ),
                    "result": result,
                }
            )

        except Exception as exc:
            st.error(
                "Admissibility check failed."
            )
            st.exception(exc)

    result = st.session_state.last_admissibility_result

    if result is not None:
        status = result.get("status", "UNKNOWN")

        if status == "ADMISSIBLE":
            st.success(status)
        elif status == "ADMISSIBLE_WITH_WARNINGS":
            st.warning(status)
        else:
            st.error(status)

        st.json(result)


def render_strategy_controls(
    engine: GovernanceEngine,
    current_price: float,
    price_source: str,
) -> None:
    st.subheader("Automated Strategy")

    strategy_risk_tier = st.selectbox(
        "Strategy risk tier",
        options=[
            "Low Risk (Retail)",
            "High Risk (Leveraged Derivative)",
        ],
        key="strategy_risk_tier",
    )

    strategy_stress_profile = st.selectbox(
        "Strategy stress profile",
        options=[
            "Nominal",
            "High Volatility",
            "Liquidity Crunch",
            "Adverse Drawdown",
        ],
        key="strategy_stress_profile",
    )

    st.caption(
        "Automated execution is permitted only when a live "
        "CoinGecko price is available."
    )

    if st.button(
        "Run automated strategy tick",
        key="run_strategy_tick",
    ):
        if price_source != PRICE_SOURCE_LIVE:
            st.error(
                "Automated strategy execution is blocked "
                "because the current price is not live."
            )
            return

        try:
            result = (
                engine.run_automated_strategy_tick(
                    current_price=current_price,
                    stress_profile=(
                        strategy_stress_profile
                    ),
                    risk_tier=strategy_risk_tier,
                )
            )

            st.session_state.last_strategy_result = (
                result
            )

            append_local_audit_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": (
                        "UI_AUTOMATED_STRATEGY_TICK"
                    ),
                    "source": price_source,
                    "symbol": PRICE_SYMBOL,
                    "price": current_price,
                    "risk_tier": strategy_risk_tier,
                    "stress_profile": (
                        strategy_stress_profile
                    ),
                    "result": result,
                }
            )

        except Exception as exc:
            st.error(
                "Automated strategy tick failed."
            )
            st.exception(exc)

    result = st.session_state.last_strategy_result

    if result is not None:
        status = result.get("status", "UNKNOWN")

        if status in {
            "EXECUTED",
            "EXECUTED_WITH_WARNINGS",
        }:
            st.success(status)
        else:
            st.warning(status)

        st.json(result)


def render_limit_controls(
    engine: GovernanceEngine,
) -> None:
    st.subheader("Administrative Limits")

    with st.form("administrative_limits_form"):
        new_rademacher_limit = st.number_input(
            "Rademacher limit",
            min_value=0.000001,
            value=float(engine.rademacher_limit),
            step=0.001,
            format="%.6f",
        )

        new_dissipativity_limit = st.number_input(
            "Dissipativity soft limit",
            min_value=0.000001,
            value=float(
                engine.dissipativity_threshold
            ),
            step=1.0,
            format="%.4f",
        )

        new_max_buy_price = st.number_input(
            "Maximum buy price",
            min_value=0.0,
            value=float(engine.max_buy_price),
            step=100.0,
            format="%.2f",
        )

        new_buy_cash_pct = st.number_input(
            "Buy cash percentage",
            min_value=0.0,
            max_value=100.0,
            value=float(engine.buy_cash_pct),
            step=1.0,
            format="%.2f",
        )

        new_min_sell_price = st.number_input(
            "Minimum sell price",
            min_value=0.0,
            value=float(engine.min_sell_price),
            step=100.0,
            format="%.2f",
        )

        new_sell_pct = st.number_input(
            "Sell percentage",
            min_value=0.0,
            max_value=100.0,
            value=float(engine.sell_pct),
            step=1.0,
            format="%.2f",
        )

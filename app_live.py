from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import requests
import streamlit as st

from governance_engine import GovernanceEngine


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("operational-governance-app")


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "btc-usd-max.csv"
AUDIT_PATH = BASE_DIR / "app_governance.jsonl"

COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
)

COINGECKO_SOURCE = "COINGECKO"
COINGECKO_SYMBOL = "bitcoin"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_coingecko_btc_price() -> Optional[float]:
    """
    Fetch the current Bitcoin price in USD.

    This uses CoinGecko's keyless public API.
    No API key is required.
    """

    try:
        response = requests.get(
            COINGECKO_PRICE_URL,
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd",
            },
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "operational-governance-engine-app/1.0"
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

    except (
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        logger.warning(
            "CoinGecko price request failed: %s",
            exc,
        )
        return None


def get_price_with_fallback() -> Tuple[Optional[float], str]:
    """
    Return a live CoinGecko price or the last successful price.
    """

    current_price = fetch_coingecko_btc_price()

    if current_price is not None:
        st.session_state.last_successful_price = current_price
        st.session_state.last_price_timestamp = (
            utc_timestamp()
        )
        return current_price, COINGECKO_SOURCE

    previous_price = st.session_state.get(
        "last_successful_price"
    )

    if previous_price is not None:
        return float(previous_price), "LAST_SUCCESSFUL_PRICE"

    return None, "UNAVAILABLE"


def append_app_record(record: Dict[str, Any]) -> None:
    """
    Append a JSON record to the local application audit file.
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
            "Could not write application audit record: %s",
            exc,
        )


def initialize_engine() -> GovernanceEngine:
    """
    Initialize GovernanceEngine once per Streamlit session.
    """

    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"Required file not found: {CSV_PATH}"
        )

    if "engine" not in st.session_state:
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


def show_price_status(
    current_price: float,
    price_source: str,
) -> None:
    """
    Display the current price source and value.
    """

    if price_source == COINGECKO_SOURCE:
        st.success(
            f"Price Source: CoinGecko API | "
            f"BTC/USD: ${current_price:,.2f}"
        )
    elif price_source == "LAST_SUCCESSFUL_PRICE":
        st.warning(
            f"Price Source: Last successful price | "
            f"BTC/USD: ${current_price:,.2f}"
        )
    else:
        st.error("No usable Bitcoin price is available.")


def show_engine_metrics(
    engine: GovernanceEngine,
    current_price: float,
) -> None:
    safe_metrics = engine.current_safe_metrics()

    rademacher = safe_metrics["rademacher"]
    dissipativity = safe_metrics["dissipativity"]

    st.subheader("Current Metrics")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "BTC/USD",
            f"${current_price:,.2f}",
        )

    with col2:
        st.metric(
            "Rademacher Estimate",
            f"{rademacher['estimate']:.6f}",
        )

    with col3:
        st.metric(
            "Rademacher Limit",
            f"{rademacher['limit']:.6f}",
        )

    with col4:
        st.metric(
            "Dissipativity V(x)",
            f"{dissipativity['V_x']:.4f}",
        )

    with st.expander("Detailed governance metrics"):
        st.json(safe_metrics)


def run_admissibility_check(
    engine: GovernanceEngine,
    current_price: float,
    proposed_order_qty: float,
    risk_tier: str,
    stress_profile: str,
) -> Dict[str, Any]:
    return engine.admissibility_check(
        current_price=current_price,
        proposed_order_qty=proposed_order_qty,
        action="execute_spot_trade",
        risk_tier=risk_tier,
        stress_profile=stress_profile,
    )


def render_sidebar() -> Tuple[
    float,
    str,
    str,
]:
    st.sidebar.header("Governance Controls")

    proposed_order_qty = st.sidebar.number_input(
        "Proposed BTC order quantity",
        min_value=0.0,
        value=0.0,
        step=0.0001,
        format="%.6f",
    )

    risk_tier = st.sidebar.selectbox(
        "Risk tier",
        options=[
            "Low Risk (Retail)",
            "High Risk (Leveraged Derivative)",
        ],
    )

    stress_profile = st.sidebar.selectbox(
        "Stress profile",
        options=[
            "Nominal",
            "High Volatility",
            "Liquidity Crunch",
            "Adverse Drawdown",
        ],
    )

    return (
        float(proposed_order_qty),
        risk_tier,
        stress_profile,
    )


def main() -> None:
    st.set_page_config(
        page_title="Operational Governance Engine",
        page_icon="?",
        layout="wide",
    )

    st.title("Operational Governance Engine")
    st.caption(
        "Governed BTC/USD monitoring using CoinGecko "
        "keyless public market data."
    )

    try:
        engine = initialize_engine()
    except Exception as exc:
        st.error(
            "The governance engine could not be initialized."
        )
        st.exception(exc)
        st.stop()

    current_price, price_source = (
        get_price_with_fallback()
    )

    if current_price is None:
        st.error(
            "CoinGecko is unavailable and no previous "
            "price exists."
        )
        st.stop()

    show_price_status(
        current_price=current_price,
        price_source=price_source,
    )

    if price_source == "LAST_SUCCESSFUL_PRICE":
        last_timestamp = st.session_state.get(
            "last_price_timestamp",
            "unknown",
        )
        st.caption(
            f"Last successful price timestamp: "
            f"{last_timestamp}"
        )

    show_engine_metrics(
        engine=engine,
        current_price=current_price,
    )

    (
        proposed_order_qty,
        risk_tier,
        stress_profile,
    ) = render_sidebar()

    st.subheader("Admissibility Check")

    if st.button(
        "Run admissibility check",
        type="primary",
    ):
        try:
            result = run_admissibility_check(
                engine=engine,
                current_price=current_price,
                proposed_order_qty=proposed_order_qty,
                risk_tier=risk_tier,
                stress_profile=stress_profile,
            )

            append_app_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": "ADMISSIBILITY_CHECK",
                    "source": price_source,
                    "symbol": COINGECKO_SYMBOL,
                    "price": current_price,
                    "result": result,
                }
            )

            status = result.get("status", "UNKNOWN")

            if status == "ADMISSIBLE":
                st.success(status)
            elif status == "ADMISSIBLE_WITH_WARNINGS":
                st.warning(status)
            else:
                st.error(status)

            st.json(result)

        except Exception as exc:
            st.error(
                "The admissibility check failed."
            )
            st.exception(exc)

    st.divider()

    st.subheader("Automated Strategy")

    if st.button("Run automated strategy tick"):
        if price_source != COINGECKO_SOURCE:
            st.error(
                "Automated execution is blocked because "
                "the current price is not live."
            )
            st.stop()

        try:
            result = (
                engine.run_automated_strategy_tick(
                    current_price=current_price,
                    stress_profile=stress_profile,
                    risk_tier=risk_tier,
                )
            )

            append_app_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": (
                        "AUTOMATED_STRATEGY_TICK"
                    ),
                    "source": price_source,
                    "symbol": COINGECKO_SYMBOL,
                    "price": current_price,
                    "result": result,
                }
            )

            status = result.get("status", "UNKNOWN")

            if status in {
                "EXECUTED",
                "EXECUTED_WITH_WARNINGS",
            }:
                st.success(status)
            else:
                st.warning(status)

            st.json(result)

        except Exception as exc:
            st.error(
                "The automated strategy tick failed."
            )
            st.exception(exc)

    st.divider()

    st.subheader("Three-Year Replay")

    replay_profile = st.selectbox(
        "Replay stress profile",
        options=[
            "Nominal",
            "High Volatility",
            "Liquidity Crunch",
            "Adverse Drawdown",
        ],
        key="replay_profile",
    )

    if st.button("Run three-year replay"):
        try:
            result = engine.run_step_2_replay(
                profile=replay_profile,
                duration="3 Years",
            )

            append_app_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": "THREE_YEAR_REPLAY",
                    "profile": replay_profile,
                    "result": result,
                }
            )

            st.json(result)

        except Exception as exc:
            st.error(
                "The historical replay failed."
            )
            st.exception(exc)


if __name__ == "__main__":
    main()

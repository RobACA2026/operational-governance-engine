from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st

from governance_engine import GovernanceEngine


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(
    "operational-governance-app"
)


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "btc-usd-max.csv"
AUDIT_PATH = BASE_DIR / "audit_chain.jsonl"
UI_AUDIT_PATH = BASE_DIR / "app_governance.jsonl"

COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
)

COINGECKO_SOURCE = "COINGECKO"
FALLBACK_SOURCE = "LAST_SUCCESSFUL_PRICE"
PRICE_SYMBOL = "bitcoin"

PRICE_CACHE_TTL_SECONDS = 60

APP_BOOT_MARKER = "app_live.py loaded"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@st.cache_data(
    ttl=PRICE_CACHE_TTL_SECONDS,
    show_spinner=False,
)
def fetch_coingecko_btc_price() -> Tuple[
    Optional[float],
    Optional[str],
]:
    """
    Return (price, error_message).

    This function catches all expected request and parsing
    failures so the Streamlit rendering layer receives a
    predictable result instead of an uncaught exception.
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
                    "operational-governance-engine/1.0"
                ),
            },
            timeout=10,
        )

        if response.status_code != 200:
            return (
                None,
                "CoinGecko HTTP status "
                f"{response.status_code}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            return (
                None,
                f"CoinGecko returned invalid JSON: {exc}",
            )

        if not isinstance(data, dict):
            return (
                None,
                "CoinGecko returned a non-object response.",
            )

        bitcoin_data = data.get("bitcoin")

        if not isinstance(bitcoin_data, dict):
            return (
                None,
                "CoinGecko response has no bitcoin object.",
            )

        raw_price = bitcoin_data.get("usd")

        if raw_price is None:
            return (
                None,
                "CoinGecko response has no bitcoin.usd value.",
            )

        price = float(raw_price)

        if not np.isfinite(price) or price <= 0:
            return (
                None,
                f"CoinGecko returned invalid price: {price}",
            )

        return price, None

    except requests.RequestException as exc:
        logger.warning(
            "CoinGecko network error: %s",
            exc,
        )
        return None, f"Network error: {exc}"

    except (TypeError, ValueError) as exc:
        logger.warning(
            "CoinGecko value error: %s",
            exc,
        )
        return None, f"Invalid CoinGecko data: {exc}"

    except Exception as exc:
        logger.exception(
            "Unexpected CoinGecko error."
        )
        return None, f"Unexpected error: {exc}"


def get_current_price() -> Tuple[
    Optional[float],
    str,
    Optional[str],
]:
    """
    Return current price, source, and timestamp/diagnostic.
    """

    price, error_message = (
        fetch_coingecko_btc_price()
    )

    if price is not None:
        st.session_state.last_successful_price = (
            price
        )
        st.session_state.last_price_timestamp = (
            utc_timestamp()
        )
        st.session_state.last_price_error = None

        logger.info(
            "CoinGecko price accepted: %.2f",
            price,
        )

        return (
            price,
            COINGECKO_SOURCE,
            None,
        )

    previous_price = st.session_state.get(
        "last_successful_price"
    )

    previous_timestamp = st.session_state.get(
        "last_price_timestamp"
    )

    st.session_state.last_price_error = (
        error_message
    )

    if previous_price is not None:
        logger.warning(
            "Using last successful price %.2f. "
            "CoinGecko reason: %s",
            previous_price,
            error_message,
        )

        return (
            float(previous_price),
            FALLBACK_SOURCE,
            previous_timestamp,
        )

    logger.error(
        "No live or fallback price available: %s",
        error_message,
    )

    return (
        None,
        "UNAVAILABLE",
        error_message,
    )


def initialize_session_state() -> None:
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


def append_ui_audit_record(
    record: Dict[str, Any],
) -> None:
    try:
        with open(
            UI_AUDIT_PATH,
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
            "Could not write UI audit record: %s",
            exc,
        )


def read_engine_audit_records() -> List[
    Dict[str, Any]
]:
    if not AUDIT_PATH.exists():
        return []

    records = []

    try:
        with open(
            AUDIT_PATH,
            "r",
            encoding="utf-8",
        ) as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    except OSError as exc:
        logger.warning(
            "Could not read engine audit chain: %s",
            exc,
        )

    return records


def verify_engine_audit_chain() -> Dict[str, Any]:
    records = read_engine_audit_records()

    if not records:
        return {
            "valid": True,
            "records_checked": 0,
            "reason": "No engine audit records found.",
        }

    expected_previous_hash = "0" * 64

    for index, record in enumerate(records):
        actual_previous_hash = record.get(
            "previous_hash"
        )

        if actual_previous_hash != (
            expected_previous_hash
        ):
            return {
                "valid": False,
                "records_checked": index,
                "reason": (
                    "Previous hash mismatch at record "
                    f"{index}."
                ),
            }

        unsigned_record = dict(record)

        actual_hash = unsigned_record.pop(
            "current_hash",
            None,
        )

        canonical = json.dumps(
            unsigned_record,
            sort_keys=True,
            separators=(",", ":"),
        )

        expected_hash = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        if actual_hash != expected_hash:
            return {
                "valid": False,
                "records_checked": index + 1,
                "reason": (
                    "Current hash mismatch at record "
                    f"{index}."
                ),
            }

        expected_previous_hash = actual_hash

    return {
        "valid": True,
        "records_checked": len(records),
        "last_hash": expected_previous_hash,
        "reason": "Engine audit chain verified.",
    }


def render_price_status(
    current_price: Optional[float],
    price_source: str,
    price_timestamp: Optional[str],
) -> None:
    if current_price is None:
        st.error(
            "No current or fallback Bitcoin price is available."
        )

        error_message = (
            st.session_state.get(
                "last_price_error"
            )
            or "No price diagnostic is available."
        )

        st.code(error_message)
        return

    if price_source == COINGECKO_SOURCE:
        st.success(
            f"Price Source: CoinGecko | "
            f"BTC/USD: ${current_price:,.2f}"
        )
        return

    if price_source == FALLBACK_SOURCE:
        st.warning(
            f"Price Source: Last successful price | "
            f"BTC/USD: ${current_price:,.2f}"
        )

        if price_timestamp:
            st.caption(
                f"Last successful price timestamp: "
                f"{price_timestamp}"
            )

        if st.session_state.get(
            "last_price_error"
        ):
            st.caption(
                "CoinGecko diagnostic: "
                f"{st.session_state.last_price_error}"
            )

        return

    st.error(
        f"Unknown price source: {price_source}"
    )


def render_portfolio_metrics(
    engine: GovernanceEngine,
    current_price: float,
) -> None:
    st.subheader("Portfolio State")

    cash = float(engine.cash)
    btc_balance = float(engine.btc_balance)

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
        st.metric(
            "Portfolio Value",
            f"${portfolio_value:,.2f}",
        )


def render_rademacher_metrics(
    engine: GovernanceEngine,
) -> None:
    estimate = float(
        engine.rademacher_estimate
    )
    limit = float(engine.rademacher_limit)
    standard_error = float(
        engine.rademacher_standard_error
    )

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

    progress_value = min(
        max(utilization, 0.0),
        1.0,
    )

    st.progress(progress_value)

    st.caption(
        f"Utilization: {utilization:.2%}"
    )

    if estimate >= limit:
        st.warning(
            "Rademacher soft limit reached or exceeded."
        )
    else:
        st.success(
            "Rademacher complexity is within the "
            "configured soft limit."
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
            "Dissipativity state is approaching its "
            "soft limit."
        )
    else:
        st.success(
            "Dissipativity state is within limits."
        )

    with st.expander("Dissipativity details"):
        st.json(state)


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
                "cash": engine.cash,
                "btc_balance": engine.btc_balance,
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
                proposed_order_qty=(
                    proposed_order_qty
                ),
                action="execute_spot_trade",
                risk_tier=risk_tier,
                stress_profile=stress_profile,
            )

            st.session_state.last_admissibility_result = (
                result
            )

            append_ui_audit_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": (
                        "UI_ADMISSIBILITY_CHECK"
                    ),
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

    if st.button(
        "Run automated strategy tick",
        key="run_strategy_tick",
    ):
        if price_source != COINGECKO_SOURCE:
            st.error(
                "Automated execution is blocked because "
                "the current price is not live."
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

            append_ui_audit_record(
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

        new_buy_active = st.checkbox(
            "Buy active",
            value=bool(engine.buy_active),
        )

        new_sell_active = st.checkbox(
            "Sell active",
            value=bool(engine.sell_active),
        )

        submitted = st.form_submit_button(
            "Apply administrative limits"
        )

    if submitted:
        try:
            engine.update_limits(
                {
                    "rademacher_limit": (
                        new_rademacher_limit
                    ),
                    "dissipativity_soft_limit": (
                        new_dissipativity_limit
                    ),
                    "max_buy_price": (
                        new_max_buy_price
                    ),
                    "buy_cash_pct": (
                        new_buy_cash_pct
                    ),
                    "min_sell_price": (
                        new_min_sell_price
                    ),
                    "sell_pct": new_sell_pct,
                    "buy_active": new_buy_active,
                    "sell_active": new_sell_active,
                }
            )

            append_ui_audit_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": (
                        "UI_ADMINISTRATIVE_LIMIT_UPDATE"
                    ),
                    "new_limits": {
                        "rademacher_limit": (
                            new_rademacher_limit
                        ),
                        "dissipativity_soft_limit": (
                            new_dissipativity_limit
                        ),
                        "max_buy_price": (
                            new_max_buy_price
                        ),
                        "buy_cash_pct": (
                            new_buy_cash_pct
                        ),
                        "min_sell_price": (
                            new_min_sell_price
                        ),
                        "sell_pct": new_sell_pct,
                        "buy_active": new_buy_active,
                        "sell_active": new_sell_active,
                    },
                }
            )

            st.success(
                "Administrative limits updated."
            )

        except Exception as exc:
            st.error(
                "Administrative limit update failed."
            )
            st.exception(exc)


def render_replay_controls(
    engine: GovernanceEngine,
) -> None:
    st.subheader("Historical Replay")

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

    if st.button(
        "Run Step 2 replay",
        key="run_step_2_replay",
    ):
        try:
            result = engine.run_step_2_replay(
                profile=replay_profile,
                duration="3 Years",
            )

            st.session_state.last_replay_result = (
                result
            )

            append_ui_audit_record(
                {
                    "timestamp": utc_timestamp(),
                    "event_type": "UI_STEP_2_REPLAY",
                    "profile": replay_profile,
                    "duration": "3 Years",
                    "result": result,
                }
            )

        except Exception as exc:
            st.error(
                "Historical replay failed."
            )
            st.exception(exc)

    result = st.session_state.last_replay_result

    if result is not None:
        st.json(result)


def render_audit_trail() -> None:
    st.subheader("Audit Trail")

    verification = verify_engine_audit_chain()

    if verification["valid"]:
        st.success(
            verification["reason"]
        )
    else:
        st.error(
            verification["reason"]
        )

    st.json(verification)

    records = read_engine_audit_records()

    if records:
        st.dataframe(
            pd.DataFrame(records),
            use_container_width=True,
        )
    else:
        st.info(
            "No engine audit records found yet."
        )

    if UI_AUDIT_PATH.exists():
        st.subheader("UI Audit Events")

        try:
            ui_records = []

            with open(
                UI_AUDIT_PATH,
                "r",
                encoding="utf-8",
            ) as file:
                for line in file:
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        ui_records.append(
                            json.loads(line)
                        )
                    except json.JSONDecodeError:
                        continue

            if ui_records:
                st.dataframe(
                    pd.DataFrame(ui_records),
                    use_container_width=True,
                )

        except OSError as exc:
            st.warning(
                f"Unable to read UI audit events: {exc}"
            )


def main() -> None:
    st.set_page_config(
        page_title="Operational Governance Engine",
        page_icon="₿",
        layout="wide",
    )

    st.title("Operational Governance Engine")
    st.caption(APP_BOOT_MARKER)

    initialize_session_state()

    try:
        engine = initialize_engine()
    except Exception as exc:
        st.error(
            "GovernanceEngine initialization failed."
        )
        st.exception(exc)
        st.stop()

    current_price, price_source, price_timestamp = (
        get_current_price()
    )

    render_price_status(
        current_price=current_price,
        price_source=price_source,
        price_timestamp=price_timestamp,
    )

    if current_price is None:
        st.stop()

    render_portfolio_metrics(
        engine=engine,
        current_price=current_price,
    )

    tab_dashboard, tab_controls, tab_replay, tab_audit = (
        st.tabs(
            [
                "Dashboard",
                "Controls",
                "Replay",
                "Audit Trail",
            ]
        )
    )

    with tab_dashboard:
        render_rademacher_metrics(engine)
        render_dissipativity_metrics(engine)
        render_governance_summary(engine)

    with tab_controls:
        render_admissibility_controls(
            engine=engine,
            current_price=current_price,
            price_source=price_source,
        )

        st.divider()

        render_strategy_controls(
            engine=engine,
            current_price=current_price,
            price_source=price_source,
        )

        st.divider()

        render_limit_controls(engine)

    with tab_replay:
        render_replay_controls(engine)

    with tab_audit:
        render_audit_trail()


if __name__ == "__main__":
    main()

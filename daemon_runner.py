import json
import time
from datetime import datetime, timezone

import requests

from governance_engine import GovernanceEngine


BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
TICK_SECONDS = 60
TOTAL_TICKS = 1440


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def fetch_binance_btc_price() -> float:
    response = requests.get(
        BINANCE_PRICE_URL,
        params={"symbol": "BTCUSDT"},
        timeout=10,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("symbol") != "BTCUSDT":
        raise ValueError(f"Unexpected response: {data}")

    price = float(data["price"])

    if price <= 0:
        raise ValueError(f"Invalid price: {price}")

    return price


def append_daemon_record(record: dict) -> None:
    with open(
        "daemon_governance.jsonl",
        "a",
        encoding="utf-8",
    ) as file:
        file.write(json.dumps(record, sort_keys=True) + "\n")


def main():
    engine = GovernanceEngine(
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

    print("Operational Governance live daemon started.")
    print(
        f"Historical Rademacher estimate: "
        f"{engine.rademacher_estimate:.6f}"
    )
    print(
        f"Rademacher soft limit: "
        f"{engine.rademacher_limit:.6f}"
    )
    print(
        f"Dissipativity soft limit: "
        f"{engine.dissipativity.soft_limit:.4f}"
    )

    for tick in range(1, TOTAL_TICKS + 1):
        timestamp = utc_timestamp()

        try:
            current_price = fetch_binance_btc_price()
        except Exception as exc:
            error_record = {
                "timestamp": timestamp,
                "tick": tick,
                "status": "DATA_ERROR",
                "source": "BINANCE",
                "symbol": "BTCUSDT",
                "reason": str(exc),
            }

            append_daemon_record(error_record)

            print(
                f"[{timestamp}] Tick {tick}/{TOTAL_TICKS} | "
                f"DATA_ERROR | {exc}"
            )

            time.sleep(TICK_SECONDS)
            continue

        result = engine.run_automated_strategy_tick(
            current_price=current_price,
            stress_profile="Nominal",
            risk_tier="Low Risk (Retail)",
        )

        safe_metrics = engine.current_safe_metrics()
        dissipativity = safe_metrics["dissipativity"]

        record = {
            "timestamp": timestamp,
            "tick": tick,
            "source": "BINANCE",
            "symbol": "BTCUSDT",
            "price": current_price,
            "status": result.get("status"),
            "admissible": result.get("admissible"),
            "hard_reasons": result.get(
                "hard_reasons",
                [],
            ),
            "warnings": result.get(
                "warnings",
                [],
            ),
            "reason": result.get("reason"),
            "warning": result.get("warning"),
            "rademacher": {
                "estimate": engine.rademacher_estimate,
                "limit": engine.rademacher_limit,
                "standard_error": (
                    engine.rademacher_standard_error
                ),
                "distance_to_limit": (
                    engine.rademacher_limit
                    - engine.rademacher_estimate
                ),
            },
            "dissipativity": dissipativity,
            "cash": engine.cash,
            "btc_balance": engine.btc_balance,
        }

        append_daemon_record(record)

        warning_text = (
            "; ".join(result.get("warnings", []))
            or "none"
        )

        print(
            f"[{timestamp}] "
            f"Tick {tick}/{TOTAL_TICKS} | "
            f"Price: ${current_price:,.2f} | "
            f"Status: {result.get('status')} | "
            f"Rademacher: "
            f"{engine.rademacher_estimate:.6f}/"
            f"{engine.rademacher_limit:.6f} | "
            f"V(x): "
            f"{dissipativity['V_x']:.4f}/"
            f"{dissipativity['soft_limit']:.4f} | "
            f"Warnings: {warning_text}"
        )

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
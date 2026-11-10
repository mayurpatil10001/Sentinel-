"""
Generates realistic-ish synthetic order flow for one instrument, with an
injected spoofing/layering pattern from one account, plus normal noise
from other accounts. This exists ONLY because real order-book data isn't
publicly available for India — see README "What's real vs synthetic".
"""

import random
import uuid
from datetime import datetime, timedelta

from app.db.models import Instrument, InstrumentType, Order, OrderSide, OrderStatus


def _oid():
    return str(uuid.uuid4())


def make_instrument(symbol="XYZSTOCK", exchange="NSE") -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange=exchange,
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=50_000,
        avg_order_size_30d=200,  # normal order size baseline for this stock
        avg_daily_turnover_30d=2_500_000,
    )


def generate_normal_noise(
    instrument: Instrument, start_time: datetime, n_accounts=15, n_orders_each=8
) -> list[Order]:
    orders = []
    base_price = 42.0
    for i in range(n_accounts):
        account_id = f"ACC{1000+i}"
        t = start_time
        price = base_price + random.uniform(-0.3, 0.3)
        for _ in range(n_orders_each):
            qty = max(10, int(random.gauss(200, 60)))  # near baseline
            side = random.choice([OrderSide.BUY, OrderSide.SELL])
            status = random.choices(
                [OrderStatus.EXECUTED, OrderStatus.CANCELLED],
                weights=[0.75, 0.25],
            )[0]
            t = t + timedelta(seconds=random.randint(5, 90))
            orders.append(
                Order(
                    id=_oid(),
                    exchange_order_id=_oid()[:12],
                    account_id=account_id,
                    instrument_id=instrument.id,
                    side=side,
                    status=status,
                    price=round(price + random.uniform(-0.1, 0.1), 2),
                    quantity=qty,
                    filled_quantity=qty if status == OrderStatus.EXECUTED else 0,
                    session="normal",
                    timestamp=t,
                    exchange=instrument.exchange,
                )
            )
    return orders


def inject_spoofing_pattern(
    instrument: Instrument, start_time: datetime, account_id="ACC9999"
) -> list[Order]:
    """
    Simulates: account places several large BUY orders (way above normal
    size) to push price up, cancels almost all of them, then sells a
    smaller genuine position at the inflated price.
    """
    orders = []
    t = start_time
    price = 42.0

    # Step 1: place large buy orders (spoof side), price ticks up as book reacts
    for i in range(4):
        qty = random.randint(1200, 1800)  # ~6-9x baseline of 200
        price += random.uniform(0.15, 0.3)
        t += timedelta(seconds=random.randint(3, 8))
        orders.append(
            Order(
                id=_oid(),
                exchange_order_id=_oid()[:12],
                account_id=account_id,
                instrument_id=instrument.id,
                side=OrderSide.BUY,
                status=OrderStatus.PLACED,
                price=round(price, 2),
                quantity=qty,
                filled_quantity=0,
                session="normal",
                timestamp=t,
                exchange=instrument.exchange,
            )
        )

    # Step 2: cancel almost all of them shortly after (before execution)
    placed_orders = list(orders)  # snapshot — avoid mutating while iterating
    for o in placed_orders:
        t += timedelta(seconds=random.randint(2, 6))
        cancelled = Order(
            id=_oid(),
            exchange_order_id=o.exchange_order_id,
            account_id=account_id,
            instrument_id=instrument.id,
            side=o.side,
            status=OrderStatus.CANCELLED,
            price=o.price,
            quantity=o.quantity,
            filled_quantity=0,
            session="normal",
            timestamp=t,
            exchange=instrument.exchange,
        )
        orders.append(cancelled)

    # Step 3: sell a smaller genuine position at the now-inflated price
    t += timedelta(seconds=5)
    sell_qty = 300
    orders.append(
        Order(
            id=_oid(),
            exchange_order_id=_oid()[:12],
            account_id=account_id,
            instrument_id=instrument.id,
            side=OrderSide.SELL,
            status=OrderStatus.EXECUTED,
            price=round(price, 2),
            quantity=sell_qty,
            filled_quantity=sell_qty,
            session="normal",
            timestamp=t,
            exchange=instrument.exchange,
        )
    )

    return orders


def generate_demo_dataset():
    instrument = make_instrument()
    start_time = datetime(2026, 9, 3, 10, 0, 0)

    noise = generate_normal_noise(instrument, start_time)
    spoof = inject_spoofing_pattern(
        instrument, start_time + timedelta(minutes=5)
    )

    all_orders = noise + spoof
    return instrument, all_orders

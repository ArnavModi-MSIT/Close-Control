"""Shared helpers: business-day calendar, ID/UTR generation, amount sampling."""

import random
import string
import datetime as dt

import numpy as np

from . import config


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in config.INDIA_HOLIDAYS_2026


def add_business_days(d: dt.date, n: int) -> dt.date:
    cur = d
    added = 0
    while added < n:
        cur += dt.timedelta(days=1)
        if is_business_day(cur):
            added += 1
    return cur


def rand_id(prefix: str, length: int = 14) -> str:
    chars = string.ascii_letters + string.digits
    return f"{prefix}_{''.join(random.choices(chars, k=length))}"


def rand_utr() -> str:
    return f"{random.randint(100000000, 999999999)}{''.join(random.choices(string.ascii_lowercase, k=6))}"


def gross_amount() -> int:
    """Amount in paise. Skewed toward small retail transactions."""
    r = random.random()
    if r < 0.75:
        rupees = round(np.random.uniform(150, 3000), 2)
    elif r < 0.95:
        rupees = round(np.random.uniform(3000, 25000), 2)
    else:
        rupees = round(np.random.uniform(25000, 250000), 2)
    return int(round(rupees * 100))


def pick_method() -> str:
    methods, weights = zip(*[(k, v[1]) for k, v in config.PAYMENT_METHODS.items()])
    return random.choices(methods, weights=weights, k=1)[0]


def random_datetime(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(
        hour=random.randint(9, 22), minute=random.randint(0, 59), second=random.randint(0, 59)
    ))


def unix_ts(d: dt.datetime) -> int:
    return int(d.timestamp())


def compute_fee_tax(gross_paise: int, method: str, wrong_fee: bool = False):
    mdr, _ = config.PAYMENT_METHODS[method]
    if wrong_fee:
        # always a positive perturbation (never clamped to zero for 0%-MDR
        # methods) -- guarantees fee_variance is always a genuine, detectable
        # discrepancy, not a coin-flip that sometimes silently vanishes
        mdr = mdr + random.uniform(0.002, 0.006)
    fee = round(gross_paise * mdr)
    tax = round(fee * config.GST_ON_FEE)
    return fee, tax

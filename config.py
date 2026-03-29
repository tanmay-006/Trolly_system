#!/usr/bin/env python3
"""Shared environment-backed configuration values."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
SHOP_NAME = os.getenv("SHOP_NAME", "Smart Trolley Shop").strip() or "Smart Trolley Shop"

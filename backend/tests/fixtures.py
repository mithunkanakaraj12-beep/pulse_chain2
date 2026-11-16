"""Shared test fixtures: one trained model per test run, a fresh database per test class."""
from __future__ import annotations

import atexit
import dataclasses
import shutil
import tempfile
from pathlib import Path

from pulsechain.config import load_settings
from pulsechain.engine import Engine

_MODEL_DIR: Path | None = None
_ROOT = Path(tempfile.mkdtemp(prefix="pulsechain-tests-"))


def model_dir() -> Path:
    """Train once (about 20 s) and reuse for every test in the run."""
    global _MODEL_DIR
    if _MODEL_DIR is None:
        d = _ROOT / "models"
        eng = Engine(dataclasses.replace(load_settings(), db_path=_ROOT / "train.db", model_dir=d))
        eng.reset()
        eng.train()
        _MODEL_DIR = d
    return _MODEL_DIR


def make_settings(name: str, **overrides):
    return dataclasses.replace(load_settings(), db_path=_ROOT / f"{name}.db", model_dir=model_dir(),
                               sms_mode="simulated", sms_allow_live=False, twilio_account_sid="", twilio_auth_token="",
                               twilio_from_number="", open_meteo_enabled=False, **overrides)


def make_engine(name: str, **overrides) -> Engine:
    eng = Engine(make_settings(name, **overrides))
    eng.reset()
    return eng


def cleanup():
    shutil.rmtree(_ROOT, ignore_errors=True)


atexit.register(cleanup)

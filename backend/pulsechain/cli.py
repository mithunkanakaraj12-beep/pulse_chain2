"""Command line interface.

    python -m pulsechain.cli reset [--train]   regenerate the synthetic dataset (optionally retrain the model)
    python -m pulsechain.cli train             train / retrain the forecasting models
    python -m pulsechain.cli serve             start the API + web app (generates data / trains on first run)
"""
from __future__ import annotations

import argparse
import json
import sys

from .api import create_app
from .config import load_settings
from .engine import Engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulsechain", description="PulseChain AI command line")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_reset = sub.add_parser("reset", help="regenerate the synthetic demo dataset")
    p_reset.add_argument("--train", action="store_true", help="also (re)train the forecasting models")
    sub.add_parser("train", help="train the forecasting models on the current dataset")
    p_serve = sub.add_parser("serve", help="run the web app")
    p_serve.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = Engine(settings)
    if args.cmd == "reset":
        print(json.dumps(engine.reset(), indent=2))
        if args.train or not engine.model_ready():
            engine.train(print)
            print("Model trained.")
    elif args.cmd == "train":
        if not engine.has_data():
            engine.reset()
        meta = engine.train(print)
        for disease, m in meta["metrics"]["diseases"].items():
            print(f"{disease:10s} MAE model {m['mae_model']:.2f} | seasonal-naive {m['mae_seasonal_naive']:.2f} | "
                  f"persistence {m['mae_persistence']:.2f} | 80% interval coverage {m['interval_coverage_80']:.0%}")
    elif args.cmd == "serve":
        engine.ensure_ready(print)
        app = create_app(settings)
        print(f"PulseChain AI running at http://{settings.host}:{settings.port}  (SMS mode: {settings.sms_mode})")
        app.run(host=settings.host, port=settings.port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

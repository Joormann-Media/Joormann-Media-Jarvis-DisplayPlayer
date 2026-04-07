#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from src.deviceplayer.config import build_config
from src.deviceplayer.logger import configure_logger


def main() -> int:
    parser = argparse.ArgumentParser(description='Joormann Media DevicePlayer')
    parser.add_argument('--manifest', default='', help='Path to local manifest.json')
    args = parser.parse_args()

    cfg = build_config(args.manifest or None)
    log = configure_logger(cfg.log_level)
    log.info('starting device player with manifest=%s', cfg.manifest_path)

    from src.deviceplayer.app import DevicePlayerApp

    app = DevicePlayerApp(cfg)
    return app.run()


if __name__ == '__main__':
    sys.exit(main())

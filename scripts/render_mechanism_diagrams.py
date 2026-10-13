#!/usr/bin/env python3
"""Build editorial Mermaid SVGs; not required when running or deploying the site."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "virt_report" / "content" / "diagrams"
ASSETS = ROOT / "virt_report" / "render" / "assets"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mmdc", default="mmdc", help="Mermaid CLI 11.12.0 executable")
    parser.add_argument("--puppeteer-config", type=Path, help="Optional local Chromium configuration")
    args = parser.parse_args()
    for source in sorted(SOURCES.glob("*.mmd")):
        command = [args.mmdc, "--input", str(source), "--output", str(ASSETS / f"{source.stem}.svg"),
                   "--configFile", str(SOURCES / "mermaid-config.json"), "--backgroundColor", "transparent"]
        if args.puppeteer_config:
            command.extend(["--puppeteerConfigFile", str(args.puppeteer_config)])
        subprocess.run(command, check=True, timeout=90)


if __name__ == "__main__":
    main()

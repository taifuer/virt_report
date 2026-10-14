#!/usr/bin/env python3
"""Build editorial Mermaid SVGs; not required when running or deploying the site."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "virt_report" / "content" / "diagrams"
ASSETS = ROOT / "virt_report" / "render" / "assets"
SVG_NS = "http://www.w3.org/2000/svg"


def prepare_svg(svg: str) -> str:
    """Validate offline SVGs and preserve their intrinsic size for zooming."""
    root = ElementTree.fromstring(svg)
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("Expected an SVG document")
    bounds = [float(value) for value in root.get("viewBox", "").split()]
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValueError("Expected a finite SVG viewBox")
    if bounds[2] <= 0 or bounds[3] <= 0:
        raise ValueError("SVG dimensions must be positive")
    for name in ("title", "desc"):
        if not any("".join(node.itertext()).strip()
                   for node in root.iter(f"{{{SVG_NS}}}{name}")):
            raise ValueError(f"Add Mermaid accessibility metadata: {name}")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in {"script", "foreignObject"}:
            raise ValueError("SVGs must not contain executable or HTML content")
        for key, value in node.attrib.items():
            local = key.rsplit("}", 1)[-1].lower()
            if local.startswith("on") or (local == "href" and not value.startswith("#")):
                raise ValueError("SVGs must be self-contained")
    root.set("width", f"{bounds[2]:g}")
    root.set("height", f"{bounds[3]:g}")
    ElementTree.register_namespace("", SVG_NS)
    ElementTree.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ElementTree.tostring(root, encoding="unicode") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mmdc", default="mmdc", help="Mermaid CLI 11.12.0 executable")
    parser.add_argument("--puppeteer-config", type=Path, help="Optional local Chromium configuration")
    parser.add_argument("--diagram", action="append", help="Build only this source stem; repeat for more")
    args = parser.parse_args()
    sources = {source.stem: source for source in sorted(SOURCES.glob("*.mmd"))}
    if args.diagram and set(args.diagram) - sources.keys():
        parser.error("Unknown diagram source")
    for name in dict.fromkeys(args.diagram or sources):
        with TemporaryDirectory(prefix="virt-report-diagram-") as temp:
            output = Path(temp) / f"{name}.svg"
            command = [args.mmdc, "--input", str(sources[name]), "--output", str(output),
                       "--configFile", str(SOURCES / "mermaid-config.json"),
                       "--backgroundColor", "transparent"]
            if args.puppeteer_config:
                command.extend(["--puppeteerConfigFile", str(args.puppeteer_config)])
            subprocess.run(command, check=True, timeout=90)
            # A failed render or validation leaves the previous checked asset intact.
            (ASSETS / f"{name}.svg").write_text(
                prepare_svg(output.read_text(encoding="utf-8")), encoding="utf-8",
            )


if __name__ == "__main__":
    main()

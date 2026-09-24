"""Render a private viewport JSON for offline visual inspection.

The input and output belong in gitignored data/generated; no location data is
embedded in this script or written to the repository history.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


def render(source: Path, output: Path) -> None:
    response = json.loads(source.read_text(encoding="utf-8"))
    west, south, east, north = response["coverageBounds"]
    panel_width = 900
    panel_height = 700
    margin = 22
    image = Image.new("RGB", (panel_width * 2, panel_height), "#101619")
    draw = ImageDraw.Draw(image)

    def project(vertex: list[object], panel: int) -> tuple[int, int]:
        longitude = float(vertex[1])
        latitude = float(vertex[2])
        return (
            round(panel * panel_width + margin + (longitude - west) / (east - west) * (panel_width - 2 * margin)),
            round(margin + (north - latitude) / (north - south) * (panel_height - 2 * margin)),
        )

    colors = {
        "ordinary": "#9be2cf",
        "sparse": "#758785",
        "high_speed": "#638196",
    }
    for panel in (0, 1):
        for piece in response["paths"]:
            movement_class = piece["movementClass"]
            if panel == 0 and movement_class != "ordinary":
                continue
            coordinates = [project(vertex, panel) for vertex in piece["vertices"]]
            if len(coordinates) >= 2:
                draw.line(coordinates, fill=colors[movement_class], width=1)
        if panel == 0:
            draw.text((margin, 5), "DEFAULT: ordinary", fill="#d3dfd9")
        else:
            draw.text(
                (panel_width + margin, 5),
                "ALL: ordinary + sparse + high speed",
                fill="#d3dfd9",
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.source, args.output)


if __name__ == "__main__":
    main()

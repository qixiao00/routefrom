"""Render a private viewport JSON for offline visual inspection.

The input and output belong in gitignored data/generated; no location data is
embedded in this script or written to the repository history.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw


def render(
    source: Path,
    output: Path,
    preview_source: Path | None = None,
    compare_legacy_gaps: bool = False,
) -> None:
    response = json.loads(source.read_text(encoding="utf-8"))
    west, south, east, north = response["coverageBounds"]
    panel_width = 900
    panel_height = 700
    margin = 22
    image = Image.new("RGB", (panel_width * 2, panel_height), "#101619")

    def project(vertex: list[object]) -> tuple[int, int]:
        longitude = float(vertex[1])
        latitude = float(vertex[2])
        return (
            round(margin + (longitude - west) / (east - west) * (panel_width - 2 * margin)),
            round(margin + (north - latitude) / (north - south) * (panel_height - 2 * margin)),
        )

    def draw_dashed(
        draw: ImageDraw.ImageDraw,
        start: tuple[int, int],
        end: tuple[int, int],
        color: str,
    ) -> None:
        length = math.dist(start, end)
        if length <= 0:
            return
        for offset in range(0, math.ceil(length), 7):
            begin = offset / length
            finish = min(1.0, (offset + 3) / length)
            draw.line((
                (round(start[0] + (end[0] - start[0]) * begin), round(start[1] + (end[1] - start[1]) * begin)),
                (round(start[0] + (end[0] - start[0]) * finish), round(start[1] + (end[1] - start[1]) * finish)),
            ), fill=color, width=1)

    inferred = []
    legacy_gaps = []
    if preview_source is not None:
        preview = json.loads(preview_source.read_text(encoding="utf-8"))
        inferred = [
            item for item in preview.get("inferredConnections", [])
            if item["displayable"] and item["kind"] == "straight_line_context"
        ]
        legacy_gaps = preview["gaps"] if compare_legacy_gaps else []

    colors = {
        "ordinary": "#9be2cf",
        "sparse": "#758785",
        "high_speed": "#638196",
    }
    for panel in (0, 1):
        panel_image = Image.new("RGB", (panel_width, panel_height), "#101619")
        draw = ImageDraw.Draw(panel_image)
        for piece in response["paths"]:
            movement_class = piece["movementClass"]
            if (panel == 0 or compare_legacy_gaps) and movement_class == "high_speed":
                continue
            coordinates = [project(vertex) for vertex in piece["vertices"]]
            if len(coordinates) >= 2:
                draw.line(coordinates, fill=colors[movement_class], width=1)
        for connection in inferred if panel == 0 or not compare_legacy_gaps else []:
            first = [None, *connection["startPosition"]]
            second = [None, *connection["endPosition"]]
            if (
                min(first[1], second[1]) > east or max(first[1], second[1]) < west or
                min(first[2], second[2]) > north or max(first[2], second[2]) < south
            ):
                continue
            draw_dashed(draw, project(first), project(second), "#75827e")
        if panel == 1 and compare_legacy_gaps:
            for gap in legacy_gaps:
                first = [None, *gap["startPosition"]]
                second = [None, *gap["endPosition"]]
                if (
                    min(first[1], second[1]) > east or max(first[1], second[1]) < west or
                    min(first[2], second[2]) > north or max(first[2], second[2]) < south
                ):
                    continue
                draw_dashed(draw, project(first), project(second), "#b28c81")
        if panel == 0:
            draw.text((margin, 5), "CURRENT: tracks + supported guesses", fill="#d3dfd9")
        else:
            draw.text(
                (margin, 5),
                "LEGACY: every gap drawn" if compare_legacy_gaps else "ALL: ordinary + sparse + high speed",
                fill="#d3dfd9",
            )
        image.paste(panel_image, (panel * panel_width, 0))

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--compare-legacy-gaps", action="store_true")
    args = parser.parse_args()
    if args.compare_legacy_gaps and args.preview is None:
        parser.error("--compare-legacy-gaps requires --preview")
    render(args.source, args.output, args.preview, args.compare_legacy_gaps)


if __name__ == "__main__":
    main()

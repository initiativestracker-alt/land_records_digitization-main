"""Non-LLM evidence: OCR disagreement, uncovered text, and deletion geometry."""

import csv
import io
import math
from pathlib import Path
import subprocess

import cv2
import numpy as np


def rectangle(box):
    if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
        raise ValueError("Region needs four finite coordinates")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        raise ValueError("Region must be within the page (0–1000)")
    return [float(v) for v in box]


def inside(word_box, region):
    x0, y0, x1, y1 = word_box
    rx0, ry0, rx1, ry1 = region
    area = max(1e-6, (x1 - x0) * (y1 - y0))
    overlap = max(0, min(x1, rx1) - max(x0, rx0)) * max(0, min(y1, ry1) - max(y0, ry0))
    return overlap / area >= 0.6


def render_page(pdf, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-singlefile", "-r", "200", "-png", str(pdf),
                    str(destination.with_suffix(""))], check=True, capture_output=True, timeout=120)


def tesseract_page(path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("Page image cannot be decoded")
    height, width = image.shape[:2]
    result = subprocess.run(["tesseract", str(path), "stdout", "--psm", "3", "tsv"],
                            capture_output=True, text=True, check=True, timeout=120)
    words = []
    # QUOTE_NONE is essential: an OCR quote is text, not a TSV quoting delimiter.
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        if not row["text"].strip() or float(row["conf"]) < 0:
            continue
        x, y, w, h = (int(row[k]) for k in ["left", "top", "width", "height"])
        words.append({"text": row["text"], "score": float(row["conf"]) / 100,
                      "box": [x / width * 1000, y / height * 1000,
                              (x + w) / width * 1000, (y + h) / height * 1000]})
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Blank detection uses ink plus OCR, never an OCR-empty response alone.
    _, ink = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY_INV)
    interior = ink[int(height * .03):int(height * .97), int(width * .03):int(width * .97)]
    density = float(np.mean(interior > 0))
    return {"width": width, "height": height, "words": words,
            "ink_density": density, "blank": density < .0003 and not words,
            "laplacian_variance": float(cv2.Laplacian(gray, cv2.CV_64F).var())}


def deletion_geometry(path, regions, words):
    image = cv2.imread(str(path))
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 65, 60), (12, 255, 255)) | cv2.inRange(hsv, (165, 65, 60), (180, 255, 255))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Red marks are common; also inspect dark strokes for monochrome deletions.
    edges = cv2.Canny(gray, 70, 180)
    findings = []
    for region in regions:
        x0, y0, x1, y1 = rectangle(region["box"])
        px0, py0, px1, py1 = int(x0 * width / 1000), int(y0 * height / 1000), int(x1 * width / 1000), int(y1 * height / 1000)
        area_words = [w for w in words if inside(w["box"], region["box"])]
        for label, mask in [("red", red), ("dark", edges)]:
            crop = mask[py0:py1, px0:px1]
            lines = cv2.HoughLinesP(crop, 1, np.pi / 180, threshold=45,
                                   minLineLength=max(40, int((px1 - px0) * .3)), maxLineGap=12)
            if lines is None:
                continue
            for line in lines.reshape(-1, 4)[:100]:
                lx0, ly0, lx1, ly1 = line
                gx0, gy0, gx1, gy1 = lx0 + px0, ly0 + py0, lx1 + px0, ly1 + py0
                if gx1 == gx0 or abs((gy1 - gy0) / (gx1 - gx0)) > .65:
                    continue
                crossed = 0
                for word in area_words:
                    wx0, wy0, wx1, wy1 = word["box"]
                    middle = (wx0 + wx1) / 2 * width / 1000
                    if min(gx0, gx1) <= middle <= max(gx0, gx1):
                        at_y = gy0 + (gy1 - gy0) * (middle - gx0) / (gx1 - gx0)
                        # Mid-glyph crossings exclude most underlines and table borders.
                        crossed += (wy0 + (wy1 - wy0) * .2) * height / 1000 <= at_y <= (wy1 - (wy1 - wy0) * .2) * height / 1000
                if crossed >= 2:
                    findings.append({"page": region["page"], "colour": label,
                                     "crossed_words": int(crossed),
                                     "line": [gx0 / width * 1000, gy0 / height * 1000,
                                              gx1 / width * 1000, gy1 / height * 1000]})
    return findings[:30]


def entry_signals(entry, pages, page_directory):
    tess, paddle = [], []
    strokes = []
    for region in entry["regions"]:
        page = pages[region["page"] - 1]
        tw = [w for w in page["tesseract"]["words"] if inside(w["box"], region["box"])]
        pw = [w for w in page.get("paddle", {}).get("words", []) if inside(w["box"], region["box"])]
        tess.extend(tw); paddle.extend(pw)
        strokes.extend(deletion_geometry(Path(page_directory) / f"page-{region['page']:04d}.png", [region], tw + pw))
    from difflib import SequenceMatcher
    normal = lambda text: " ".join(text.upper().split())
    a, b = normal(" ".join(w["text"] for w in tess)), normal(" ".join(w["text"] for w in paddle))
    scores = [w["score"] for w in tess + paddle]
    return {"ocr_word_count": len(tess), "paddle_word_count": len(paddle),
            "ocr_score_p10": float(np.quantile(scores, .1)) if scores else None,
            "ocr_agreement": SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else None,
            "deletion_strokes": strokes,
            "cancelled_text_detected": "CANCELLED" in a or "CANCELLED" in b,
            "continued_notice": "FOLIO CONTINUATION" in a or "FOLIO CONTINUATION" in b}


def uncovered(pages, entries, excluded):
    issues = []
    for number, page in enumerate(pages, 1):
        if page["tesseract"]["blank"]:
            continue
        regions = [r["box"] for e in entries for r in e["regions"] if r["page"] == number]
        regions += [r["box"] for r in excluded if r["page"] == number]
        words = page["tesseract"]["words"] + page.get("paddle", {}).get("words", [])
        remaining = [w for w in words if not any(inside(w["box"], box) for box in regions)]
        if len(remaining) >= 5:
            issues.append({"code": "UNASSIGNED_TEXT", "page": number, "words": len(remaining),
                           "boxes": [w["box"] for w in remaining],
                           "message": "Text lies outside the proposed entries and excluded page furniture."})
    return issues

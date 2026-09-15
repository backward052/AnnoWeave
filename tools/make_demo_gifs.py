"""Regenerate the README demo animation.

    python tools/make_demo_gifs.py

This is a maintainer tool, not part of the shipped package. It renders the README
animation into ``docs/assets/`` so the GIF is reproducible instead of being a
hand-made binary nobody can update.

Three design rules:

1. **No media and no model weights.** The scene is drawn procedurally with OpenCV, so
   the repository stays inside its public boundary (no footage, no weights).
2. **The association decision is real.** Which marker pairs with which container, and
   therefore which containers get cropped, comes from running AnnoWeave's own
   :class:`GenericAssociateNode` on synthetic detections. If the node's semantics
   change, the regenerated animation changes with them.
3. **Staleness is detectable.** The generator is deterministic and records a digest of
   its inputs in ``docs/assets/demo.json``. ``tests/test_demo_assets.py`` fails when the
   committed GIF no longer matches what this script would produce.

Requires Pillow, which is in the ``dev`` extra (GIF encoding is not needed otherwise).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from annoweave.inference.datatypes import Detection, Frame, ModelResult, TaskType  # noqa: E402
from annoweave.ui.theme import TOKENS  # noqa: E402
from annoweave.workflow.association import GenericAssociateNode  # noqa: E402
from annoweave.workflow.packet import Packet  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "docs" / "assets"
GIF_NAME = "workflow-associate-crop"

# Read straight from the application theme so the animation cannot drift from the UI.
BRAND = TOKENS["brand"]
ACCENT = TOKENS["brand_accent"]
SURFACE = TOKENS["surface"]
PANEL = TOKENS["panel"]
TEXT = TOKENS["text"]
TEXT_MUTED = TOKENS["text_muted"]
BORDER = TOKENS["border"]
PENDING = TOKENS["state_pending"]
CONFIRMED = TOKENS["state_confirmed"]
REJECTED = TOKENS["state_rejected"]

# Canvas colours in OpenCV's BGR order.
CANVAS_BG = (30, 26, 22)
SUBJECT_COLOR = (191, 140, 76)
CANDIDATE_COLOR = (200, 200, 200)
MATCH_COLOR = (154, 185, 25)
MUTED_BOX = (86, 86, 92)


def bgr(hex_colour: str) -> tuple[int, int, int]:
    """Convert a ``#RRGGBB`` theme token into the BGR tuple OpenCV expects."""
    red, green, blue = (int(hex_colour[index : index + 2], 16) for index in (1, 3, 5))
    return (blue, green, red)


ACCENT_BGR = bgr(ACCENT)
MUTED_BOX = bgr("#5A5A60")
REJECTED_BGR = bgr(REJECTED)

SCENE_WIDTH, SCENE_HEIGHT = 560, 360
CANVAS_WIDTH = 1000
HEADER_HEIGHT = 46
STRIP_HEIGHT = 34
CAPTION_HEIGHT = 38
FOOTER_HEIGHT = 26
PANEL_GAP = 24
CANVAS_HEIGHT = HEADER_HEIGHT + STRIP_HEIGHT + SCENE_HEIGHT + CAPTION_HEIGHT + FOOTER_HEIGHT

FRAME_MS = 250
#: GIF palette size. 48 keeps text crisp while holding the file near 1.9 MB; GitHub
#: scales the image down to the README column, so staying at full width avoids blur.
GIF_COLORS = 48
HOLD_FRAMES = 7
TRANSITION_FRAMES = 4
#: Frames a staggered reveal takes to complete within one stage.
REVEAL_FRAMES = 4
#: Source stage: a playhead sweeps the frame so stage 1 is not a still image.
SOURCE_SWEEP_FRAMES = TRANSITION_FRAMES + HOLD_FRAMES

STAGES = (
    ("source", "Source", "Sampled video frame, nothing inferred yet.", "视频采样帧，尚未推理。"),
    ("detect", "Detect", "Two full-frame models run on the same frame.", "两个整图模型在同一帧上推理。"),
    (
        "associate",
        "Associate",
        "Each marker is matched to the container whose box holds its center.",
        "按中心点落框判定标记所属的容器。",
    ),
    ("crop", "Crop", "Only containers that matched a marker are cropped.", "只裁剪命中标记的容器。"),
    (
        "downstream",
        "Downstream",
        "A classifier scores each crop, ready for review.",
        "分类模型对每张裁剪图打分，随后进入复核。",
    ),
)

# Synthetic scene. The third container and the bottom-right marker are deliberate
# non-matches, so the animation shows a real decision rather than "everything matches".
# Layout constraints, each learned from a rendering bug:
#   * every box keeps >= 8 px from the scene border, because the crop stage draws its
#     brackets 5 px outside the detection;
#   * no box may overlap another box's label strip (the 20 px above its top edge), or the
#     dimmed overlay would cover that label.
SUBJECTS = (
    Detection(46, 62, 232, 250, "package", 0.93),
    Detection(300, 96, 512, 316, "package", 0.89),
    # Upper right. Its label strip must clear package 0's box and package 1's label.
    Detection(464, 36, 552, 126, "crate", 0.81),
)
CANDIDATES = (
    Detection(96, 118, 140, 160, "label", 0.87),
    Detection(374, 190, 430, 240, "seal", 0.84),
    # Sits in the gap between the two containers, so the rule correctly rejects it.
    Detection(236, 296, 292, 336, "tag", 0.62),
)

#: Reveal order: the two containers, then the crate. Markers: the two matches, then the reject.
SUBJECT_REVEAL_ORDER = (0, 1, 2)
CANDIDATE_REVEAL_ORDER = (0, 1, 2)

CLASSIFIER_VERDICTS = (("ok", 0.94), ("damaged", 0.77), ("ok", 0.88))


# --------------------------------------------------------------------------- model


def association_pairs() -> list[tuple[int, int]]:
    """Return ``(subject_index, candidate_index)`` pairs using the real node."""
    packet = Packet(
        frame=Frame(0, 0.0, np.zeros((SCENE_HEIGHT, SCENE_WIDTH, 3), dtype=np.uint8)),
        full_results={
            "subject": ModelResult(TaskType.DETECTION, list(SUBJECTS)),
            "candidate": ModelResult(TaskType.DETECTION, list(CANDIDATES)),
        },
    )
    result = GenericAssociateNode(
        {
            "left_key": "subject",
            "right_key": "candidate",
            "metric": "center_inside",
            "threshold": 1.0,
            "expand_ratio": 0.0,
        }
    ).run(packet)
    return sorted(
        (SUBJECTS.index(association.subject), CANDIDATES.index(candidate))
        for association in result.associations
        for candidate in association.matched_candidates
    )


PAIRS = association_pairs()
MATCHED_SUBJECTS = sorted({subject for subject, _ in PAIRS})
#: Candidates that the rule rejected, shown as crossed out during the associate stage.
REJECTED_CANDIDATES = sorted({index for index in range(len(CANDIDATES))} - {candidate for _, candidate in PAIRS})

# Guards for the demo's teaching value. Moving a box must not silently turn the
# animation into "everything matches" (which is what happened once already).
if len(MATCHED_SUBJECTS) < 2:
    raise SystemExit(
        f"demo scene is wrong: only {len(MATCHED_SUBJECTS)} container(s) matched a marker. "
        "Place at least two markers inside different containers."
    )
if not REJECTED_CANDIDATES:
    raise SystemExit(
        "demo scene is wrong: every marker matched a container, so the animation no longer "
        "shows the rule rejecting anything. Move one marker into the gap between containers."
    )
if len(MATCHED_SUBJECTS) == len(SUBJECTS):
    raise SystemExit(
        "demo scene is wrong: every container matched, so the crop stage no longer shows a "
        "container being skipped."
    )


# Rendering constraints, checked here so a box move fails loudly instead of silently
# producing an unreadable frame. Both were violated during development.
LABEL_STRIP = 20  #: vertical band above a box top edge where its label is drawn
CROP_PAD = 5  #: how far the crop brackets extend outside a detection
MIN_BORDER_MARGIN = CROP_PAD + 3


def _validate_layout() -> None:
    boxes = [(det, "subject") for det in SUBJECTS] + [(det, "candidate") for det in CANDIDATES]
    for det, kind in boxes:
        if (
            det.x1 - CROP_PAD < 0
            or det.y1 - CROP_PAD < 0
            or det.x2 + CROP_PAD > SCENE_WIDTH
            or det.y2 + CROP_PAD > SCENE_HEIGHT
        ):
            raise SystemExit(
                f"demo scene is wrong: {kind} {det.label!r} at "
                f"({det.x1:.0f},{det.y1:.0f},{det.x2:.0f},{det.y2:.0f}) is flush with the "
                f"scene border, so its crop brackets would be clipped. Keep at least "
                f"{MIN_BORDER_MARGIN} px of margin."
            )

    # A box must not intrude into another box's rendered label, or the dimmed overlay
    # would paint over that label in the crop stage. The strip is the actual text extent.
    for index, det in enumerate(SUBJECTS):
        text = f"{det.label} {det.score:.2f}"
        width = text_width(text)
        fits = int(det.x1) + 4 + width <= int(det.x2) - 16
        label_x = int(det.x1) + 4 if fits else int(det.x2) - 12 - width
        strip = (label_x, det.y1 - LABEL_STRIP, label_x + width, det.y1)
        for other_index, other in enumerate(SUBJECTS):
            if index == other_index:
                continue
            if (
                strip[2] > other.x1
                and strip[0] < other.x2
                and strip[3] > other.y1
                and strip[1] < other.y2
            ):
                raise SystemExit(
                    f"demo scene is wrong: container {other.label!r} at index {other_index} overlaps the "
                    f"label of container {det.label!r} at index {index} (label spans x "
                    f"{strip[0]:.0f}-{strip[2]:.0f}, y {strip[1]:.0f}-{strip[3]:.0f}), so muting it would "
                    "hide that label. Move one of them."
                )




# --------------------------------------------------------------------------- timing

#: ``(stage index, stage progress, absolute frame number)`` for every rendered frame.
STAGES_PLAN: list[tuple[int, float, int]] = []
_frame_number = 0
for _stage in range(len(STAGES)):
    for _step in range(1, TRANSITION_FRAMES + 1):
        STAGES_PLAN.append((_stage, _step / TRANSITION_FRAMES, _frame_number))
        _frame_number += 1
    for _ in range(HOLD_FRAMES):
        STAGES_PLAN.append((_stage, 1.0, _frame_number))
        _frame_number += 1


# --------------------------------------------------------------------------- fonts


def _font(size: int, *, bold: bool = False):
    names = ("msyhbd.ttc", "segoeuib.ttf") if bold else ("msyh.ttc", "segoeui.ttf")
    for name in names:
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default()


def text_width(text: str) -> float:
    """Rendered width in pixels, using the same metrics OpenCV Hershey fonts use."""
    (width, _height), _baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, 1)
    return width


#: Font scale used for detection labels; the layout validator needs the same value.
LABEL_SCALE = 0.40

FONT_TITLE = _font(17, bold=True)
FONT_STAGE = _font(13)
FONT_BODY = _font(13)
FONT_SMALL = _font(11)
FONT_TINY = _font(10)

# The layout validator renders label metrics, so it runs once those helpers exist.
_validate_layout()


# --------------------------------------------------------------------------- helpers


def blend(base: np.ndarray, overlay: np.ndarray, alpha: float) -> np.ndarray:
    alpha = max(0.0, min(1.0, alpha))
    return cv2.addWeighted(base, 1.0 - alpha, overlay, alpha, 0.0)


def reveal(progress: float, slot: int, total: int) -> float:
    """Staggered 0..1 reveal for element ``slot`` of ``total`` within one stage."""
    span = 1.0 / max(total, 1)
    return max(0.0, min(1.0, (progress - slot * span) / span))


def to_pil(image: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def scene_image() -> np.ndarray:
    """Procedural frame: floor, shelf lines, mild noise. No real footage involved."""
    rng = np.random.default_rng(20260915)
    canvas = np.full((SCENE_HEIGHT, SCENE_WIDTH, 3), CANVAS_BG, dtype=np.uint8)
    lower = canvas[206:, :].astype(np.int16)
    canvas[206:, :] = np.clip(lower + rng.integers(0, 9, size=lower.shape, dtype=np.int16), 0, 255).astype(np.uint8)
    for x in range(0, SCENE_WIDTH, 56):
        cv2.line(canvas, (x, 202), (x, SCENE_HEIGHT), (44, 38, 32), 1)
    cv2.line(canvas, (0, 204), (SCENE_WIDTH, 204), (54, 46, 38), 2)
    cv2.line(canvas, (0, 118), (SCENE_WIDTH, 118), (36, 31, 26), 1)
    return canvas


def draw_box(image: np.ndarray, det: Detection, colour, thickness: int) -> None:
    """Box outline only, matching AnnoWeave's DrawNode rectangle style."""
    cv2.rectangle(
        image,
        (int(round(det.x1)), int(round(det.y1))),
        (int(round(det.x2)), int(round(det.y2))),
        colour,
        thickness,
    )


def draw_label(image: np.ndarray, det: Detection, colour, *, align: str = "left") -> None:
    """Detection label, drawn in a final pass so nothing painted later can hide it.

    Labels sit just above the box. ``align`` picks which end of the box the text is
    anchored to: long text is anchored right so it overhangs leftwards instead of
    colliding with the crop bracket that sits outside the right edge.
    """
    text = f"{det.label} {det.score:.2f}"
    width = text_width(text)
    if align == "right":
        x = int(det.x2) - 12 - width
    else:
        x = int(det.x1) + 4
    cv2.putText(
        image,
        text,
        (x, max(11, int(det.y1) - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        LABEL_SCALE,
        colour,
        1,
        cv2.LINE_AA,
    )


def draw_cross(image: np.ndarray, det: Detection, colour=REJECTED_BGR) -> None:
    """Mark a rejected candidate with a dashed cross through its centre."""
    cx = int((det.x1 + det.x2) / 2)
    cy = int((det.y1 + det.y2) / 2)
    half = 11
    for offset in range(-half, half, 6):
        cv2.line(image, (cx + offset, cy + offset), (cx + offset + 4, cy + offset + 4), colour, 1, cv2.LINE_AA)
        cv2.line(image, (cx + offset, cy - offset), (cx + offset + 4, cy - offset - 4), colour, 1, cv2.LINE_AA)


def draw_crop_marker(image: np.ndarray, det: Detection, pad: int = 5) -> None:
    """Corner brackets marking the crop region.

    The top-left corner is deliberately skipped: that is where the detection label sits,
    and a bracket there makes the text unreadable.
    """
    for corner, (dx, dy) in (
        ((int(det.x2) + pad, int(det.y1) - pad), (-1, 1)),
        ((int(det.x1) - pad, int(det.y2) + pad), (1, -1)),
        ((int(det.x2) + pad, int(det.y2) + pad), (-1, -1)),
    ):
        cv2.line(image, corner, (corner[0] + dx * 18, corner[1]), ACCENT_BGR, 2, cv2.LINE_AA)
        cv2.line(image, corner, (corner[0], corner[1] + dy * 18), ACCENT_BGR, 2, cv2.LINE_AA)


def mute_region(image: np.ndarray, det: Detection, strength: float = 0.35) -> np.ndarray:
    """Dim a box that did not qualify, using an outline rather than a solid fill.

    A filled rectangle would paint over detection labels drawn by earlier stages (that is
    how the second container's label got obscured once already). Mutates ``image`` and
    returns it, so call it on the image that is actually being composited.
    """
    x1, y1 = int(det.x1), int(det.y1)
    x2, y2 = int(det.x2), int(det.y2)
    dimmed = image.copy()
    cv2.rectangle(dimmed, (x1, y1), (x2, y2), MUTED_BOX, -1)
    image[:, :] = blend(image, dimmed, strength)
    cv2.rectangle(image, (x1, y1), (x2, y2), MUTED_BOX, 1, cv2.LINE_AA)
    return image


# --------------------------------------------------------------------------- canvas


def render_canvas(stage_index: int, progress: float, frame_number: int) -> np.ndarray:
    """Render the annotated scene for one frame. Deterministic given its arguments.

    Draw order matters and is deliberate: scene, then boxes and links, then the
    mute/crop overlays, and labels last so no later pass can hide a label.
    """
    image = scene_image()

    # Stage 0: a playhead sweeps the frame so the opening is not a still image.
    if stage_index == 0:
        sweep = (frame_number % SOURCE_SWEEP_FRAMES) / max(1, SOURCE_SWEEP_FRAMES - 1)
        overlay = image.copy()
        x = int(sweep * (SCENE_WIDTH - 1))
        cv2.line(overlay, (x, 0), (x, SCENE_HEIGHT), ACCENT_BGR, 2, cv2.LINE_AA)
        cv2.rectangle(overlay, (0, SCENE_HEIGHT - 5), (max(x, 1), SCENE_HEIGHT), ACCENT_BGR, -1)
        image = blend(image, overlay, 1.0)
        return image

    detections = [(SUBJECTS[i], SUBJECT_COLOR, 2) for i in SUBJECT_REVEAL_ORDER]
    detections += [(CANDIDATES[i], CANDIDATE_COLOR, 1) for i in CANDIDATE_REVEAL_ORDER]
    total = len(detections)
    revealed = [
        (det, colour, thickness)
        for slot, (det, colour, thickness) in enumerate(detections)
        if stage_index > 1 or reveal(progress, slot, total) >= 1.0
    ]

    # Boxes first.
    overlay = image.copy()
    for det, colour, thickness in revealed:
        draw_box(overlay, det, colour, thickness)
    image = blend(image, overlay, 1.0)

    # Association links and matched-box highlighting.
    if stage_index >= 2:
        overlay = image.copy()
        for slot, (subject_index, candidate_index) in enumerate(PAIRS):
            if stage_index == 2 and reveal(progress, slot, len(PAIRS)) < 1.0:
                continue
            subject, candidate = SUBJECTS[subject_index], CANDIDATES[candidate_index]
            draw_box(overlay, subject, ACCENT_BGR, 2)
            draw_box(overlay, candidate, MATCH_COLOR, 2)
            anchor = (int((subject.x1 + subject.x2) / 2), int((subject.y1 + subject.y2) / 2))
            centre = (int((candidate.x1 + candidate.x2) / 2), int((candidate.y1 + candidate.y2) / 2))
            cv2.line(overlay, anchor, centre, ACCENT_BGR, 1, cv2.LINE_AA)
            cv2.circle(overlay, centre, 3, ACCENT_BGR, -1, cv2.LINE_AA)
        image = blend(image, overlay, 1.0)
        if stage_index >= 3:
            overlay = image.copy()
            for index in REJECTED_CANDIDATES:
                draw_cross(overlay, CANDIDATES[index], REJECTED_BGR)
            image = blend(image, overlay, 1.0)

    # Crop regions and the muted container that did not qualify.
    if stage_index >= 3:
        overlay = image.copy()
        for slot, index in enumerate(MATCHED_SUBJECTS):
            if stage_index == 3 and reveal(progress, slot, len(MATCHED_SUBJECTS)) < 1.0:
                continue
            draw_crop_marker(overlay, SUBJECTS[index])
        image = blend(image, overlay, 1.0)
        for index in range(len(SUBJECTS)):
            if index not in MATCHED_SUBJECTS:
                mute_region(image, SUBJECTS[index])

    # Labels last: nothing drawn above can obscure them now. Long text is anchored to the
    # right of the box so it overhangs leftwards rather than into the crop bracket.
    for det, colour, _thickness in revealed:
        text = f"{det.label} {det.score:.2f}"
        align = "left" if int(det.x1) + 4 + text_width(text) <= int(det.x2) - 16 else "right"
        draw_label(image, det, colour, align=align)

    return image


# --------------------------------------------------------------------------- chrome


def rounded_rect(draw, box, radius: int, *, fill=None, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def visible_crops(stage_index: int, progress: float) -> int:
    """How many crop cards the side panel shows for this frame."""
    if stage_index < 3:
        return 0
    total = len(MATCHED_SUBJECTS)
    if stage_index == 3:
        return sum(1 for slot in range(total) if reveal(progress, slot, total) >= 1.0)
    if stage_index == 4:
        return sum(1 for slot in range(total) if reveal(progress, slot, total) >= 1.0)
    return total


def render_frame(stage_index: int, progress: float, frame_number: int) -> Image.Image:
    """Compose the full review-workspace-like canvas."""
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), SURFACE)
    draw = ImageDraw.Draw(canvas)

    # Header
    draw.rectangle((0, 0, CANVAS_WIDTH, HEADER_HEIGHT), fill=BRAND)
    draw.rectangle((0, HEADER_HEIGHT - 2, CANVAS_WIDTH, HEADER_HEIGHT), fill=ACCENT)
    draw.text((20, 13), "AnnoWeave", font=FONT_TITLE, fill=TEXT)
    draw.text((128, 17), "review workspace", font=FONT_STAGE, fill=TEXT_MUTED)
    draw.text((CANVAS_WIDTH - 236, 17), "Media 1/1   ·   Frame 0   ·   zh / en", font=FONT_SMALL, fill=TEXT_MUTED)

    # Stage strip
    strip_top = HEADER_HEIGHT
    draw.rectangle((0, strip_top, CANVAS_WIDTH, strip_top + STRIP_HEIGHT), fill=PANEL)
    chip_x = 20
    for index, (_key, label, _en, _zh) in enumerate(STAGES):
        text = f"{index + 1}  {label}"
        width = int(draw.textlength(text, font=FONT_STAGE)) + 22
        if index < stage_index:
            fill, outline, colour = (28, 58, 50), ACCENT, ACCENT
        elif index == stage_index:
            fill, outline, colour = (54, 42, 16), PENDING, PENDING
        else:
            fill, outline, colour = PANEL, BORDER, TEXT_MUTED
        rounded_rect(draw, (chip_x, strip_top + 6, chip_x + width, strip_top + 28), 5, fill=fill, outline=outline)
        draw.text((chip_x + 11, strip_top + 10), text, font=FONT_STAGE, fill=colour)
        chip_x += width + 9

    # Scene
    scene_top = strip_top + STRIP_HEIGHT
    scene = render_canvas(stage_index, progress, frame_number)
    canvas.paste(to_pil(scene), (20, scene_top))
    rounded_rect(draw, (20, scene_top, 20 + SCENE_WIDTH, scene_top + SCENE_HEIGHT), 6, outline=BORDER)

    # Side panel: crops plus downstream verdicts
    panel_left = SCENE_WIDTH + 20 + PANEL_GAP
    panel_right = CANVAS_WIDTH - 20
    panel_width = panel_right - panel_left
    draw.text((panel_left, scene_top + 2), "Crops", font=FONT_BODY, fill=TEXT)
    draw.text((panel_left, scene_top + 20), f"{len(MATCHED_SUBJECTS)} of {len(SUBJECTS)} containers matched",
              font=FONT_TINY, fill=TEXT_MUTED)

    show = visible_crops(stage_index, progress)
    thumb_top = scene_top + 42
    for order, subject_index in enumerate(MATCHED_SUBJECTS):
        if order >= show:
            break
        subject = SUBJECTS[subject_index]
        thumb = scene[int(subject.y1) : int(subject.y2), int(subject.x1) : int(subject.x2)]
        if thumb.size == 0:
            continue
        thumb_h = 76
        thumb_w = int(thumb.shape[1] * thumb_h / thumb.shape[0])
        thumb_w = min(thumb_w, panel_width - 96)
        thumb_h = int(thumb.shape[0] * thumb_w / thumb.shape[1])
        resized = cv2.resize(thumb, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        canvas.paste(to_pil(resized), (panel_left, thumb_top))
        rounded_rect(draw, (panel_left, thumb_top, panel_left + thumb_w, thumb_top + thumb_h), 4,
                     outline=BORDER, width=1)
        draw.text((panel_left + thumb_w + 10, thumb_top + 4), f"crop {order + 1}", font=FONT_SMALL, fill=TEXT)
        if stage_index >= 4:
            label, score = CLASSIFIER_VERDICTS[order % len(CLASSIFIER_VERDICTS)]
            colour = CONFIRMED if label == "ok" else REJECTED
            draw.text((panel_left + thumb_w + 10, thumb_top + 22), f"{label} {score:.2f}", font=FONT_SMALL, fill=colour)
        else:
            draw.text((panel_left + thumb_w + 10, thumb_top + 22), "pending", font=FONT_SMALL, fill=TEXT_MUTED)
        thumb_top += thumb_h + 12

    if stage_index < 2:
        draw.text((panel_left, thumb_top + 4), "Nothing cropped yet.", font=FONT_SMALL, fill=TEXT_MUTED)
        draw.text((panel_left, thumb_top + 20), "Association decides", font=FONT_SMALL, fill=TEXT_MUTED)
        draw.text((panel_left, thumb_top + 36), "which boxes qualify.", font=FONT_SMALL, fill=TEXT_MUTED)
    elif stage_index == 2:
        draw.text((panel_left, thumb_top + 4), f"{len(PAIRS)} pairs matched,", font=FONT_SMALL, fill=ACCENT)
        draw.text((panel_left, thumb_top + 20), f"{len(REJECTED_CANDIDATES)} marker rejected.", font=FONT_SMALL, fill=REJECTED)

    # Caption
    caption_top = scene_top + SCENE_HEIGHT
    draw.text((20, caption_top + 8), STAGES[stage_index][2], font=FONT_BODY, fill=TEXT)
    draw.text((20, caption_top + 25), STAGES[stage_index][3], font=FONT_SMALL, fill=TEXT_MUTED)

    # Footer
    footer_top = CANVAS_HEIGHT - FOOTER_HEIGHT
    draw.rectangle((0, footer_top, CANVAS_WIDTH, CANVAS_HEIGHT), fill=PANEL)
    draw.text((20, footer_top + 6), f"stage {stage_index + 1}/{len(STAGES)} · {STAGES[stage_index][1]}",
              font=FONT_TINY, fill=TEXT_MUTED)
    draw.text((CANVAS_WIDTH - 268, footer_top + 6), "synthetic demo · no weights, no footage",
              font=FONT_TINY, fill=TEXT_MUTED)
    return canvas


# --------------------------------------------------------------------------- output


def build_gif(path: Path) -> dict:
    frames = [render_frame(stage, progress, number) for stage, progress, number in STAGES_PLAN]
    palette = [frame.convert("P", palette=Image.ADAPTIVE, colors=GIF_COLORS) for frame in frames]
    palette[0].save(
        path,
        save_all=True,
        append_images=palette[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )
    reloaded = Image.open(path)
    actual_frames = 0
    while True:
        try:
            reloaded.seek(actual_frames)
        except EOFError:
            break
        actual_frames += 1
    return {
        "file": path.name,
        "width": frames[0].width,
        "height": frames[0].height,
        "rendered_frames": len(frames),
        "encoded_frames": actual_frames,
        "bytes": path.stat().st_size,
        "association_pairs": PAIRS,
        "matched_subjects": MATCHED_SUBJECTS,
        "rejected_candidates": REJECTED_CANDIDATES,
    }


def source_digest() -> str:
    """Digest of everything that determines the rendered output."""
    payload = json.dumps(
        {
            "subjects": [(d.x1, d.y1, d.x2, d.y2, d.label, d.score) for d in SUBJECTS],
            "candidates": [(d.x1, d.y1, d.x2, d.y2, d.label, d.score) for d in CANDIDATES],
            "stages": STAGES,
            "pairs": PAIRS,
            "plan": len(STAGES_PLAN),
            "scene": [SCENE_WIDTH, SCENE_HEIGHT],
            "canvas": [CANVAS_WIDTH, CANVAS_HEIGHT],
            "frame_ms": FRAME_MS,
            "verdicts": CLASSIFIER_VERDICTS,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{GIF_NAME}.gif"
    info = build_gif(target)
    manifest = {"digest": source_digest(), "assets": [info]}
    (OUTPUT_DIR / "demo.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"  wrote {target.relative_to(REPO_ROOT)}")
    print(f"    {info['bytes'] / 1024:.0f} KB  {info['width']}x{info['height']}")
    print(f"    rendered {info['rendered_frames']} frames, GIF stores {info['encoded_frames']}")
    print(f"    association pairs {PAIRS}  matched subjects {MATCHED_SUBJECTS}")
    print(f"    rejected candidates {REJECTED_CANDIDATES}")
    print(f"  digest {manifest['digest'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

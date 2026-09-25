import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))


def face_blob(cx: float, cy: float, r: float) -> np.ndarray:
    img = np.full((480, 640, 3), 30, dtype=np.uint8)

    cv2.ellipse(
        img, (int(cx), int(cy)), (int(r), int(r * 1.25)), 0, 0, 360, (210, 180, 155), -1
    )
    cv2.ellipse(
        img, (int(cx), int(cy)), (int(r), int(r * 1.25)), 0, 0, 360, (190, 160, 135), -1
    )

    for sx in (-1, 1):
        cv2.ellipse(
            img,
            (int(cx + sx * r * 0.38), int(cy - r * 0.42)),
            (int(r * 0.16), int(r * 0.05)),
            0,
            180,
            360,
            (60, 70, 90),
            -1,
        )

    for sx in (-1, 1):
        ex, ey = int(cx + sx * r * 0.38), int(cy - r * 0.22)
        cv2.ellipse(
            img,
            (ex, ey),
            (int(r * 0.14), int(r * 0.08)),
            0,
            0,
            360,
            (245, 245, 245),
            -1,
        )
        cv2.circle(img, (ex, ey), int(r * 0.05), (40, 40, 40), -1)

    cv2.circle(img, (int(cx), int(cy + r * 0.12)), int(r * 0.07), (160, 135, 115), -1)

    cv2.ellipse(
        img,
        (int(cx), int(cy + r * 0.55)),
        (int(r * 0.24), int(r * 0.1)),
        0,
        0,
        180,
        (120, 90, 110),
        -1,
    )
    return img


def two_face_blob() -> np.ndarray:
    img = np.full((480, 640, 3), 30, dtype=np.uint8)
    for cx in (160, 480):
        one = face_blob(cx, 240, 90)
        mask = one.sum(axis=2) > 90
        img[mask] = one[mask]
    return img


def face_synth() -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", two_face_blob())
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    bx1, by1, bx2, by2 = 399, 144, 563, 350
    m, s = 100, 1.5
    h, w = img.shape[:2]
    crop = img[max(0, by1 - m) : min(h, by2 + m), max(0, bx1 - m) : min(w, bx2 + m)]
    return cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)


def write_live_sequence(source: Path, out_dir: Path) -> None:
    from helpers import make_live_jpegs

    _write_frames(out_dir, make_live_jpegs(source.read_bytes()))


def sequence_dir(action: str) -> str:
    return f"live_{action.split('_')[1].lower()}"


E2E_APPROACH_RATIO = 1.6


def write_action_sequences(source: Path, base_dir: Path) -> None:
    from app import challenge
    from helpers import make_action_jpegs, settle_frames

    hold = settle_frames(challenge.SETTLE_MS_MAX)
    for action in (challenge.MOVE_CLOSER, challenge.LOOK_LEFT, challenge.LOOK_RIGHT):
        _write_frames(
            base_dir / sequence_dir(action),
            make_action_jpegs(
                source.read_bytes(), action, hold=hold, ratio=E2E_APPROACH_RATIO
            ),
        )


def _write_frames(out_dir: Path, jpegs: list) -> None:
    out_dir.mkdir(exist_ok=True)
    for i, jpeg in enumerate(jpegs):
        (out_dir / f"frame_{i:02d}.jpg").write_bytes(jpeg)


def main() -> None:
    cv2.imwrite(str(HERE / "face_like.jpg"), face_blob(320, 240, 130))
    cv2.imwrite(str(HERE / "face_like_small.jpg"), face_blob(320, 240, 60))
    cv2.imwrite(str(HERE / "two_face_like.jpg"), two_face_blob())
    cv2.imwrite(
        str(HERE / "face_synth.jpg"), face_synth(), [cv2.IMWRITE_JPEG_QUALITY, 90]
    )
    cv2.imwrite(str(HERE / "blank.jpg"), np.full((480, 640, 3), 128, np.uint8))
    (HERE / "corrupt.jpg").write_bytes(b"this is not a jpeg at all\n" * 8)
    write_live_sequence(HERE / "face_synth.jpg", HERE / "live")
    write_action_sequences(HERE / "face_synth.jpg", HERE)
    listed = sorted(HERE.glob("*.jpg")) + sorted(HERE.glob("live*/*.jpg"))
    for f in listed:
        print(f.relative_to(HERE), f.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

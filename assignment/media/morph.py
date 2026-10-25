"""Similarity-align faces, interpolate landmark geometry, and encode below 100 KB."""
import argparse
import subprocess
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import Delaunay


def landmarks(path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("Unreadable input image")
    h, w = image.shape[:2]
    reduced = cv2.resize(image, (min(w, 1200), round(h * min(1, 1200 / w))))
    with mp.solutions.face_mesh.FaceMesh(static_image_mode=True, refine_landmarks=True) as mesh:
        result = mesh.process(cv2.cvtColor(reduced, cv2.COLOR_BGR2RGB))
    if not result.multi_face_landmarks:
        raise ValueError("No face detected")
    points = np.array([[p.x * w, p.y * h] for p in result.multi_face_landmarks[0].landmark[:468]], dtype=np.float32)
    return image, points


def align(image, points, width=320, height=384):
    a, b = points[33], points[263]
    delta = b - a
    scale = (width * .32) / np.linalg.norm(delta)
    theta = np.arctan2(delta[1], delta[0])
    rotation = scale * np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    translation = np.array([width * .34, height * .365]) - rotation @ a
    matrix = np.column_stack([rotation, translation]).astype(np.float32)
    aligned = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)
    points = points @ rotation.T + translation
    # Border control points anchor the image while the face changes shape.
    anchors = np.array([[x, y] for x, y in [(0, 0), (width / 2, 0), (width - 1, 0), (width - 1, height / 2),
                                          (width - 1, height - 1), (width / 2, height - 1), (0, height - 1), (0, height / 2)]])
    return aligned, np.concatenate([points, anchors]).astype(np.float32)


def warp(image, source, target, triangles):
    result = np.zeros_like(image, dtype=np.float32)
    for tri in triangles:
        a, b = source[tri], target[tri]
        x, y, w, h = cv2.boundingRect(b)
        # Rectangles from facial landmarks can extend beyond the frame.
        left, top, right, bottom = max(0, x), max(0, y), min(image.shape[1], x+w), min(image.shape[0], y+h)
        if right <= left or bottom <= top:
            continue
        local = b - np.array([left, top], np.float32)
        matrix = cv2.getAffineTransform(a.astype(np.float32), local.astype(np.float32))
        patch = cv2.warpAffine(image, matrix, (right-left, bottom-top), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
        mask = np.zeros((bottom-top, right-left), np.float32)
        cv2.fillConvexPoly(mask, np.rint(local).astype(np.int32), 1, lineType=cv2.LINE_AA)
        roi = result[top:bottom, left:right]
        roi[:] = roi * (1-mask[..., None]) + patch * mask[..., None]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("output")
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    first, a = align(*landmarks(args.source))
    last, b = align(*landmarks(args.target))
    triangles = Delaunay((a+b)/2).simplices
    raw = out.with_suffix(".rgb")
    examples = []
    with raw.open("wb") as stream:
        for frame in range(120):
            t = np.clip((frame-10)/99, 0, 1)
            t = t*t*(3-2*t)
            if t == 0:
                image = first
            elif t == 1:
                image = last
            else:
                mid = (1-t)*a+t*b
                image = np.clip((1-t)*warp(first, a, mid, triangles) + t*warp(last, b, mid, triangles), 0, 255).astype(np.uint8)
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            stream.write(rgb.tobytes())
            if frame in [0, 30, 60, 90, 119]:
                examples.append(Image.fromarray(rgb))
    strip = Image.new("RGB", (320*len(examples), 384))
    for i, image in enumerate(examples):
        strip.paste(image, (320*i, 0))
    strip.save(out.with_name("morph-contact-sheet.jpg"), quality=92)
    common = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x384", "-r", "30", "-i", str(raw),
              "-an", "-c:v", "libx264", "-preset", "slow", "-b:v", "175k", "-pix_fmt", "yuv420p", "-passlogfile", str(out.with_suffix(".pass"))]
    subprocess.run(common + ["-pass", "1", "-f", "null", "/dev/null"], check=True)
    credit = "Dario Fo: Bernard Gotfryd / Library of Congress (public domain). Frank-Walter Steinmeier: Press Service of the President of Azerbaijan, President.az, CC BY 4.0 https://creativecommons.org/licenses/by/4.0/ . Images aligned, warped and blended; source links in ATTRIBUTION.md."
    subprocess.run(common + ["-pass", "2", "-movflags", "+faststart", "-metadata", "comment="+credit, str(out)], check=True)
    raw.unlink()
    for p in out.parent.glob(out.with_suffix(".pass").name + "*"):
        p.unlink()
    assert out.stat().st_size < 100000, f"Video exceeds size limit: {out.stat().st_size}"
    print(f"{out}: {out.stat().st_size} bytes; 120 frames; 4 seconds")


if __name__ == "__main__":
    main()

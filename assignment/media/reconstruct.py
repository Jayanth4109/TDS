"""Single-view face reconstruction: inferred facial depth plus an estimated closed cranium.

The unseen head shape is an estimate, not ground-truth geometry. No flat billboard is used.
"""
import argparse
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import trimesh
from PIL import Image
from scipy.spatial import Delaunay


def oval_order():
    graph = {}
    for a, b in mp.solutions.face_mesh.FACEMESH_FACE_OVAL:
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    order = [10]
    previous = None
    while True:
        nxt = next(x for x in graph[order[-1]] if x != previous)
        if nxt == order[0]:
            break
        previous = order[-1]
        order.append(nxt)
    return order


def reconstruct(source, output):
    image = Image.open(source).convert("RGB")
    reduced = image.copy()
    reduced.thumbnail((1200, 1200))
    with mp.solutions.face_mesh.FaceMesh(static_image_mode=True, refine_landmarks=True) as detector:
        result = detector.process(np.array(reduced))
    if not result.multi_face_landmarks:
        raise ValueError("No face detected")
    points = np.array([[p.x, p.y, p.z] for p in result.multi_face_landmarks[0].landmark[:468]])
    width, height = image.size
    ratio = height / width
    oval = np.array(oval_order())
    border = points[oval].copy()
    center = np.array([np.mean(border[:, 0]), .43])
    # Expand the predicted facial oval to the photographed hair and ear silhouette.
    border[:, 0] = center[0] + (border[:, 0]-center[0])*1.055
    upper = np.clip((.43-border[:, 1])/(.43-points[10, 1]), 0, 1)
    border[:, 1] -= .13*upper**1.35
    border[:, 1] += .005*(1-upper)
    border[:, 2] = .17 - .04*upper
    front_points = np.concatenate([points, border])
    contour = border[:, :2].astype(np.float32)
    faces = Delaunay(front_points[:, :2]).simplices
    faces = np.array([t for t in faces if cv2.pointPolygonTest(contour, tuple(front_points[t, :2].mean(axis=0).astype(float)), False) >= 0])
    scale = .16/(np.max(border[:, 0])-np.min(border[:, 0]))
    def xyz(p):
        return np.column_stack([(p[:, 0]-center[0])*scale, (.43-p[:, 1])*ratio*scale, -p[:, 2]*scale])
    vertices = xyz(front_points)
    # Trimesh UV convention has v upward; the source photograph has y downward.
    uv = np.column_stack([front_points[:, 0], 1-front_points[:, 1]])
    tex = image.copy()
    tex.thumbnail((1536, 1536))
    tex.format = "JPEG"
    material = trimesh.visual.material.PBRMaterial(name="Nixon portrait", baseColorTexture=tex,
        metallicFactor=0, roughnessFactor=1, doubleSided=True)
    front = trimesh.Trimesh(vertices=vertices, faces=faces, process=False,
        visual=trimesh.visual.texture.TextureVisuals(uv=uv, material=material))
    # Ensure facial normals point toward the viewer (+Z).
    if np.median(front.face_normals[:, 2]) < 0:
        front.invert()
    n = len(border)
    shell_vertices = [xyz(border)]
    shell_uv = [np.column_stack([border[:, 0], 1-border[:, 1]])]
    shell_colors = []
    for j in range(12):
        angle = (j/12)*(np.pi/2)
        if j:
            p = border.copy()
            p[:, 0] = center[0] + (border[:, 0]-center[0])*np.cos(angle)
            p[:, 1] = .36 + (border[:, 1]-.36)*np.cos(angle)
            p[:, 2] = border[:, 2] + (.46-border[:, 2])*np.sin(angle)
            shell_vertices.append(xyz(p))
            shell_uv.append(np.column_stack([p[:, 0], 1-p[:, 1]]))
        for k in range(n):
            is_hair = border[k, 1] < .43
            shell_colors.append([49, 43, 40, 255] if is_hair else [157, 112, 87, 255])
    shell_vertices = np.vstack(shell_vertices)
    back_center = np.array([[0, (.43-.36)*ratio*scale, -.46*scale]])
    shell_vertices = np.vstack([shell_vertices, back_center])
    shell_uv = np.vstack([*shell_uv, [[.5, .64]]])
    shell_colors.append([61, 51, 44, 255])
    shell_faces = []
    for j in range(11):
        for k in range(n):
            a, b = j*n+k, j*n+(k+1)%n
            c, d = a+n, b+n
            shell_faces += [[a, c, b], [b, c, d]]
    for k in range(n):
        shell_faces.append([11*n+k, len(shell_vertices)-1, 11*n+(k+1)%n])
    back_texture = np.zeros((256, 64, 3), dtype=np.uint8)
    for row in range(256):
        blend = np.clip((row/255-.42)/.035, 0, 1)
        back_texture[row, :, :] = (1-blend)*np.array([49, 43, 40])+blend*np.array([157, 112, 87])
    back_material = trimesh.visual.material.PBRMaterial(name="Estimated hair and skin", baseColorTexture=Image.fromarray(back_texture), metallicFactor=0, roughnessFactor=1, doubleSided=True)
    shell = trimesh.Trimesh(vertices=shell_vertices, faces=shell_faces, process=False,
        visual=trimesh.visual.texture.TextureVisuals(uv=shell_uv, material=back_material))
    shell.fix_normals()
    scene = trimesh.Scene()
    scene.add_geometry(front, node_name="Reconstructed facial surface")
    scene.add_geometry(shell, node_name="Estimated cranium and hair")
    def unlit(tree):
        for mat in tree.get("materials", []):
            if mat.get("name") == "Nixon portrait":
                mat.setdefault("extensions", {})["KHR_materials_unlit"] = {}
        tree.setdefault("extensionsUsed", []).append("KHR_materials_unlit")
        tree["asset"]["extras"] = {"source": "https://upload.wikimedia.org/wikipedia/commons/e/ec/Richard_Nixon_presidential_portrait.jpg",
            "method": "MediaPipe facial depth; single-view estimated cranium", "subject": "Richard Nixon"}
    payload = trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=unlit, include_normals=True)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    assert len(payload) <= 2000000
    loaded = trimesh.load(output)
    assert len(loaded.geometry) == 2 and loaded.extents[2] > .1
    print(f"{output}: {len(payload)} bytes; {sum(len(g.faces) for g in loaded.geometry.values())} triangles; extents {loaded.extents.round(3)} m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()
    reconstruct(args.source, args.output)

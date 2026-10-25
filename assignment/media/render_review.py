"""Run with Blender: blender -b --python media/render_review.py -- model.glb output_dir"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
model, output = Path(args[0]).resolve(), Path(args[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(model))
points = [obj.matrix_world @ Vector(corner) for obj in bpy.context.scene.objects if obj.type == "MESH" for corner in obj.bound_box]
low = Vector([min(p[i] for p in points) for i in range(3)])
high = Vector([max(p[i] for p in points) for i in range(3)])
center = (low+high)/2
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 16
scene.cycles.use_denoising = False
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.world.color = (.45, .45, .45)
scene.view_settings.view_transform = "Standard"
for location, energy, size in [((-.35, -.45, .5), .8, .4), ((.35, .1, .3), .4, .3)]:
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
bpy.ops.object.camera_add()
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = .31
scene.camera = camera
for name, degrees in [("front", 0), ("three-quarter", 45), ("profile", 90), ("back", 180)]:
    a = math.radians(degrees)
    camera.location = center + Vector((.6*math.sin(a), -.6*math.cos(a), .015))
    camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(output/(name+".png"))
    bpy.ops.render.render(write_still=True)

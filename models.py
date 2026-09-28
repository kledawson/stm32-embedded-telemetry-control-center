"""Built-in 3D meshes and STL loading for the dashboard."""

import math

import numpy as np
import pyqtgraph.opengl as gl
from stl import mesh as stl_mesh


# --- PROCEDURAL 3D MESH BUILDERS ---
def create_mesh(verts, faces, color):
    face_colors = np.tile(np.array(color, dtype=np.float32), (len(faces), 1))
    return gl.GLMeshItem(vertexes=np.array(verts, dtype=np.float32), faces=np.array(faces), faceColors=face_colors, shader='shaded', drawEdges=True, edgeColor=(1,1,1,1))


def create_stl_mesh(path):
    cad_mesh = stl_mesh.Mesh.from_file(path)
    triangles = np.asarray(cad_mesh.vectors, dtype=np.float32)
    vertices = triangles.reshape(-1, 3)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    vertices = vertices - center
    faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
    return create_mesh(vertices, faces, (0.25, 0.75, 1.0, 0.95))

class ColoredMesh:
    """Small triangle builder so model components have recognizable colors."""

    def __init__(self):
        self.triangles = []
        self.colors = []

    def quad(self, a, b, c, d, color):
        self.triangles.extend(((a, b, c), (a, c, d)))
        self.colors.extend((color, color))

    def box(self, x0, x1, y0, y1, z0, z1, color):
        points = ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
        for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            self.quad(*(points[index] for index in face), color)

    def disc(self, x, y, z, radius, color, segments=12):
        for index in range(segments):
            first = 2 * math.pi * index / segments
            second = 2 * math.pi * (index + 1) / segments
            self.triangles.append(((x, y, z),
                                   (x + radius * math.cos(first), y + radius * math.sin(first), z),
                                   (x + radius * math.cos(second), y + radius * math.sin(second), z)))
            self.colors.append(color)

    def item(self):
        vertices = np.asarray(self.triangles, dtype=np.float32).reshape(-1, 3)
        faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
        return gl.GLMeshItem(vertexes=vertices, faces=faces,
                             faceColors=np.asarray(self.colors, dtype=np.float32),
                             shader='shaded', drawEdges=True, edgeColor=(.14, .23, .32, .55))

def create_satellite_mesh():
    model = ColoredMesh()
    silver = (.68, .75, .79, 1)
    gold = (.82, .62, .24, 1)
    blue = (.09, .27, .58, 1)
    model.box(-1.4, 1.4, -1.1, 1.1, -1.15, 1.15, gold)  # equipment bus
    model.box(-1.1, 1.1, -1.14, -1.1, -.85, .85, silver)
    model.box(-1.65, -1.4, -.32, .32, -.2, .2, silver)
    model.box(1.4, 1.65, -.32, .32, -.2, .2, silver)
    for side in (-1, 1):
        for panel in range(2):
            inner = 1.65 + panel * 2.9
            outer = inner + 2.75
            x0, x1 = (inner, outer) if side > 0 else (-outer, -inner)
            model.box(x0, x1, -1.2, 1.2, -.12, .12, blue)
            # Pale cell grid makes the wings read as photovoltaic panels.
            for step in range(1, 4):
                x = x0 + (x1 - x0) * step / 4
                model.box(x - .025, x + .025, -1.2, 1.2, .13, .15, silver)
            model.box(x0, x1, -.025, .025, .13, .15, silver)
    model.box(-.08, .08, -.08, .08, 1.15, 2.6, silver)  # antenna mast
    model.disc(0, 0, 2.65, .55, silver)
    model.box(-.16, .16, 1.1, 2.35, -.16, .16, silver)  # forward sensor boom
    model.disc(0, 2.35, .17, .48, (.78, .84, .88, 1))
    return model.item()


def create_drone_mesh():
    model = ColoredMesh()
    shell = (.22, .34, .41, 1)
    arm = (.52, .62, .68, 1)
    rotor = (.08, .72, .83, .88)
    model.box(-1.3, 1.3, -1.05, 1.05, -.55, .55, shell)
    model.box(-.8, .8, .55, 1.15, .55, .68, (.96, .45, .20, 1))  # forward marker
    for x in (-3.3, 3.3):
        for y in (-3.0, 3.0):
            x0, x1 = sorted((0, x))
            y0, y1 = sorted((0, y))
            model.box(x0, x1, y - .14, y + .14, -.12, .14, arm)
            model.box(x - .14, x + .14, y0, y1, -.12, .14, arm)
            model.box(x - .45, x + .45, y - .45, y + .45, .05, .4, shell)
            model.disc(x, y, .48, 1.12, rotor)
            model.disc(x, y, .5, .18, shell)
    for x in (-.9, .9):
        model.box(x - .08, x + .08, -1.4, 1.4, -1.55, -.5, arm)
        model.box(x - .22, x + .22, -1.5, 1.5, -1.6, -1.48, arm)
    return model.item()

def create_aircraft_mesh():
    verts = [[0.0,5.0,0.0],[-1.0,-2.0,-0.5],[1.0,-2.0,-0.5],[0.0,-2.0,1.0],[-7.0,-2.0,0.0],[7.0,-2.0,0.0],[0.0,-5.0,3.0],[0.0,-5.0,0.0]]
    faces = [[0,1,2],[0,2,3],[0,3,1],[0,1,4],[0,2,5],[3,6,7]]
    return create_mesh(verts, faces, (0.9, 0.2, 0.2, 0.95))

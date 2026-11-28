"""Montaj önizlemesi (kaba çözünürlük)."""
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from robot_v4 import *

renk = {"govde": "#e8e8e8", "alt_plaka": "#b0b0b0", "kapak": "#cfcfcf", "kelepce": "#888888",
        "boyun": "#9fb7d6", "kafa_on": "#f5f5f5", "kafa_arka": "#d9d9d9", "mil_burcu": "#9fb7d6"}
meshes = {}
for n, (fn, bounds, _) in PARTS.items():
    v, f, _ = mesh(fn, bounds, res=1.1)
    meshes[n] = (assembly_transform(n, v), f)
light = np.array([0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
fig = plt.figure(figsize=(14, 8), dpi=110)
for k, (el, az, title) in enumerate(((15, -60, "Önden (DC jak sol-önde)"), (15, 150, "Arkadan (GPIO penceresi)"), (10, 0, "Sağdan (USB/Ethernet)"))):
    ax = fig.add_subplot(1, 3, k + 1, projection="3d")
    T, C = [], []
    for n, (v, f) in meshes.items():
        tri = v[f]
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
        sh = 0.35 + 0.65 * np.clip(nrm @ light, 0, 1)
        base = np.array(matplotlib.colors.to_rgb(renk[n]))
        T.append(tri); C.append(np.clip(base[None] * sh[:, None], 0, 1))
    ax.add_collection3d(Poly3DCollection(np.concatenate(T), facecolors=np.concatenate(C), linewidths=0))
    ax.set_xlim(-60, 60); ax.set_ylim(-60, 60); ax.set_zlim(0, 215); ax.set_box_aspect((120, 120, 215))
    ax.view_init(el, az); ax.set_axis_off(); ax.set_title(title)
plt.tight_layout(); plt.savefig("onizleme.png"); print("ok")

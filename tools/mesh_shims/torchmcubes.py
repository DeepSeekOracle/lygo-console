"""Stand-in for torchmcubes so TripoSR can extract a mesh without a CUDA compile.

torchmcubes returns each vertex as (z, y, x). TripoSR's marching-cubes helper
then swaps those axes with [2, 1, 0], which puts them back in (x, y, z).
scikit-image already returns (x, y, z). Handing those through unchanged makes
the helper's swap scramble the shape, and the vertex colors are then sampled
at the scrambled points. This shim swaps first, so the helper's own swap
restores the grid axes.
"""
from __future__ import annotations


def marching_cubes(volume, thresh):
    import numpy as np
    import torch
    from skimage.measure import marching_cubes as mc

    arr = volume.detach().float().cpu().numpy()
    verts, faces, _normals, _values = mc(arr, level=float(thresh), allow_degenerate=False)
    # (x, y, z) -> (z, y, x), the order torchmcubes returns.
    ordered = np.ascontiguousarray(verts[:, [2, 1, 0]], dtype=np.float32)
    # scikit-image winds triangles inward on a positive-inside field. Flip them
    # so the surface faces out, which is what the viewer lights.
    wound = np.ascontiguousarray(faces[:, [0, 2, 1]], dtype=np.int64)
    return (
        torch.from_numpy(ordered),
        torch.from_numpy(wound),
    )

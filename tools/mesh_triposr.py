"""Photo to GLB. Run with the TripoSR virtualenv, not the console interpreter.

The panel credits Modly by Lightning Pixel (https://github.com/lightningpixel/modly).
This file does not include Modly's source. The engine is TripoSR, MIT.

The console passes LYGO_MESH_HOME (the TripoSR checkout) and LYGO_MESH_SHIM (this
kit's torchmcubes stand-in). One JSON object is the last line of stdout.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(error: str, detail: str = "") -> None:
    emit({"ok": False, "error": error, "detail": detail[:800]})
    raise SystemExit(1)


def _boot() -> None:
    home = os.environ.get("LYGO_MESH_HOME") or ""
    shim = os.environ.get("LYGO_MESH_SHIM") or ""
    if shim and shim not in sys.path:
        sys.path.insert(0, shim)
    if home and home not in sys.path:
        sys.path.insert(0, home)


def do_status() -> None:
    _boot()
    home = Path(os.environ.get("LYGO_MESH_HOME") or "")
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        fail("no_torch", str(exc))
    emit({
        "ok": True,
        "cuda": bool(torch.cuda.is_available()),
        "torch": torch.__version__,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "tsr": (home / "tsr" / "system.py").is_file(),
        "home": str(home),
    })


def _fill_background(image):
    """Official TripoSR plate: RGB over mid-gray (0.5), which is what the weights saw."""
    import numpy as np
    from PIL import Image

    arr = np.asarray(image).astype(np.float32) / 255.0
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.shape[-1] == 3:
        return Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8))
    rgb = arr[:, :, :3] * arr[:, :, 3:4] + (1.0 - arr[:, :, 3:4]) * 0.5
    return Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8))


def _prepare(path: str, remove_bg: bool, foreground: float):
    from PIL import Image

    image = Image.open(path)
    ratio = max(0.5, min(1.0, float(foreground)))
    if remove_bg:
        # The demo converts to RGB first, cuts, then scales the subject to `ratio`
        # of a square frame. A near-white plate and an unscaled photo both warp the mesh.
        image = image.convert("RGB")
        try:
            from tsr.utils import remove_background, resize_foreground

            cut = remove_background(image)
            image = resize_foreground(cut.convert("RGBA"), ratio)
        except Exception as exc:  # noqa: BLE001 - a missing cutter still leaves the photo usable
            sys.stderr.write("background cutter skipped: %s\n" % exc)
        return _fill_background(image)
    if image.mode == "RGBA":
        return _fill_background(image)
    return image.convert("RGB")


def _as_uint8_colors(mesh):
    import numpy as np

    colors = np.asarray(mesh.visual.vertex_colors)
    if colors.size == 0:
        return None
    peak = float(np.nanmax(colors))
    floor = float(np.nanmin(colors))
    if colors.dtype.kind == "f" or peak <= 1.01:
        painted = (np.clip(colors, 0, 1) * 255).astype("uint8")
        mesh.visual.vertex_colors = painted
    return {"color_min": round(floor, 4), "color_max": round(peak, 4)}


def do_generate(image: str, out: str, resolution: int, remove_bg: bool, foreground: float = 0.85) -> None:
    _boot()
    if not Path(image).is_file():
        fail("missing_image", image)
    try:
        import torch
        # The checkpoint is a trusted local weight file. Newer torch refuses it
        # unless weights_only is turned off.
        _load = torch.load

        def _load_weights(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _load(*args, **kwargs)

        torch.load = _load_weights
        from tsr.system import TSR
    except Exception as exc:  # noqa: BLE001
        fail("import", traceback.format_exc()[-800:] or str(exc))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        fail("no_cuda", "TripoSR on this desk needs the NVIDIA card")
    try:
        model = TSR.from_pretrained(
            "stabilityai/TripoSR",
            config_name="config.yaml",
            weight_name="model.ckpt",
        )
        model.renderer.set_chunk_size(8192)
        model.to(device)
        photo = _prepare(image, remove_bg, foreground)
        with torch.no_grad():
            scene = model([photo], device=device)
            meshes = model.extract_mesh(scene, has_vertex_color=True, resolution=resolution)
        mesh = meshes[0]
        paint = None
        try:
            paint = _as_uint8_colors(mesh)
        except Exception:
            paint = None
        if hasattr(mesh, "nondegenerate_faces"):
            mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        texture = 0
        try:
            baked, texture = _bake_texture(mesh, model, scene[0])
            if baked is not None:
                mesh = baked
                paint = {"color_min": 0, "color_max": 255}
        except Exception as exc:  # noqa: BLE001 - vertex colors still ship if the bake cannot run
            sys.stderr.write("texture bake skipped: %s\n" % exc)
            texture = 0
        _stand_up(mesh)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        mesh.export(out)
        # trimesh will not write COLOR_0 beside a texture, so put the carried colours in
        # by hand. Without this the baked GLB renders uncoloured in this console.
        _inject_color0(out, (getattr(mesh, "metadata", None) or {}).get("lygo_color0"))
        emit({
            "ok": True,
            "path": out,
            "bytes": Path(out).stat().st_size,
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "resolution": resolution,
            "foreground": ratio_used(foreground),
            "texture": texture,
            "device": device,
            "engine": "TripoSR",
            **(paint or {}),
        })
    except Exception as exc:  # noqa: BLE001
        fail("generate", traceback.format_exc()[-800:] or str(exc))
    finally:
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _stand_up(mesh) -> None:
    """Same rotation the official demo applies before showing the mesh."""
    try:
        import numpy as np
        import trimesh
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    except Exception:
        return
    try:
        if mesh.is_watertight and float(mesh.volume) < 0:
            mesh.invert()
    except Exception:
        return


def _inject_color0(path, colors) -> bool:
    """Write COLOR_0 into an exported GLB, because trimesh omits it beside a texture.

    glTF multiplies a material's baseColorTexture by COLOR_0 (spec 5.19), and a viewer
    that cannot load that texture then has only the colour attribute to draw - the
    difference between a coloured model and a white one here. `colors` must already be
    in the exported file's vertex order; the baked mesh carries them through
    ``atlas["vmapping"]``, so they are. Best-effort: a missing colour attribute must
    never cost a finished mesh, so every failure just returns False.
    """
    if colors is None:
        return False
    try:
        import json as _json
        import struct as _struct

        import numpy as _np

        rgba = _np.asarray(colors)
        if rgba.dtype != _np.uint8:
            rgba = _np.clip(rgba, 0, 255).astype(_np.uint8)
        if rgba.ndim != 2 or rgba.shape[1] not in (3, 4):
            return False
        if rgba.shape[1] == 3:
            # VEC4, not VEC3: a 3-byte stride is not 4-byte aligned, and WebGL refuses an
            # unaligned vertex attribute by dropping the whole primitive.
            rgba = _np.concatenate(
                [rgba, _np.full((len(rgba), 1), 255, dtype=_np.uint8)], axis=1)

        raw = Path(path).read_bytes()
        if raw[:4] != b"glTF":
            return False
        off, js, bindata = 12, None, None
        while off < len(raw):
            clen, ctype = _struct.unpack("<II", raw[off:off + 8])
            if ctype == 0x4E4F534A:
                js = _json.loads(raw[off + 8:off + 8 + clen].decode("utf-8"))
            elif ctype == 0x004E4942:
                bindata = raw[off + 8:off + 8 + clen]
            off += 8 + clen
        if js is None or bindata is None:
            return False
        prim = js["meshes"][0]["primitives"][0]
        if "COLOR_0" in prim["attributes"]:
            return False
        exported = int(js["accessors"][prim["attributes"]["POSITION"]]["count"])
        if exported != len(rgba):
            sys.stderr.write(
                "color0 skipped: %d colours for %d exported vertices\n" % (len(rgba), exported))
            return False

        bindata += b"\x00" * ((-len(bindata)) % 4)
        cbytes = bytes(rgba.tobytes())
        js["bufferViews"].append(
            {"buffer": 0, "byteOffset": len(bindata), "byteLength": len(cbytes)})
        bindata += cbytes
        bindata += b"\x00" * ((-len(bindata)) % 4)
        js["accessors"].append({
            "bufferView": len(js["bufferViews"]) - 1,
            "componentType": 5121,
            "count": int(len(rgba)),
            "type": "VEC4",
            "normalized": True,
        })
        prim["attributes"]["COLOR_0"] = len(js["accessors"]) - 1
        js["buffers"][0]["byteLength"] = len(bindata)

        jb = _json.dumps(js, separators=(",", ":")).encode("utf-8")
        jb += b" " * ((-len(jb)) % 4)
        head = b"glTF" + _struct.pack("<II", 2, 12 + 8 + len(jb) + 8 + len(bindata))
        Path(path).write_bytes(
            head
            + _struct.pack("<II", len(jb), 0x4E4F534A) + jb
            + _struct.pack("<II", len(bindata), 0x004E4942) + bindata)
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("color0 injection skipped: %s\n" % exc)
        return False


def _bake_texture(mesh, model, scene_code):
    """Paint a texture from the model instead of smearing color across big faces.

    TripoSR's own bake does this with an xatlas unwrap and a hidden OpenGL pass.
    Vertex colors stay in place when that pass cannot run. 2048 is the bake the
    official script uses; 1024 is the fallback when the card is already busy.
    """
    import numpy as np
    import torch
    import trimesh
    from PIL import Image
    from tsr.bake_texture import make_atlas, rasterize_position_atlas

    last = None
    for resolution in (2048, 1024):
        try:
            padding = round(max(2, resolution / 256))
            atlas = make_atlas(mesh, resolution, padding)
            positions = rasterize_position_atlas(
                mesh,
                atlas["vmapping"],
                atlas["indices"],
                atlas["uvs"],
                resolution,
                padding,
            )
            flat = torch.tensor(positions.reshape(-1, 4)[:, :3], dtype=torch.float32, device=scene_code.device)
            with torch.no_grad():
                queried = model.renderer.query_triplane(model.decoder, flat, scene_code)
            rgb = queried["color"].detach().float().cpu().numpy().reshape(resolution, resolution, 3)
            rgb = np.clip(rgb, 0.0, 1.0)
            rgb[positions[:, :, 3] <= 0.0] = 0.5
            # The GL read is bottom-up. Flip it so the image's top row is UV v=0,
            # which is the top of a glTF texture.
            image = Image.fromarray((rgb * 255.0).astype(np.uint8), mode="RGB").transpose(Image.FLIP_TOP_BOTTOM)
            # The IMAGE is flipped; the UVs are NOT. TripoSR's own exporter pairs a flipped
            # image with `atlas["uvs"]` unchanged (run.py:185), and trimesh already converts
            # its bottom-left uv convention to glTF's top-left on export. Flipping v here as
            # well was a DOUBLE flip, measured on this rig: 39.6% of the mesh's vertices then
            # sampled the atlas's flat-grey fill (the model rendered grey) against 4.9% when
            # the uv is left alone, and mean saturation fell 0.33 -> 0.21.
            uvs = np.asarray(atlas["uvs"], dtype=np.float32)
            material = trimesh.visual.material.PBRMaterial(
                baseColorTexture=image,
                metallicFactor=0.0,
                roughnessFactor=0.85,
            )
            visual = trimesh.visual.TextureVisuals(uv=uvs, material=material)
            # Carry the normals through the atlas remap, exactly as TripoSR's own export does
            # (`meshes[0].vertex_normals[bake_output["vmapping"]]`). `process=False` means
            # trimesh will NOT recompute them, so dropping them here shipped a GLB with
            # POSITION+TEXCOORD_0 and NO NORMAL - and a PBR viewer then shades it flat.
            baked = trimesh.Trimesh(
                vertices=np.asarray(mesh.vertices)[atlas["vmapping"]],
                faces=np.asarray(atlas["indices"]),
                vertex_normals=np.asarray(mesh.vertex_normals, dtype=np.float32)[atlas["vmapping"]],
                visual=visual,
                process=False,
            )
            # CARRY THE SOURCE COLOURS THROUGH THE SAME REMAP. The unwrap rebuilds the
            # vertex list as vertices[vmapping], and the baked mesh then carries ONLY a
            # texture - so a viewer that cannot display that texture has nothing to draw
            # and paints the model white. Measured on this rig: the texture-only GLB
            # rendered pure white in the console's model-viewer, while the same model as a
            # COLOR_0 mesh rendered correctly, and Grok's bake is what removed COLOR_0.
            # `_source_colors` folds whatever visual the source carries back onto its
            # vertices, so this restores TripoSR's own colours exactly, not resampled ones.
            try:
                _src = _source_colors(mesh)
                if _src is not None:
                    baked.metadata["lygo_color0"] = np.asarray(_src)[atlas["vmapping"]]
            except Exception as exc:  # noqa: BLE001 - the texture still ships if this fails
                sys.stderr.write("vertex colour carry skipped: %s\n" % exc)
            return baked, resolution
        except Exception as exc:  # noqa: BLE001
            last = exc
            sys.stderr.write("texture %s skipped: %s\n" % (resolution, exc))
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
    if last is not None:
        raise last
    return None, 0


def ratio_used(foreground: float) -> float:
    return max(0.5, min(1.0, float(foreground)))


def _source_colors(mesh):
    """Per-vertex RGBA for a mesh, whatever kind of visual it carries.

    The baked path ships a TEXTURE, not vertex colours - so a reader that only knows
    ``visual.vertex_colors`` finds nothing on exactly the meshes this kit now produces,
    which is how a "lighter copy" came back completely uncoloured. trimesh's own
    ``to_color()`` folds the texture back onto the vertices and settles the uv/winding
    question for us, so it is preferred over sampling the atlas by hand.
    """
    import numpy as np

    n = len(mesh.vertices)
    try:
        visual = mesh.visual
        if getattr(visual, "kind", "") != "vertex":
            visual = visual.to_color()
        vc = np.asarray(visual.vertex_colors)
        if vc.size and vc.shape[0] == n:
            out = np.full((n, 4), 255, dtype=np.uint8)
            width = min(4, vc.shape[1])
            out[:, :width] = vc[:, :width]
            return out
    except Exception:
        return None
    return None


def _transfer_colors(simple, points, colors) -> None:
    """Give a decimated mesh the colours of the mesh it came from, by nearest vertex.

    Quadric decimation is a geometry operation: it returns no visual at all, so without
    this the simplified copy of a coloured model exports white.
    """
    import numpy as np
    import trimesh

    try:
        target = np.asarray(simple.vertices, dtype=np.float64)
        source = np.asarray(points, dtype=np.float64)
        colors = np.asarray(colors)
        try:
            from scipy.spatial import cKDTree

            _, index = cKDTree(source).query(target, k=1)
        except Exception:
            index = np.zeros(len(target), dtype=np.int64)
            for start in range(0, len(target), 512):
                chunk = target[start:start + 512]
                d = ((chunk[:, None, :] - source[None, :, :]) ** 2).sum(axis=2)
                index[start:start + 512] = d.argmin(axis=1)
        simple.visual = trimesh.visual.ColorVisuals(simple, vertex_colors=colors[index])
    except Exception as exc:  # noqa: BLE001 - a lost transfer must not lose the mesh
        sys.stderr.write("colour transfer skipped: %s\n" % exc)


def _cluster(mesh, ratio: float, source_colors=None):
    """Merge nearby vertices when the quadratic simplifier is not installed."""
    import numpy as np

    span = float(np.ptp(mesh.vertices, axis=0).max()) or 1.0
    # A higher keep-ratio uses a finer grid, so more of the shape survives.
    voxel = max(span * (1.0 - ratio) * 0.08, span * 0.004)
    quantized = np.round(mesh.vertices / voxel).astype(np.int64)
    _keys, inverse = np.unique(quantized, axis=0, return_inverse=True)
    counts = np.bincount(inverse)
    summed = np.zeros((_keys.shape[0], 3), dtype=np.float64)
    np.add.at(summed, inverse, mesh.vertices)
    verts = summed / counts[:, None]
    faces = inverse[mesh.faces]
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    colors = None
    src = source_colors if source_colors is not None else _source_colors(mesh)
    try:
        if src is not None and np.asarray(src).shape[0] == len(mesh.vertices):
            src = np.asarray(src, dtype=np.float64)
            summed_c = np.zeros((_keys.shape[0], src.shape[1]), dtype=np.float64)
            np.add.at(summed_c, inverse, src)
            colors = np.clip(summed_c / counts[:, None], 0, 255).astype(np.uint8)
    except Exception:
        colors = None
    import trimesh
    simple = trimesh.Trimesh(
        vertices=verts,
        faces=faces[keep],
        vertex_colors=colors,
        process=True,
    )
    return simple


def do_decimate(src: str, out: str, ratio: float) -> None:
    import numpy as np
    import trimesh

    if not Path(src).is_file():
        fail("missing_mesh", src)
    mesh = trimesh.load(src, force="mesh")
    before = int(len(mesh.faces))
    target = max(400, int(before * ratio))
    # Read the colours BEFORE the geometry is thrown away. The baked meshes carry a
    # texture, and quadric decimation keeps neither the texture nor the uvs - which is
    # how a "lighter copy" of a coloured model came back white.
    source_colors = _source_colors(mesh)
    simple = None
    clustered = False
    if hasattr(mesh, "simplify_quadric_decimation"):
        try:
            simple = mesh.simplify_quadric_decimation(target)
        except Exception:
            simple = None
    if simple is None or len(getattr(simple, "faces", [])) == 0:
        simple = _cluster(mesh, ratio, source_colors)
        clustered = True
    if not clustered and source_colors is not None and simple is not None:
        _transfer_colors(simple, np.asarray(mesh.vertices), source_colors)
    # Touch the property so trimesh actually computes them. An export that finds an empty
    # normal cache ships a GLB with no NORMAL, and a PBR viewer then shades the whole copy
    # flat - the same defect the baked path had.
    try:
        _ = simple.vertex_normals
    except Exception:
        pass
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    simple.export(out)
    emit({
        "ok": True,
        "path": out,
        "bytes": Path(out).stat().st_size,
        "faces_before": before,
        "faces": int(len(simple.faces)),
        "vertices": int(len(simple.vertices)),
        "colored": bool(source_colors is not None),
    })


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        do_status()
        return
    if cmd == "generate":
        foreground = float(sys.argv[6]) if len(sys.argv) > 6 else 0.85
        do_generate(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5] == "1", foreground)
        return
    if cmd == "decimate":
        do_decimate(sys.argv[2], sys.argv[3], float(sys.argv[4]))
        return
    fail("unknown_command", cmd)


if __name__ == "__main__":
    main()

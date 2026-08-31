"""
AuRaSim Blender -> GLB batch exporter (scene-aware)
===================================================

Designed for scene trees like:

Scene Collection
├─ static_world
├─ dynamic_rigid
├─ dynamic_deformable
├─ SENSORS
├─ SIM_IGNORE
└─ PATH_VIS
   ├─ sensors
   ├─ direct_path
   ├─ specular
   │  ├─ paths.*
   │  └─ hits.*
   ├─ diffuse
   ├─ refraction
   └─ diffraction

What it does for each requested frame:
1) Set frame and evaluate the dependency graph.
2) Export ALL currently visible geometry objects.
3) Freeze Armature / Shape Keys / Modifiers / Geometry Nodes to static geometry.
4) For PATH_VIS ray objects that are edge-only meshes/curves, convert them to real tube meshes
   so browser GLB viewers do not reduce them to 1-pixel lines.
5) For PATH_VIS hit objects that are point-only meshes, convert points to small icospheres.
6) Build temporary glTF-safe Principled materials without changing original materials.
7) Preserve BaseColor/Albedo/Diffuse image textures where possible, UVs, and vertex colors.
8) Export one GLB per frame to Desktop.
9) Clean up temporary objects/materials and restore the original frame/selection.

Recommended: Blender 4.x
"""

import bpy
import bmesh
import os
import traceback
import re
from pathlib import Path
from mathutils import Matrix, Vector

EXPORTER_VERSION = "2026-08-31-v3-HARD-FRAME-FILTER"
FRAME_TOKEN_RE = re.compile(r"frame[_\-. ]*0*(\d+)", re.IGNORECASE)


# =============================================================================
# USER CONFIG
# =============================================================================

# Single frame example:
# FRAME_OR_FRAMES = 1

# Five evenly spaced keyframes for a 100-frame sequence:
FRAME_OR_FRAMES = [1, 25, 50, 75, 100]

# Or choose any frame list you want, e.g.:
# FRAME_OR_FRAMES = [1, 20, 40, 60, 80]

FILE_PREFIX = "AuRaSim"

# Export only geometry that is currently visible in the active View Layer.
ONLY_VISIBLE = True
EXCLUDE_HIDE_RENDER = True

# Keep SIM_IGNORE if it is visible. This is useful for antenna-pattern visualization.
# If you do NOT want it in the GLB, set this to False.
EXPORT_SIM_IGNORE_IF_VISIBLE = True

# PATH_VIS handling ------------------------------------------------------------
PATH_VIS_ROOT_NAME = "PATH_VIS"
CONVERT_RAYS_TO_TUBES = True
CONVERT_HITS_TO_SPHERES = True

# Tube radius in Blender scene units (normally meters in AuRaSim).
RAY_TUBE_RADIUS = 0.006
RAY_TUBE_SIDES = 6

# Hit marker radius.
HIT_SPHERE_RADIUS = 0.025
HIT_SPHERE_SUBDIVISIONS = 1

# If a ray object already has triangle faces, keep it as-is instead of rebuilding tubes.
ONLY_TUBEIFY_EDGE_ONLY_OBJECTS = True

# Materials -------------------------------------------------------------------
MAKE_GLTF_SAFE_MATERIALS = True
PREFER_VERTEX_COLOR_FOR_SCIENTIFIC_MESH = True

SCIENTIFIC_NAME_HINTS = (
    "antenna", "pattern", "gain", "radiation", "heatmap",
    "ray", "path", "paths", "hit", "hits",
)

# If a material has no usable texture / vertex color, use its current Blender
# material/display color as fallback rather than making it white.
USE_FALLBACK_MATERIAL_COLOR = True

# GLB -------------------------------------------------------------------------
EXPORT_LOOSE_EDGES = True   # safety fallback for any edge geometry not tubeified
EXPORT_LOOSE_POINTS = True  # safety fallback for any point geometry not sphereified

TEMP_COLLECTION_NAME = "__AURASIM_GLB_EXPORT_TMP__"
TEMP_MATERIAL_PREFIX = "__AURASIM_GLTF_MAT__"


# =============================================================================
# LOG
# =============================================================================

LOG = []


def log(msg=""):
    msg = str(msg)
    print(msg)
    LOG.append(msg)


# =============================================================================
# PATH / FRAMES
# =============================================================================


def find_desktop():
    candidates = []
    for env_name in ("USERPROFILE", "OneDrive", "OneDriveConsumer"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "Desktop")
    candidates.append(Path.home() / "Desktop")

    for p in candidates:
        if p.exists():
            return p

    p = Path.home() / "Desktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


DESKTOP = find_desktop()


def normalize_frames(value):
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [int(x) for x in value]
    raise TypeError("FRAME_OR_FRAMES must be int or list/tuple/set of ints")


FRAMES = normalize_frames(FRAME_OR_FRAMES)


# =============================================================================
# COLLECTION / VISIBILITY HELPERS
# =============================================================================


def collection_has_ancestor_named(collection, target_name):
    """Search scene collection tree recursively for target ancestor relation."""
    scene_root = bpy.context.scene.collection

    def rec(parent, found_target=False):
        now_target = found_target or (parent.name == target_name)
        if parent == collection:
            return now_target
        for child in parent.children:
            result = rec(child, now_target)
            if result is not None:
                return result
        return None

    return bool(rec(scene_root, False))


def object_in_collection_tree(obj, target_name):
    for col in obj.users_collection:
        if col.name == target_name or collection_has_ancestor_named(col, target_name):
            return True
    return False


def object_path_category(obj):
    """Return PATH_VIS category if recognizable."""
    candidates = []
    for col in obj.users_collection:
        candidates.append(col.name.lower())

    name = obj.name.lower()
    candidates.append(name)

    for category in ("direct_path", "specular", "diffuse", "refraction", "diffraction", "sensors"):
        if any(category in x for x in candidates):
            return category

    return "path_vis"


def _collection_parent_map():
    """Build child-collection -> parent-collection names map."""
    parents = {}
    for parent in bpy.data.collections:
        for child in parent.children:
            parents.setdefault(child.name, set()).add(parent.name)

    # Scene root is not a normal bpy.data.collections parent, but its children
    # may contain useful frame tokens too.
    try:
        for child in bpy.context.scene.collection.children:
            parents.setdefault(child.name, set()).add(bpy.context.scene.collection.name)
    except Exception:
        pass

    return parents


def _ancestor_collection_names(obj):
    """
    Return direct + ancestor collection names for an object.
    This makes the frame filter work even if frame_XXXX is stored on a parent
    collection rather than the object itself.
    """
    parent_map = _collection_parent_map()
    result = []
    visited = set()
    stack = [col.name for col in obj.users_collection]

    while stack:
        name = stack.pop()
        if name in visited:
            continue
        visited.add(name)
        result.append(name)
        stack.extend(parent_map.get(name, ()))

    return result


def extract_explicit_frame_number(obj):
    """
    HARD frame-token extractor.

    It searches:
      - object name
      - direct collection names
      - ancestor collection names

    Supported examples:
      paths_specular_frame_0001
      hits_diffuse_frame_0025
      frame-100
      frame.0040

    If no explicit ``frameXXXX`` token exists, returns None. Such objects are
    treated as static/shared geometry and are kept for every export.
    """
    candidates = [obj.name]
    candidates.extend(_ancestor_collection_names(obj))

    for name in candidates:
        m = FRAME_TOKEN_RE.search(str(name))
        if m:
            return int(m.group(1))

    return None


def object_matches_export_frame(obj, frame):
    """
    GLOBAL hard filter:
    Any visible geometry carrying an explicit frame_XXXX token must match the
    requested export frame. This intentionally does NOT depend on PATH_VIS
    membership, so collection-tree naming cannot silently bypass the filter.
    """
    obj_frame = extract_explicit_frame_number(obj)
    return obj_frame is None or obj_frame == int(frame)


# Backward-compatible aliases used by other helpers in this script.
def extract_path_vis_frame_number(obj):
    return extract_explicit_frame_number(obj)


def path_vis_matches_export_frame(obj, frame):
    return object_matches_export_frame(obj, frame)

def is_exportable_visible_geometry(obj):
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
        return False

    if obj.name.startswith("__AURASIM_"):
        return False

    if not EXPORT_SIM_IGNORE_IF_VISIBLE and object_in_collection_tree(obj, "SIM_IGNORE"):
        return False

    if ONLY_VISIBLE:
        try:
            if not obj.visible_get(view_layer=bpy.context.view_layer):
                return False
        except TypeError:
            if not obj.visible_get():
                return False
        except Exception:
            if obj.hide_get() or obj.hide_viewport:
                return False

    if EXCLUDE_HIDE_RENDER and obj.hide_render:
        return False

    return True


# =============================================================================
# CLEANUP
# =============================================================================


def remove_temp_collection_if_exists():
    col = bpy.data.collections.get(TEMP_COLLECTION_NAME)
    if not col:
        return

    for obj in list(col.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        try:
            if data and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
        except Exception:
            pass

    bpy.data.collections.remove(col)


def remove_temp_materials():
    for mat in list(bpy.data.materials):
        if mat.name.startswith(TEMP_MATERIAL_PREFIX) and mat.users == 0:
            bpy.data.materials.remove(mat)


# =============================================================================
# MATERIAL HELPERS
# =============================================================================


def find_principled_nodes(mat):
    if not mat or not mat.use_nodes:
        return []
    return [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"]


def upstream_nodes(socket, max_depth=12):
    result = []
    visited = set()

    def rec(sock, depth):
        if depth > max_depth or not sock or not sock.is_linked:
            return
        for link in sock.links:
            node = link.from_node
            key = node.as_pointer()
            if key in visited:
                continue
            visited.add(key)
            result.append((depth, node))
            for inp in node.inputs:
                if inp.is_linked:
                    rec(inp, depth + 1)

    rec(socket, 0)
    return result


def image_score(node):
    if node.bl_idname != "ShaderNodeTexImage" or node.image is None:
        return -9999

    image = node.image
    text = " ".join([
        node.name or "", node.label or "", image.name or "", image.filepath or ""
    ]).lower()

    score = 0
    try:
        if "srgb" in image.colorspace_settings.name.lower():
            score += 20
    except Exception:
        pass

    for k in ("basecolor", "base_color", "base color", "albedo", "diffuse", "diff", "color", "colour"):
        if k in text:
            score += 10

    for k in ("normal", "rough", "metal", "specular", "ao", "ambient", "height", "bump", "displace", "opacity", "alpha", "mask"):
        if k in text:
            score -= 25

    return score


def find_best_color_texture(mat):
    if not mat or not mat.use_nodes:
        return None

    candidates = []

    # Best source: something actually upstream of Principled Base Color.
    for bsdf in find_principled_nodes(mat):
        base = bsdf.inputs.get("Base Color")
        if base and base.is_linked:
            for depth, node in upstream_nodes(base):
                if node.bl_idname == "ShaderNodeTexImage" and node.image:
                    candidates.append((100 - 3 * depth + image_score(node), node))

    # Fallback: search all image textures.
    for node in mat.node_tree.nodes:
        if node.bl_idname == "ShaderNodeTexImage" and node.image:
            candidates.append((image_score(node), node))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    score, node = candidates[0]
    return node if score >= -10 else None


def fallback_color(mat):
    if not mat:
        return (0.8, 0.8, 0.8, 1.0)

    for bsdf in find_principled_nodes(mat):
        base = bsdf.inputs.get("Base Color")
        if base and not base.is_linked:
            try:
                return tuple(base.default_value)
            except Exception:
                pass

    if mat.use_nodes:
        for node in mat.node_tree.nodes:
            for socket_name in ("Color", "Base Color"):
                sock = node.inputs.get(socket_name) if hasattr(node, "inputs") else None
                if sock and hasattr(sock, "default_value") and not sock.is_linked:
                    try:
                        val = tuple(sock.default_value)
                        if len(val) == 4:
                            return val
                    except Exception:
                        pass

        for node in mat.node_tree.nodes:
            if node.bl_idname == "ShaderNodeRGB":
                try:
                    return tuple(node.outputs[0].default_value)
                except Exception:
                    pass

    try:
        return tuple(mat.diffuse_color)
    except Exception:
        return (0.8, 0.8, 0.8, 1.0)


def get_active_color_attribute(mesh):
    try:
        attrs = mesh.color_attributes
        if not attrs or len(attrs) == 0:
            return None
        if attrs.active_color:
            return attrs.active_color.name
        return attrs[0].name
    except Exception:
        return None


def material_referenced_color_attribute(mat, mesh):
    if not mat or not mat.use_nodes:
        return None

    try:
        existing = {a.name for a in mesh.color_attributes}
    except Exception:
        existing = set()

    for node in mat.node_tree.nodes:
        if node.bl_idname == "ShaderNodeVertexColor":
            name = getattr(node, "layer_name", "")
            if name in existing:
                return name
        if node.bl_idname == "ShaderNodeAttribute":
            name = getattr(node, "attribute_name", "")
            if name in existing:
                return name
    return None


def uv_name_for_texture(source_obj, texture_node):
    if source_obj.type != "MESH" or len(source_obj.data.uv_layers) == 0:
        return None

    try:
        vec = texture_node.inputs.get("Vector")
        if vec and vec.is_linked:
            src = vec.links[0].from_node
            if src.bl_idname == "ShaderNodeUVMap":
                uv = getattr(src, "uv_map", "")
                if uv in source_obj.data.uv_layers:
                    return uv
    except Exception:
        pass

    try:
        for uv in source_obj.data.uv_layers:
            if uv.active_render:
                return uv.name
    except Exception:
        pass

    try:
        return source_obj.data.uv_layers.active.name
    except Exception:
        return source_obj.data.uv_layers[0].name


def make_vertex_color_node(nodes, attr_name):
    try:
        node = nodes.new("ShaderNodeVertexColor")
        node.layer_name = attr_name
        return node, node.outputs.get("Color")
    except Exception:
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_name = attr_name
        return node, node.outputs.get("Color")


def copy_basic_pbr_values(old_mat, bsdf):
    old_nodes = find_principled_nodes(old_mat)
    if old_nodes:
        old = old_nodes[0]
        for name in ("Metallic", "Roughness", "IOR"):
            a = old.inputs.get(name)
            b = bsdf.inputs.get(name)
            if a and b and not a.is_linked:
                try:
                    b.default_value = a.default_value
                except Exception:
                    pass
        return

    name = old_mat.name.lower()
    metallic = bsdf.inputs.get("Metallic")
    roughness = bsdf.inputs.get("Roughness")

    if any(k in name for k in ("chrome", "metal", "copper")):
        if metallic: metallic.default_value = 1.0
        if roughness: roughness.default_value = 0.15
    elif "rubber" in name:
        if metallic: metallic.default_value = 0.0
        if roughness: roughness.default_value = 0.75
    elif "plastic" in name:
        if metallic: metallic.default_value = 0.0
        if roughness: roughness.default_value = 0.35
    elif "paint" in name:
        if metallic: metallic.default_value = 0.1
        if roughness: roughness.default_value = 0.22
    elif "glass" in name:
        if metallic: metallic.default_value = 0.0
        if roughness: roughness.default_value = 0.08
        transmission = bsdf.inputs.get("Transmission Weight")
        if transmission: transmission.default_value = 1.0
        ior = bsdf.inputs.get("IOR")
        if ior: ior.default_value = 1.45


def create_safe_material(old_mat, source_obj, export_mesh, cache):
    if old_mat is None:
        return None

    scientific = any(h in source_obj.name.lower() for h in SCIENTIFIC_NAME_HINTS)
    attr_name = material_referenced_color_attribute(old_mat, export_mesh)
    if not attr_name and scientific and PREFER_VERTEX_COLOR_FOR_SCIENTIFIC_MESH:
        attr_name = get_active_color_attribute(export_mesh)

    color_tex = find_best_color_texture(old_mat)

    if attr_name and (scientific or color_tex is None):
        mode = ("VCOL", attr_name)
    elif color_tex is not None:
        mode = ("TEX", color_tex.image.name)
    elif attr_name:
        mode = ("VCOL", attr_name)
    else:
        mode = ("COLOR",)

    key = (old_mat.as_pointer(), mode, source_obj.name if mode[0] == "VCOL" else "")
    if key in cache:
        return cache[key]

    new_mat = bpy.data.materials.new(TEMP_MATERIAL_PREFIX + old_mat.name)
    new_mat.use_nodes = True
    nodes = new_mat.node_tree.nodes
    links = new_mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (500, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (160, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    color = fallback_color(old_mat) if USE_FALLBACK_MATERIAL_COLOR else (0.8, 0.8, 0.8, 1.0)
    try:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = color[3]
    except Exception:
        pass

    copy_basic_pbr_values(old_mat, bsdf)

    if mode[0] == "VCOL":
        node, out_socket = make_vertex_color_node(nodes, mode[1])
        node.location = (-220, 100)
        if out_socket:
            links.new(out_socket, bsdf.inputs["Base Color"])
        log(f"    [VERTEX COLOR] {old_mat.name} <- {mode[1]}")

    elif mode[0] == "TEX":
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = color_tex.image
        tex.location = (-220, 80)
        try:
            tex.interpolation = color_tex.interpolation
            tex.extension = color_tex.extension
        except Exception:
            pass

        uv_name = uv_name_for_texture(source_obj, color_tex)
        if uv_name and uv_name in export_mesh.uv_layers:
            uv = nodes.new("ShaderNodeUVMap")
            uv.uv_map = uv_name
            uv.location = (-480, 80)
            links.new(uv.outputs["UV"], tex.inputs["Vector"])

        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        log(f"    [TEXTURE] {old_mat.name} <- {color_tex.image.name} / UV={uv_name}")

    else:
        log(f"    [FALLBACK COLOR] {old_mat.name} <- {tuple(round(float(x),3) for x in color)}")

    lname = old_mat.name.lower()
    if "emission" in lname or "light" in lname:
        ec = bsdf.inputs.get("Emission Color")
        es = bsdf.inputs.get("Emission Strength")
        if ec: ec.default_value = color
        if es: es.default_value = 1.0

    cache[key] = new_mat
    return new_mat


# =============================================================================
# GEOMETRY EVALUATION / PATH VIS CONVERSION
# =============================================================================


def evaluated_mesh_copy(source_obj, depsgraph):
    eval_obj = source_obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        eval_obj,
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    return mesh, eval_obj.matrix_world.copy()


def build_tube_mesh_from_edges(source_mesh, radius, sides=6):
    """Convert all mesh edges into real cylinder geometry in local coordinates."""
    out_mesh = bpy.data.meshes.new("__AURASIM_RAY_TUBES__")
    bm = bmesh.new()

    verts = source_mesh.vertices
    created = 0

    for edge in source_mesh.edges:
        a = Vector(verts[edge.vertices[0]].co)
        b = Vector(verts[edge.vertices[1]].co)
        delta = b - a
        length = delta.length
        if length <= 1e-9:
            continue

        mid = (a + b) * 0.5
        rot = delta.to_track_quat('Z', 'Y').to_matrix().to_4x4()
        mat = Matrix.Translation(mid) @ rot

        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=max(3, int(sides)),
            radius1=radius,
            radius2=radius,
            depth=length,
            matrix=mat,
        )
        created += 1

    bm.to_mesh(out_mesh)
    bm.free()
    out_mesh.update()
    return out_mesh, created


def build_hit_mesh_from_points(source_mesh, radius, subdivisions=1):
    """Convert every source vertex into a small icosphere."""
    out_mesh = bpy.data.meshes.new("__AURASIM_HIT_SPHERES__")
    bm = bmesh.new()

    created = 0
    for v in source_mesh.vertices:
        mat = Matrix.Translation(Vector(v.co))
        bmesh.ops.create_icosphere(
            bm,
            subdivisions=max(1, int(subdivisions)),
            radius=radius,
            matrix=mat,
        )
        created += 1

    bm.to_mesh(out_mesh)
    bm.free()
    out_mesh.update()
    return out_mesh, created


def copy_material_slots(src_mesh, dst_mesh):
    for mat in src_mesh.materials:
        dst_mesh.materials.append(mat)


def bake_source_object(source_obj, depsgraph):
    """
    Returns (mesh, matrix_world, mode_string).
    PATH_VIS rays/hits receive special conversion.
    """
    mesh, matrix_world = evaluated_mesh_copy(source_obj, depsgraph)

    if not object_in_collection_tree(source_obj, PATH_VIS_ROOT_NAME):
        return mesh, matrix_world, "evaluated"

    name = source_obj.name.lower()
    category = object_path_category(source_obj)
    is_hit = "hit" in name
    is_path = ("path" in name or "ray" in name or category in {
        "direct_path", "specular", "diffuse", "refraction", "diffraction"
    }) and not is_hit

    # Hit points -> spheres
    if CONVERT_HITS_TO_SPHERES and is_hit and len(mesh.vertices) > 0:
        # Only rebuild if point/edge-like. If it already has faces, preserve it.
        if len(mesh.polygons) == 0:
            new_mesh, n = build_hit_mesh_from_points(mesh, HIT_SPHERE_RADIUS, HIT_SPHERE_SUBDIVISIONS)
            copy_material_slots(mesh, new_mesh)
            bpy.data.meshes.remove(mesh)
            return new_mesh, matrix_world, f"hits->spheres ({n})"

    # Ray edges -> tubes
    if CONVERT_RAYS_TO_TUBES and is_path and len(mesh.edges) > 0:
        should_convert = True
        if ONLY_TUBEIFY_EDGE_ONLY_OBJECTS and len(mesh.polygons) > 0:
            should_convert = False

        if should_convert:
            new_mesh, n = build_tube_mesh_from_edges(mesh, RAY_TUBE_RADIUS, RAY_TUBE_SIDES)
            copy_material_slots(mesh, new_mesh)
            bpy.data.meshes.remove(mesh)
            return new_mesh, matrix_world, f"rays->tubes ({n})"

    return mesh, matrix_world, "path-vis preserved"


# =============================================================================
# GLTF EXPORT
# =============================================================================


def export_glb(filepath):
    wanted = {
        "filepath": str(filepath),
        "export_format": "GLB",
        "use_selection": True,
        "export_animations": False,
        "export_materials": "EXPORT",
        "export_yup": True,
        "export_apply": False,
        "export_loose_edges": EXPORT_LOOSE_EDGES,
        "export_loose_points": EXPORT_LOOSE_POINTS,
        "export_vertex_color": "MATERIAL",
        "export_all_vertex_colors": True,
        "export_cameras": False,
        "export_lights": False,
    }

    try:
        props = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
        kwargs = {k: v for k, v in wanted.items() if k in props}
    except Exception:
        kwargs = wanted

    result = bpy.ops.export_scene.gltf(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"glTF exporter returned {result}")


# =============================================================================
# FRAME EXPORT
# =============================================================================


def export_frame(frame, material_cache):
    scene = bpy.context.scene
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()

    remove_temp_collection_if_exists()
    temp_collection = bpy.data.collections.new(TEMP_COLLECTION_NAME)
    scene.collection.children.link(temp_collection)

    visible_objects = [
        obj for obj in scene.objects
        if is_exportable_visible_geometry(obj)
    ]

    # HARD FILTER: independent of collection membership.
    source_objects = []
    skipped_frame_objects = []

    for obj in visible_objects:
        explicit_frame = extract_explicit_frame_number(obj)
        if explicit_frame is not None and explicit_frame != int(frame):
            skipped_frame_objects.append((obj, explicit_frame))
            continue
        source_objects.append(obj)

    kept_frame_specific = [
        obj for obj in source_objects
        if extract_explicit_frame_number(obj) == int(frame)
    ]

    # Fail closed. Never silently export a wrong-frame object again.
    wrong_frame_objects = [
        (obj, extract_explicit_frame_number(obj))
        for obj in source_objects
        if extract_explicit_frame_number(obj) is not None
        and extract_explicit_frame_number(obj) != int(frame)
    ]

    if wrong_frame_objects:
        preview = ", ".join(
            f"{obj.name}->frame{obj_frame}"
            for obj, obj_frame in wrong_frame_objects[:20]
        )
        raise RuntimeError(
            f"HARD FRAME FILTER FAILED for export frame {frame}: {preview}"
        )

    log("")
    log("=" * 80)
    log(f"FRAME {frame}")
    log("=" * 80)
    log(f"Exporter version: {EXPORTER_VERSION}")
    log(f"Visible geometry before hard frame filtering: {len(visible_objects)}")
    log(f"Wrong-frame geometry skipped: {len(skipped_frame_objects)}")
    log(f"Matching frame-specific geometry kept: {len(kept_frame_specific)}")
    log(f"Final geometry objects to export: {len(source_objects)}")

    log("Matching frame-specific objects:")
    for obj in kept_frame_specific:
        log(f"  KEEP frame {frame}: {obj.name}")

    # Show a short skip preview so the user can verify that the filter worked.
    if skipped_frame_objects:
        log("Skipped wrong-frame objects (first 12):")
        for obj, obj_frame in skipped_frame_objects[:12]:
            log(f"  SKIP frame {obj_frame}: {obj.name}")

    exported_objects = []

    for src in source_objects:
        try:
            mesh, matrix_world, mode = bake_source_object(src, depsgraph)
            temp_obj = bpy.data.objects.new(f"__AURASIM_EXPORT__{src.name}", mesh)
            temp_obj.matrix_world = matrix_world
            temp_collection.objects.link(temp_obj)
            temp_obj["AuRaSim_Source_Object"] = src.name
            temp_obj["AuRaSim_Frame"] = int(frame)

            log(f"  {src.name}: {mode} | V={len(mesh.vertices)} E={len(mesh.edges)} F={len(mesh.polygons)}")

            if MAKE_GLTF_SAFE_MATERIALS:
                old_materials = list(mesh.materials)
                for slot_index, old_mat in enumerate(old_materials):
                    if old_mat is None:
                        continue
                    safe = create_safe_material(old_mat, src, mesh, material_cache)
                    mesh.materials[slot_index] = safe

            exported_objects.append(temp_obj)

        except Exception as e:
            log(f"  ERROR {src.name}: {type(e).__name__}: {e}")
            log(traceback.format_exc())

    if not exported_objects:
        raise RuntimeError(f"Frame {frame}: no visible geometry to export")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in exported_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = exported_objects[0]

    filepath = DESKTOP / f"{FILE_PREFIX}_frame_{frame:04d}.glb"
    export_glb(filepath)
    log(f"DONE -> {filepath}")

    remove_temp_collection_if_exists()
    return filepath


# =============================================================================
# MAIN
# =============================================================================


def main():
    scene = bpy.context.scene
    original_frame = scene.frame_current
    original_selected = [o.name for o in bpy.context.selected_objects]
    original_active = bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None

    material_cache = {}
    exported = []

    log("=" * 80)
    log("AuRaSim SCENE-AWARE GLB EXPORT")
    log("=" * 80)
    log(f"Blender: {bpy.app.version_string}")
    log(f"Blend: {bpy.data.filepath or '[Unsaved]'}")
    log(f"Desktop: {DESKTOP}")
    log(f"Frames: {FRAMES}")
    log(f"Exporter version: {EXPORTER_VERSION}")
    log("HARD frame filter: ON (ANY explicit frame_XXXX geometry must match the export frame)")
    log(f"Ray tube radius: {RAY_TUBE_RADIUS}")
    log(f"Hit sphere radius: {HIT_SPHERE_RADIUS}")

    try:
        remove_temp_collection_if_exists()
        remove_temp_materials()

        for frame in FRAMES:
            exported.append(export_frame(frame, material_cache))

    finally:
        remove_temp_collection_if_exists()
        scene.frame_set(original_frame)
        bpy.context.view_layer.update()

        bpy.ops.object.select_all(action="DESELECT")
        for name in original_selected:
            obj = bpy.data.objects.get(name)
            if obj:
                try: obj.select_set(True)
                except Exception: pass

        if original_active:
            obj = bpy.data.objects.get(original_active)
            if obj:
                bpy.context.view_layer.objects.active = obj

        remove_temp_materials()

    log("")
    log("=" * 80)
    log("EXPORT SUMMARY")
    log("=" * 80)
    for p in exported:
        log(p)

    log_path = DESKTOP / f"{FILE_PREFIX}_GLB_export_log.txt"
    log_path.write_text("\n".join(LOG), encoding="utf-8")

    print("")
    print("=" * 80)
    print("AuRaSim export finished")
    print(f"GLBs: {DESKTOP}")
    print(f"Log: {log_path}")
    print("=" * 80)


main()

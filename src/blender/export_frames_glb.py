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

EXPORTER_VERSION = "2026-08-31-v4-SINGLE-ANIMATED-GLB"
FRAME_TOKEN_RE = re.compile(r"frame[_\-. ]*0*(\d+)", re.IGNORECASE)


# =============================================================================
# USER CONFIG
# =============================================================================

# Single frame example:
# FRAME_OR_FRAMES = 1

# Five evenly spaced keyframes for a 100-frame sequence:
FRAME_OR_FRAMES = list(range(1, 101))

# Or choose any frame list you want, e.g.:
# FRAME_OR_FRAMES = [1, 20, 40, 60, 80]

FILE_PREFIX = "AuRaSim"

# Simulation / playback rate. Your current sequence is 20 Hz.
EXPORT_FPS = 20

# Scene-aware animation categories based on your Outliner structure.
DYNAMIC_RIGID_ROOT_NAMES = ("dynamic_rigid", "SENSORS", "SIM_IGNORE")
DYNAMIC_DEFORMABLE_ROOT_NAMES = ("dynamic_deformable",)

# Objects under PATH_VIS with no explicit frame token are treated as rigid/animated.
ANIMATE_NONFRAME_PATH_VIS = True

# One shared NLA track name lets Blender's glTF exporter merge object tracks
# into one animation clip where supported.
MERGED_ANIMATION_NAME = "AuRaSim_AllFrames"

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

TEMP_COLLECTION_NAME = "__AURASIM_ALL_FRAMES_TMP__"
TEMP_MATERIAL_PREFIX = "__AURASIM_ALLFRAMES_MAT__"


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

# =============================================================================
# SINGLE-GLB ANIMATION HELPERS
# =============================================================================


def object_in_any_collection_tree(obj, root_names):
    return any(object_in_collection_tree(obj, name) for name in root_names)


def has_deformation_that_needs_snapshots(obj):
    """
    Rigid objects can reuse one mesh and animate TRS.
    Objects whose vertices genuinely deform are baked once per frame.
    """
    if obj.type != "MESH":
        # Curves / procedural geometry can change shape, so snapshot them if dynamic.
        return True

    try:
        if obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) > 1:
            return True
    except Exception:
        pass

    deform_modifiers = {
        "ARMATURE",
        "LATTICE",
        "MESH_DEFORM",
        "SURFACE_DEFORM",
        "NODES",
    }

    try:
        if any(mod.type in deform_modifiers for mod in obj.modifiers):
            return True
    except Exception:
        pass

    return False


def classify_shared_object(obj):
    """Return STATIC, RIGID, or SNAPSHOT for non-frame-specific geometry."""
    if object_in_any_collection_tree(obj, DYNAMIC_DEFORMABLE_ROOT_NAMES):
        return "SNAPSHOT"

    if object_in_any_collection_tree(obj, DYNAMIC_RIGID_ROOT_NAMES):
        if has_deformation_that_needs_snapshots(obj):
            return "SNAPSHOT"
        return "RIGID"

    if ANIMATE_NONFRAME_PATH_VIS and object_in_collection_tree(obj, PATH_VIS_ROOT_NAME):
        if has_deformation_that_needs_snapshots(obj):
            return "SNAPSHOT"
        return "RIGID"

    return "STATIC"


def apply_safe_materials(mesh, src, material_cache):
    if not MAKE_GLTF_SAFE_MATERIALS:
        return

    old_materials = list(mesh.materials)
    for slot_index, old_mat in enumerate(old_materials):
        if old_mat is None:
            continue
        safe = create_safe_material(old_mat, src, mesh, material_cache)
        mesh.materials[slot_index] = safe


def make_temp_object(src, depsgraph, temp_collection, material_cache, name_suffix=""):
    mesh, matrix_world, mode = bake_source_object(src, depsgraph)
    apply_safe_materials(mesh, src, material_cache)

    temp_name = f"__AURASIM_EXPORT__{src.name}{name_suffix}"
    obj = bpy.data.objects.new(temp_name, mesh)
    temp_collection.objects.link(obj)
    obj.matrix_world = matrix_world
    obj["AuRaSim_Source_Object"] = src.name
    return obj, mode


def set_object_trs_from_matrix(obj, matrix_world):
    loc, rot, scale = matrix_world.decompose()
    obj.location = loc
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rot
    obj.scale = scale
    return tuple(scale)


def keyframe_trs(obj, frame, matrix_world, visible=True):
    loc, rot, scale = matrix_world.decompose()
    obj.location = loc
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rot
    obj.scale = scale if visible else (0.0, 0.0, 0.0)

    obj.keyframe_insert(data_path="location", frame=frame)
    obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
    obj.keyframe_insert(data_path="scale", frame=frame)


def keyframe_snapshot_visibility(obj, visible_frame, base_scale):
    """
    Show one baked snapshot only at its own frame.
    Scale is used because standard glTF has no broadly supported object-visibility animation.
    CONSTANT interpolation makes the switch instantaneous.
    """
    f0 = FRAMES[0]
    f1 = FRAMES[-1]

    keys = []
    if visible_frame > f0:
        keys.append((visible_frame - 1, (0.0, 0.0, 0.0)))

    keys.append((visible_frame, base_scale))

    if visible_frame < f1:
        keys.append((visible_frame + 1, (0.0, 0.0, 0.0)))

    for frame, scale in keys:
        obj.scale = scale
        obj.keyframe_insert(data_path="scale", frame=frame)


def set_action_interpolation(obj, interpolation="LINEAR"):
    anim = obj.animation_data
    if not anim or not anim.action:
        return
    for fc in anim.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation


def set_scale_curve_constant(obj):
    anim = obj.animation_data
    if not anim or not anim.action:
        return
    for fc in anim.action.fcurves:
        if fc.data_path == "scale":
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"


def push_action_to_shared_nla(obj):
    """
    Put every object's action on an NLA track with the same name.
    Blender glTF groups same-named NLA tracks into one animation clip on versions
    that support this behavior.
    """
    anim = obj.animation_data
    if not anim or not anim.action:
        return False

    action = anim.action
    start = int(action.frame_range[0])

    track = anim.nla_tracks.new()
    track.name = MERGED_ANIMATION_NAME
    strip = track.strips.new(action.name, start, action)
    strip.name = MERGED_ANIMATION_NAME

    # Clear active action so only the NLA strip is exported.
    anim.action = None
    return True


def export_single_animated_glb(filepath):
    wanted = {
        "filepath": str(filepath),
        "export_format": "GLB",
        "use_selection": True,
        "export_animations": True,
        "export_frame_range": True,
        "export_force_sampling": True,
        "export_nla_strips": True,
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

    log("glTF exporter options:")
    for k, v in kwargs.items():
        log(f"  {k} = {v}")

    result = bpy.ops.export_scene.gltf(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"glTF exporter returned {result}")


# =============================================================================
# BUILD ONE ANIMATED GLB
# =============================================================================


def build_all_frames_scene():
    scene = bpy.context.scene
    material_cache = {}

    remove_temp_collection_if_exists()
    temp_collection = bpy.data.collections.new(TEMP_COLLECTION_NAME)
    scene.collection.children.link(temp_collection)

    # -------------------------------------------------------------------------
    # 1. Identify shared (non frame_XXXX) objects from the first frame.
    # -------------------------------------------------------------------------
    scene.frame_set(FRAMES[0])
    bpy.context.view_layer.update()

    shared_sources = [
        obj for obj in scene.objects
        if is_exportable_visible_geometry(obj)
        and extract_explicit_frame_number(obj) is None
    ]

    static_sources = []
    rigid_sources = []
    snapshot_sources = []

    for obj in shared_sources:
        cls = classify_shared_object(obj)
        if cls == "STATIC":
            static_sources.append(obj)
        elif cls == "RIGID":
            rigid_sources.append(obj)
        else:
            snapshot_sources.append(obj)

    log("")
    log("Scene classification:")
    log(f"  Shared visible geometry: {len(shared_sources)}")
    log(f"  STATIC once: {len(static_sources)}")
    log(f"  RIGID transform animation: {len(rigid_sources)}")
    log(f"  SNAPSHOT deformation animation: {len(snapshot_sources)}")

    exported_objects = []
    animated_objects = []

    # -------------------------------------------------------------------------
    # 2. Static geometry: bake exactly once.
    # -------------------------------------------------------------------------
    depsgraph = bpy.context.evaluated_depsgraph_get()

    for src in static_sources:
        try:
            obj, mode = make_temp_object(
                src, depsgraph, temp_collection, material_cache, "__STATIC"
            )
            exported_objects.append(obj)
            log(f"  STATIC {src.name}: {mode}")
        except Exception as e:
            log(f"  ERROR STATIC {src.name}: {type(e).__name__}: {e}")
            log(traceback.format_exc())

    # -------------------------------------------------------------------------
    # 3. Rigid geometry: bake mesh once, animate evaluated world transform.
    # -------------------------------------------------------------------------
    rigid_temp = {}

    for src in rigid_sources:
        try:
            obj, mode = make_temp_object(
                src, depsgraph, temp_collection, material_cache, "__RIGID"
            )
            rigid_temp[src.name] = obj
            exported_objects.append(obj)
            animated_objects.append(obj)
            log(f"  RIGID {src.name}: {mode}")
        except Exception as e:
            log(f"  ERROR RIGID INIT {src.name}: {type(e).__name__}: {e}")
            log(traceback.format_exc())

    # Keyframe rigid transforms over all frames.
    for frame in FRAMES:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        for src in rigid_sources:
            obj = rigid_temp.get(src.name)
            if obj is None:
                continue
            try:
                eval_obj = src.evaluated_get(depsgraph)
                visible = is_exportable_visible_geometry(src)
                keyframe_trs(obj, frame, eval_obj.matrix_world, visible=visible)
            except Exception as e:
                log(f"  ERROR RIGID KEY frame {frame} {src.name}: {e}")

    for obj in rigid_temp.values():
        set_action_interpolation(obj, "LINEAR")

    # -------------------------------------------------------------------------
    # 4. Deformable geometry: bake one static snapshot per frame and toggle it.
    # -------------------------------------------------------------------------
    deform_snapshot_count = 0

    for frame in FRAMES:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        for src in snapshot_sources:
            if not is_exportable_visible_geometry(src):
                continue

            try:
                obj, mode = make_temp_object(
                    src,
                    depsgraph,
                    temp_collection,
                    material_cache,
                    f"__DEFORM_FRAME_{frame:04d}",
                )
                base_scale = tuple(obj.scale)
                keyframe_snapshot_visibility(obj, frame, base_scale)
                set_scale_curve_constant(obj)

                obj["AuRaSim_Frame"] = int(frame)
                exported_objects.append(obj)
                animated_objects.append(obj)
                deform_snapshot_count += 1

            except Exception as e:
                log(f"  ERROR DEFORM frame {frame} {src.name}: {type(e).__name__}: {e}")
                log(traceback.format_exc())

    log(f"  Deformable snapshots created: {deform_snapshot_count}")

    # -------------------------------------------------------------------------
    # 5. PATH_VIS / any explicit frame_XXXX geometry:
    #    include every requested frame, but show each only at its own frame.
    # -------------------------------------------------------------------------
    frame_specific_count = 0

    for frame in FRAMES:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        frame_sources = [
            obj for obj in scene.objects
            if is_exportable_visible_geometry(obj)
            and extract_explicit_frame_number(obj) == int(frame)
        ]

        log(f"  Frame {frame}: frame-specific geometry = {len(frame_sources)}")

        for src in frame_sources:
            try:
                obj, mode = make_temp_object(
                    src,
                    depsgraph,
                    temp_collection,
                    material_cache,
                    f"__FRAME_{frame:04d}",
                )
                base_scale = tuple(obj.scale)
                keyframe_snapshot_visibility(obj, frame, base_scale)
                set_scale_curve_constant(obj)

                obj["AuRaSim_Frame"] = int(frame)
                exported_objects.append(obj)
                animated_objects.append(obj)
                frame_specific_count += 1
                log(f"    FRAME {frame} {src.name}: {mode}")

            except Exception as e:
                log(f"  ERROR FRAME-SPECIFIC {frame} {src.name}: {type(e).__name__}: {e}")
                log(traceback.format_exc())

    log(f"  Frame-specific snapshots created: {frame_specific_count}")

    # -------------------------------------------------------------------------
    # 6. Merge per-object actions into same-named NLA tracks.
    # -------------------------------------------------------------------------
    nla_count = 0
    for obj in animated_objects:
        if push_action_to_shared_nla(obj):
            nla_count += 1

    log(f"  Animated objects pushed to NLA '{MERGED_ANIMATION_NAME}': {nla_count}")

    return exported_objects


# =============================================================================
# MAIN
# =============================================================================


def main():
    scene = bpy.context.scene

    original_frame = scene.frame_current
    original_start = scene.frame_start
    original_end = scene.frame_end
    original_fps = scene.render.fps
    original_fps_base = scene.render.fps_base
    original_selected = [o.name for o in bpy.context.selected_objects]
    original_active = (
        bpy.context.view_layer.objects.active.name
        if bpy.context.view_layer.objects.active else None
    )

    log("=" * 80)
    log("AuRaSim SINGLE ANIMATED GLB EXPORT")
    log("=" * 80)
    log(f"Exporter version: {EXPORTER_VERSION}")
    log(f"Blender: {bpy.app.version_string}")
    log(f"Blend: {bpy.data.filepath or '[Unsaved]'}")
    log(f"Frames: {FRAMES[0]} .. {FRAMES[-1]} ({len(FRAMES)} frames)")
    log(f"Playback FPS: {EXPORT_FPS}")
    log(f"Desktop: {DESKTOP}")

    filepath = DESKTOP / (
        f"{FILE_PREFIX}_all_frames_{FRAMES[0]:04d}_{FRAMES[-1]:04d}.glb"
    )

    try:
        remove_temp_collection_if_exists()
        remove_temp_materials()

        scene.frame_start = FRAMES[0]
        scene.frame_end = FRAMES[-1]
        scene.render.fps = int(EXPORT_FPS)
        scene.render.fps_base = 1.0

        exported_objects = build_all_frames_scene()

        if not exported_objects:
            raise RuntimeError("No temporary export geometry was created")

        bpy.ops.object.select_all(action="DESELECT")
        for obj in exported_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = exported_objects[0]

        log("")
        log(f"Exporting one GLB -> {filepath}")
        export_single_animated_glb(filepath)

        if filepath.exists():
            size_mb = filepath.stat().st_size / (1024 * 1024)
            log(f"DONE: {filepath}")
            log(f"File size: {size_mb:.1f} MiB")

    finally:
        remove_temp_collection_if_exists()
        remove_temp_materials()

        scene.frame_start = original_start
        scene.frame_end = original_end
        scene.render.fps = original_fps
        scene.render.fps_base = original_fps_base
        scene.frame_set(original_frame)
        bpy.context.view_layer.update()

        bpy.ops.object.select_all(action="DESELECT")
        for name in original_selected:
            obj = bpy.data.objects.get(name)
            if obj:
                try:
                    obj.select_set(True)
                except Exception:
                    pass

        if original_active:
            obj = bpy.data.objects.get(original_active)
            if obj:
                bpy.context.view_layer.objects.active = obj

    log_path = DESKTOP / f"{FILE_PREFIX}_single_glb_export_log.txt"
    log_path.write_text("\n".join(LOG), encoding="utf-8")

    print("")
    print("=" * 80)
    print("AuRaSim single animated GLB export finished")
    print(f"GLB: {filepath}")
    print(f"Log: {log_path}")
    print("=" * 80)


main()

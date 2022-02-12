# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Combination Shape Keys",
    "description": "Combination shape keys.",
    "author": "James Snowden",
    "version": (1, 0, 0),
    "blender": (2, 90, 0),
    "location": "View3D",
    "doc_url": "",
    "category": "Animation",
    "warning": ""
}

import contextlib
import itertools
import math
from this import d
import typing
import uuid
import bpy
import mathutils
from .lib import curve_mapping
from .lib.transform_utils import (ROTATION_MODE_ITEMS,
                                  ROTATION_MODE_INDEX,
                                  TRANSFORM_TYPE_ITEMS,
                                  TRANSFORM_TYPE_INDEX,
                                  TRANSFORM_SPACE_ITEMS,
                                  TRANSFORM_SPACE_INDEX,
                                  transform_target,
                                  transform_target_distance,
                                  transform_target_rotational_difference,
                                  transform_matrix,
                                  transform_matrix_element)

curve_mapping.BLCMAP_OT_curve_copy.bl_idname = "csk.curve_copy"
curve_mapping.BLCMAP_OT_curve_paste.bl_idname = "csk.curve_paste"
curve_mapping.BLCMAP_OT_curve_edit.bl_idname = "csk.curve_edit"

#
#
#

CURVE_BUFFER: typing.Dict[str, typing.Union[str, typing.List[typing.Dict[str, typing.Any]]]] = None
VARIABLE_BUFFER: typing.List[typing.Dict[str, typing.Any]] = []

ID_TYPE_ITEMS = [
    ('OBJECT'      , "Object"  , "", 'OBJECT_DATA',                0),
    ('MESH'        , "Mesh"    , "", 'MESH_DATA',                  1),
    ('CURVE'       , "Curve"   , "", 'CURVE_DATA',                 2),
    ('META'        , "Metaball", "", 'META_DATA',                  3),
    ('FONT'        ,  "Font"    , "", 'FONT_DATA',                 4),
    ('VOLUME'      , "Volume"  , "", 'VOLUME_DATA',                5),
    ('GREASEPENCIL', "GPencil" , "", 'OUTLINER_DATA_GREASEPENCIL', 6),
    ('ARMATURE'    , "Armature", "", 'ARMATURE_DATA',              7),
    ('LATTICE'     , "Lattice" , "", 'LATTICE_DATA',               8),
    ('LIGHT'       , "Light"   , "", 'LIGHT_DATA',                 9),
    ('LIGHT_PROBE' , "Light"   , "", 'OUTLINER_DATA_LIGHTPROBE',   10),
    ('CAMERA'      , "Camera"  , "", 'CAMERA_DATA',                11),
    ('SPEAKER'     , "Speaker" , "", 'OUTLINER_DATA_SPEAKER',      12),
    ('KEY'         , "Key"     , "", 'SHAPEKEY_DATA',              13),
    ]

ID_TYPE_INDEX: typing.Dict[str, int] = {
    item[0]: item[4] for item in ID_TYPE_ITEMS
    }

BLEND_DATA_LUT: typing.Dict[str, str] = {
    'ARMATURE'      : "armatures",
    'CAMERA'        : "cameras",
    'CURVE'         : "curves",
    'FONT'          : "fonts",
    'GREASEPENCIL'  : "grease_pencils",
    'KEY'           : "shape_keys",
    'LIGHT'         : "light",
    'LATTICE'       : "lattices",
    'META'          : "metaballs",
    'MESH'          : "meshes",
    'OBJECT'        : "objects",
    'LIGHT_PROBE'   : "lightprobes",
    'SPEAKER'       : "speakers",
    'VOLUME'        : "volumes",
    }

#
#
#

def driver_find(id: bpy.types.ID, path: str, index: typing.Optional[int]=None) -> typing.Optional[bpy.types.FCurve]:
    animdata = id.animation_data
    if animdata is not None:
        drivers = animdata.drivers
        return drivers.find(path) if index is None else drivers.find(path, index=index)

def driver_ensure(id: bpy.types.ID, path: str, index: typing.Optional[int]=None) -> bpy.types.FCurve:
    fcurve = driver_find(id, path, index)
    if fcurve is None:
        drivers = id.animation_data_create().drivers
        fcurve = drivers.new(path) if index is None else drivers.new(path, index=index)
    return fcurve

def driver_remove(id: bpy.types.ID, path: str, index: typing.Optional[int]=None) -> None:
    fcurve = driver_find(id, path, index)
    if fcurve is not None:
        fcurve.id_data.animation_data.drivers.remove(fcurve)

def idprop_remove(owner: typing.Union[bpy.types.ID, bpy.types.PoseBone, bpy.types.Bone], name: str) -> None:
    try:
        del owner[name]
    except KeyError: pass
    if bpy.app.version[0] < 3:
        try:
            del owner["_RNA_UI"][name]
        except KeyError: pass

#
#
#

#region Properties
###################################################################################################

class DriverTarget(bpy.types.PropertyGroup):

    def update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        self.id_data.path_resolve(self.path_from_id().rpartition(".targets.")[0]).update(context)

    bone_target: bpy.props.StringProperty(
        name="Bone",
        default="",
        update=update,
        options=set()
        )

    data_path: bpy.props.StringProperty(
        name="Path",
        default="",
        update=update,
        options=set()
        )

    id_type: bpy.props.EnumProperty(
        name="Type",
        items=ID_TYPE_ITEMS,
        default='OBJECT',
        update=update,
        options=set()
        )

    @property
    def id(self) -> typing.Optional[bpy.types.ID]:
        object = self.object
        if object is not None:
            idtype = self.id_type
            if idtype == 'OBJECT'   : return object
            if idtype == 'KEY'      : return object.data.shape_keys if object.type == 'MESH' else None
            if idtype == object.type: return object.data

    object: bpy.props.PointerProperty(
        name="Object",
        type=bpy.types.Object,
        update=update,
        options=set()
        )

    rotation_mode: bpy.props.EnumProperty(
        name="Mode",
        items=ROTATION_MODE_ITEMS,
        default='AUTO',
        update=update,
        options=set()
        )

    shape_target: bpy.props.StringProperty(
        name="Shape",
        default="",
        options=set(),
        update=update
        )

    transform_space: bpy.props.EnumProperty(
        name="Space",
        items=TRANSFORM_SPACE_ITEMS,
        default='WORLD_SPACE',
        update=update,
        options=set()
        )

    transform_type: bpy.props.EnumProperty(
        name="Type",
        items=TRANSFORM_TYPE_ITEMS,
        default='LOC_X',
        update=update,
        options=set()
        )

class DriverTargets(bpy.types.PropertyGroup):

    data__internal__: bpy.props.CollectionProperty(
        type=DriverTarget,
        options={'HIDDEN'}
        )

    size__internal__: bpy.props.IntProperty(
        min=1,
        max=2,
        default=1,
        options={'HIDDEN'}
        )

    def __contains__(self, target: typing.Any) -> bool:
        return any((item == target for item in self))

    def __len__(self) -> int:
        return self.size__internal__

    def __iter__(self) -> typing.Iterator[DriverTarget]:
        return iter(self.data__internal__[:self.size__internal__])

    def __getitem__(self, key: typing.Union[int, slice]) -> typing.Union[DriverTarget, typing.List[DriverTarget]]:

        if isinstance(key, int):
            if 0 < key > self.size__internal__: raise IndexError(f'{self.size__internal__}')
            return self.data__internal__[key]

        if isinstance(key, slice):
            return self.data__internal__[key]

        raise TypeError((f'{self.__class__.__name__}[key]: '
                         f'Expected key to be int or slice, not {key.__class__.__name__}'))

class DriverVariable(bpy.types.PropertyGroup):

    def update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        self.targets.size__internal__ = int(self.type.endswith('DIFF')) + 1
        if self.type == 'SHAPEKEY':
            target = self.targets[0]
            target["id_type"] = ID_TYPE_INDEX['KEY']
            target["data_path"] = f'key_blocks["{target.shape_target}"].value'
        self.id_data.path_resolve(self.path_from_id().rpartition(".variables.")[0]).update(context)

    def pose_value_get(self) -> float:
        return self.pose_value

    def pose_value_set(self, value: float) -> None:
        self.pose_value = value

    def rest_value_get(self) -> float:
        return self.rest_value

    def rest_value_set(self, value: float) -> None:
        self.rest_value = value

    pose_angle: bpy.props.FloatProperty(
        name="Pose",
        subtype='ANGLE',
        precision=3,
        options=set(),
        get=pose_value_get,
        set=pose_value_set,
        )

    pose_value: bpy.props.FloatProperty(
        name="Pose",
        default=1.0,
        precision=3,
        options=set(),
        update=update
        )

    name: bpy.props.StringProperty(
        name="Name",
        default="var",
        update=update,
        options=set()
        )

    show_expanded: bpy.props.BoolProperty(
        name="Expand",
        default=False,
        options=set()
        )

    rest_angle: bpy.props.FloatProperty(
        name="Rest",
        subtype='ANGLE',
        precision=3,
        options=set(),
        get=rest_value_get,
        set=rest_value_set,
        )

    rest_value: bpy.props.FloatProperty(
        name="Rest",
        default=0.0,
        precision=3,
        options=set(),
        update=update
        )

    targets: bpy.props.PointerProperty(
        name="Targets",
        type=DriverTargets,
        options=set()
        )

    type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('SHAPEKEY'     , "Shape Key"            , "Use the value of a shape key"                , 'SHAPEKEY_DATA',                0),
            ('SINGLE_PROP'  , "Single Property"      , "Use the value from some RNA property"        , 'RNA',                          1),
            ('TRANSFORMS'   , "Transform Channel"    , "Final transformation value of object or bone", 'DRIVER_TRANSFORM',             2),
            ('ROTATION_DIFF', "Rotational Difference", "Use the angle between two bones"             , 'DRIVER_ROTATIONAL_DIFFERENCE', 3),
            ('LOC_DIFF'     , "Distance"             , "Distance between two bones or objects"       , 'DRIVER_DISTANCE',              4),
            ],
        default='SHAPEKEY',
        update=update,
        options=set()
        )

    @property
    def value(self) -> float:
        type = self.type

        if type == 'SHAPEKEY':
            id = self.targets[0].id
            if isinstance(id, bpy.types.Key):
                with contextlib.suppress(KeyError):
                    return id.key_blocks[self.targets[0].shape_target].value

        elif type == 'SINGLE_PROP':
            id = self.targets[0].id
            if isinstance(id, bpy.types.ID):
                with contextlib.suppress(ValueError):
                    value = id.path_resolve(self.targets[0].data_path)
                    if isinstance(value, float): return value

        elif type == 'TRANSFORMS':
            target = self.targets[0]
            object = transform_target(target.object, target.bone_target)
            if object:
                matrix = transform_matrix(object, target.transform_space)
                return transform_matrix_element(matrix, target.transform_type, target.rotation_mode, driver=True)

        elif type == 'ROTATION_DIFF':
            t1 = self.targets[0]
            t2 = self.targets[1]
            o1 = transform_target(t1.object, t1.bone_target)
            o2 = transform_target(t2.object, t2.bone_target)
            if o1 and o2:
                return transform_target_rotational_difference(o1, o2)

        elif type == 'LOC_DIFF':
            t1 = self.targets[0]
            t2 = self.targets[1]
            o1 = transform_target(t1.object, t1.bone_target)
            o2 = transform_target(t2.object, t2.bone_target)
            if o1 and o2:
                return transform_target_distance(o1, o2)

        return 0.0

class DriverVariables(bpy.types.PropertyGroup):

    data__internal__: bpy.props.CollectionProperty(
        type=DriverVariable,
        options={'HIDDEN'}
        )

    def __contains__(self, name: str) -> bool:
        return any((var.name == name for var in self))

    def __len__(self) -> int:
        return len(self.data__internal__)

    def __iter__(self) -> typing.Iterator[DriverVariable]:
        return iter(self.data__internal__)

    def __getitem__(self, key: typing.Union[str, int, slice]) -> typing.Union[DriverVariable, typing.List[DriverVariable]]:
        if isinstance(key, str):
            variable = next((var for var in self if var.name == key), None)
            if variable is None:
                raise KeyError(f'{self.__class__.__name__}[key]: "{key}" not found.')
            return variable

        if isinstance(key, int):
            if 0 > key >= len(self):
                raise IndexError((f'{self.__class__.__name__}[key]: '
                                  f'Index {key} out of range 0-{len(self)}.'))

            return self.data__internal__[key]

        if isinstance(key, slice):
            return self.data__internal__[key]

        raise TypeError((f'{self.__class__.__name__}[key]: '
                         f'Expected key to be str, int or slice, not {key.__class__.__name__}.'))

    def find(self, name: str) -> int:
        return next((i for i, var in enumerate(self) if var.name == name), -1)

    def get(self, name: str, default: typing.Optional[object]=None) -> typing.Any:
        return self.data__internal__.get(name, default)

    def new(self) -> DriverVariable:
        variable = self.data__internal__.add()
        variable.targets.data__internal__.add()
        self.id_data.path_resolve(self.path_from_id().rpartition(".")[0]).update()
        return variable

    def remove(self, variable: DriverVariable) -> None:
        if not isinstance(variable, DriverVariable):
            raise TypeError((f'{self.__class__.__name__}.remove(variable): '
                             f'Expected variable to be DriverVariable, not {variable.__class__.__name__}'))

        index = next((i for i, x in enumerate(self) if x == variable), -1)
        if index == -1:
            raise ValueError((f'{self.__class__.__name__}.remove(variable): '
                              f'variable is not a member of this collection'))

        self.data__internal__.remove(index)
        self.id_data.path_resolve(self.path_from_id().rpartition(".")[0]).update()

def _driver_name_update_handler(driver: 'Driver', context: bpy.types.Context) -> None:
    collection = driver.id_data.path_resolve(driver.path_from_id().rpartition(".")[0])
    names = [item.name for item in collection if item != driver]
    index = 0
    value = driver.name
    while value in names:
        index += 1
        value = f'{driver.name}.{str(index).zfill(3)}'
    driver["name"] = value

class Driver(bpy.types.PropertyGroup):

    def fcurve_update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        fcurve = driver_ensure(self.id_data, self.data_path, self.array_index)
        points = fcurve.keyframe_points

        count = len(points)

        while count > 2:
            points.remove(points[-1])
            count -= 1

        precision = self.precision
        values = tuple((var.rest_value, round(var.pose_value, precision)) for var in self.variables)
        
        if len(values) == 0:
            curve = (((0., 1.), (-.25, 1.), (.25, .75)),
                     ((1., 0.), (.75, .25), (1.25, 0.)))
        else:
            if self.type == 'QUATERNION':
                value = math.acos((2.0*pow(max(min(sum([r*g for r,g in values]),1.0),-1.0),2.0))-1.0)/math.pi
            elif self.type == 'EUCLIDEAN':
                value = math.sqrt(sum([pow(r-g,2.0) for r,g in values]))
            else:
                value = sum([math.fabs(r-g) for r,g in values])/float(len(values))

            curve = (((0.  , 1.), (-.25, 1.), (value*.25, .75)),
                    ((value, 0.), (value*.75, .25), (value*1.25, 0.)))

        for point, (co, hl, hr) in zip(points, curve):
            point.interpolation = 'BEZIER'
            point.co = co
            point.handle_left_type = 'FREE'
            point.handle_right_type = 'FREE'
            point.handle_left = hl
            point.handle_right = hr

    def driver_update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        fcurve = driver_ensure(self.id_data, self.data_path, self.array_index)
        driver = fcurve.driver
        driver.type = 'SCRIPTED'

        variables = driver.variables
        while len(variables):
            variables.remove(variables[-1])

        symbols = []

        for cskvar in self.variables:
            var = variables.new()
            var.type = 'SINGLE_PROP' if cskvar.type in ('SHAPEKEY', 'SINGLE_PROP') else cskvar.type
            var.name = cskvar.name

            if var.type == 'SINGLE_PROP':
                src = cskvar.targets[0]
                tgt = var.targets[0]
                tgt.id_type = src.id_type
                tgt.id = src.id
                tgt.data_path = src.data_path

            elif var.type == 'TRANSFORMS':
                src = cskvar.targets[0]
                tgt = var.targets[0]
                tgt.id = src.id
                tgt.bone_target = src.bone_target
                tgt.transform_space = src.transform_space
                tgt.transform_type = src.transform_type
                tgt.rotation_mode = src.rotation_mode

            else:
                for src, tgt in zip(cskvar.targets, var.targets):
                    tgt.id = src.id
                    tgt.bone_target = src.bone_target
                    tgt.transform_space = src.transform_space

            symbols.append((var.name, str(round(cskvar.pose_value, self.precision))))

        count = len(symbols)

        if count == 0:
            driver.expression = "1.0"
        elif self.type == 'EUCLIDEAN':
            driver.expression = f'sqrt({"+".join("pow("+v+"-"+g+",2.0)" for v, g in symbols)})'
        elif self.type == 'QUATERNION':
            driver.expression = f'acos((2.0*pow(clamp({"+".join(v+"*"+g for v, g in symbols)},-1.0,1.0),2.0))-1.0)/pi'
        elif count == 1:
            driver.expression = f'fabs({symbols[0][0]}-{symbols[0][1]})'
        else:
            driver.expression = f'({"+".join("fabs("+v+"-"+g+")" for v, g in symbols)})/{str(float(count))}'

    def update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        self.fcurve_update(context)
        self.driver_update(context)

    array_index: bpy.props.IntProperty(
        name="Index",
        get=lambda self: self.get("array_index", 0),
        options=set()
        )

    data_path: bpy.props.StringProperty(
        name="Path",
        get=lambda self: self.get("data_path", ""),
        options=set()
        )

    name: bpy.props.StringProperty(
        name="Name",
        default="",
        options=set(),
        update=_driver_name_update_handler
        )

    precision: bpy.props.IntProperty(
        name="Precision",
        min=3,
        max=28,
        default=6,
        options=set(),
        update=update
        )

    type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('ABSOLUTE'  , "Absolute Difference", "Use the absolute difference between values and goals", 'NONE', 0),
            ('EUCLIDEAN' , "Euclidean Distance" , "Use the euclidean distance between values and goals" , 'NONE', 1),
            ('QUATERNION', "Quaternion Distance", "Use the quaternion distance between values and goals , 'NONE", 2),
            ],
        default='ABSOLUTE',
        options=set(),
        update=driver_update
        )

    show_expanded: bpy.props.BoolProperty(
        name="Expand",
        default=False,
        options=set()
        )

    variables: bpy.props.PointerProperty(
        name="Variables",
        type=DriverVariables,
        options=set()
        )

class Drivers(bpy.types.PropertyGroup):

    active_index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set(),
        )

    @property
    def active(self) -> typing.Optional[Driver]:
        index = self.active_index
        return self[index] if index < len(self) else None

    data__internal__: bpy.props.CollectionProperty(
        type=Driver,
        options={'HIDDEN'}
        )

    def __len__(self) -> int:
        return len(self.data__internal__)

    def __iter__(self) -> typing.Iterator[Driver]:
        return iter(self.data__internal__)

    def __getitem__(self, key: typing.Union[str, int, slice]) -> typing.Union[Driver, typing.List[Driver]]:
        return self.data__internal__[key]

    def find(self, name: str) -> int:
        return self.data__internal__.find(name)

    def get(self, name: str, default: typing.Optional[object]=None) -> typing.Any:
        return self.data__internal__.get(name, default)

class TargetFalloff(curve_mapping.BCLMAP_CurveManager, bpy.types.PropertyGroup):

    def update(self, context: typing.Optional[bpy.types.Context] = None) -> None:
        super().update(context)
        self.id_data.path_resolve(self.path_from_id().rpartition(".")[0]).fcurve_update()

class Target(bpy.types.PropertyGroup):

    def name_get(self) -> str:
        return self.get("name", "")

    def name_set(self, value: str) -> None:
        previous_value = self.name_get()
        if previous_value != value:
            self["name"] = value
            if previous_value:
                key = self.id_data.shape_keys
                if key:
                    driver_remove(key, f'key_blocks.["{previous_value}"].value')
            self.update()

    def fcurve_update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        if self.is_valid:
            fcurve = driver_ensure(self.id, self.data_path)
            dcurve: curve_mapping.BLCMAP_Curve = self.falloff.curve

            bezier = curve_mapping.to_bezier(dcurve.points,
                                             x_range=(1.0-self.radius, 1.0),
                                             y_range=(0.0, self.goal),
                                             extrapolate=not self.clamp)

            curve_mapping.keyframe_points_assign(fcurve.keyframe_points, bezier)

    def driver_update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        if self.is_valid:
            fcurve = driver_ensure(self.id, self.data_path)
            driver = fcurve.driver

            fcurve.mute = self.mute

            variables = driver.variables
            while len(variables):
                variables.remove(variables[-1])

            for index in range(len(self.drivers)):
                variable = variables.new()
                variable.name = f'd{index}'
                variable.type = 'SINGLE_PROP'

                target = variable.targets[0]
                target.id_type = 'MESH'
                target.id = self.id_data
                target.data_path = f'["csk_{self.identifier}"][{index}]'

            if self.activation_mode == 'MULTIPLY':
                driver.type = 'SCRIPTED'
                driver.expression = "*".join(variables.keys())
            else:
                driver.type = self.activation_mode

    def update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        self.fcurve_update()
        self.driver_update()

    activation_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ('MULTIPLY', "Multiply", "Activate the target shape keys by multiplying the driver values"        , 'NONE', 0),
            ('MIN'     , "Minimum" , "Activate the target shape key based on the lowest driver value"         , 'NONE', 1),
            ('MAX'     , "Maximum" , "Activate the target shape key based on the highest driver value"        , 'NONE', 2),
            ('AVERAGE' , "Average" , "Activate the target shape key based on the average of the driver values", 'NONE', 3),
            ],
        default='MULTIPLY',
        options=set(),
        update=update
        )

    clamp: bpy.props.BoolProperty(
        name="Clamp",
        default=True,
        options=set(),
        update=fcurve_update
        )

    drivers: bpy.props.PointerProperty(
        name="Drivers",
        type=Drivers,
        options=set()
        )

    falloff: bpy.props.PointerProperty(
        name="Falloff",
        type=TargetFalloff,
        options=set()
        )

    identifier: bpy.props.StringProperty(
        name="Identifier",
        get=lambda self: self.get("identifier", ""),
        options=set()
        )

    @property
    def is_valid(self) -> bool:
        key = self.id
        return key is not None and self.name in key.key_blocks

    mute: bpy.props.BoolProperty(
        name="Mute",
        default=False,
        options=set(),
        update=driver_update
        )

    name: bpy.props.StringProperty(
        name="Name",
        default="",
        options=set(),
        get=name_get,
        set=name_set,
        )

    @property
    def data_path(self) -> str:
        name = self.name
        return f'key_blocks["{name}"].value' if name else ""

    @property
    def id(self) -> typing.Optional[bpy.types.Key]:
        return self.id_data.shape_keys

    radius: bpy.props.FloatProperty(
        name="Radius",
        min=0.0,
        max=1.0,
        default=1.0,
        precision=3,
        options=set(),
        update=fcurve_update
        )

    goal: bpy.props.FloatProperty(
        name="Goal",
        min=0.0,
        max=10.0,
        default=1.0,
        precision=3,
        options=set(),
        update=fcurve_update
        )

class Manager(bpy.types.PropertyGroup):

    def update(self, context: typing.Optional[bpy.types.Context]=None) -> None:
        if (context is not None
            and context.object is not None
            and context.object.type == 'MESH'
            and context.object.data == self.id_data
            and context.object.data.shape_keys is not None
            and self.active is not None
            and self.active.name in context.object.data.shape_keys.key_blocks
            ):
            context.object.active_shape_key_index = context.object.data.shape_keys.key_blocks.find(self.active.name)

    active_index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set(),
        update=update
        )

    @property
    def active(self) -> typing.Optional[Target]:
        index = self.active_index
        return self[index] if index < len(self) else None

    data__internal__: bpy.props.CollectionProperty(
        type=Target,
        options={'HIDDEN'}
        )

    tree__internal__: bpy.props.PointerProperty(
        type=bpy.types.NodeTree,
        options={'HIDDEN'}
        )

    def get_identifier(self) -> str:
        identifier = self.get("identifier", "")
        if not identifier:
            identifier = uuid.uuid4().hex
            self["identifier"] = identifier
        return identifier

    identifier: bpy.props.StringProperty(
        name="Identifier",
        get=get_identifier,
        options=set()
        )

    def __len__(self) -> int:
        return len(self.data__internal__)

    def __iter__(self) -> typing.Iterator[Target]:
        return iter(self.data__internal__)

    def __getitem__(self, key: typing.Union[str, int, slice]) -> typing.Union[Target, typing.List[Target]]:
        return self.data__internal__[key]

    def find(self, name: str) -> int:
        return self.data__internal__.find(name)

    def get(self, name: str, default: typing.Optional[object]=None) -> typing.Any:
        return self.data__internal__.get(name, default)

class ShapeKeyTarget(bpy.types.PropertyGroup):

    is_selected: bpy.props.BoolProperty(
        name="Selected",
        default=False,
        options=set()
        )

#endregion Properties

#region Operators
###################################################################################################

class ActiveTargetOperator:

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return (object is not None
                and object.type == 'MESH'
                and object.data.is_property_set("combination_shape_keys")
                and object.data.combination_shape_keys.active is not None)

class ActiveDriverOperator:

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return (object is not None
                and object.type == 'MESH'
                and object.data.is_property_set("combination_shape_keys")
                and object.data.combination_shape_keys.active is not None
                and object.data.combination_shape_keys.active.drivers.active is not None)

class DriverVariablesCopy(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_variables_copy"
    bl_label = "Copy"
    bl_description = "Copy driver variables to the buffer"

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        global VARIABLE_BUFFER
        VARIABLE_BUFFER.clear()
        for variable in context.object.data.combination_shape_keys.active.drivers.active.variables:
            VARIABLE_BUFFER.append({
                "pose_value": variable.pose_value,
                "name": variable.name,
                "show_expanded": variable.show_expanded,
                "rest_value": variable.rest_value,
                "targets": [{
                    "bone_target": variable.bone_target,
                    "data_path": variable.data_path,
                    "id_type": variable.get("id_type", ID_TYPE_INDEX['OBJECT']),
                    "object": variable.object,
                    "rotation_mode": variable.get("rotation_mode", ROTATION_MODE_INDEX['AUTO']),
                    "shape_target": variable.shape_target,
                    "transform_space": variable.get("transform_space", TRANSFORM_SPACE_INDEX['WORLD_SPACE']),
                    "transform_type": variable.get("transform_type", TRANSFORM_TYPE_INDEX['LOC_X']),
                    } for target in variable.targets],
                "type": variable.get("type", 0)
                })
        return {'FINISHED'}

class DriverVariablesPaste(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_variables_paste"
    bl_label = "Paste"
    bl_description = "Paste driver variables from the buffer"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return super().poll(context) and bool(VARIABLE_BUFFER)

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        driver: Driver = context.object.data.combination_shape_keys.active.drivers.active
        variables = driver.variables
        for item in VARIABLE_BUFFER:
            variable = variables.data__internal__.add()
            for key, value in item.items():
                if key == "targets":
                    variable.targets.size__internal__ = len(value)
                    for source, target in zip(value, variable.targets):
                        for k, v in source.items():
                            target[k] = v
                else:
                    variable[key] = value
        driver.update()
        return {'FINISHED'}

class DriverVariableAdd(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_variable_add"
    bl_label = "Add"
    bl_description = "Add a variable to the active combination shape key driver"

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        context.object.data.combination_shape_keys.active.drivers.active.variables.new()
        return {'FINISHED'}

class DriverVariableRemove(bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_variable_remove"
    bl_label = "Remove"
    bl_description = "Remove a variable from the active combination shape key driver"

    index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set()
        )

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        variables = context.object.data.combination_shape_keys.active.drivers.active.variables
        try:
            variables.remove(variables[self.index])
        except IndexError:
            self.report({'ERROR'}, "Index out of range.")
            return {'CANCELLED'}
        return {'FINISHED'}

class DriverVariableValueUpdate(bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_variable_value_update"
    bl_label = "Update"
    bl_description = "Update the value to the current variable value"

    index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set()
        )

    value: bpy.props.EnumProperty(
        name="Value",
        items=[
            ('REST', "Rest", "", 'NONE', 0),
            ('POSE', "Pose", "", 'NONE', 1),
            ],
        default='REST',
        options=set()
        )

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        variables = context.object.data.combination_shape_keys.active.drivers.active.variables
        try:
            variable: DriverVariable = variables[self.index]
        except IndexError:
            self.report({'ERROR'}, "Index out of range.")
            return {'CANCELLED'}
        else:
            if self.value == 'REST':
                variable.rest_value = variable.value
            else:
                variable.pose_value = variable.value
        return {'FINISHED'}

class DriverAdd(ActiveTargetOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_add"
    bl_label = "Add Driver"
    bl_description = "Add a driver to the active combination shape key"

    bone_target: bpy.props.StringProperty(
        name="Bone",
        default="",
        options=set(),
        )

    bone_target_diff: bpy.props.StringProperty(
        name="Bone",
        default="",
        options=set()
        )

    data_path: bpy.props.StringProperty(
        name="Path",
        default="",
        options=set()
        )

    id_type: bpy.props.EnumProperty(
        name="Type",
        items=ID_TYPE_ITEMS,
        default='OBJECT',
        options=set()
        )

    object: bpy.props.StringProperty(
        name="Object",
        default="",
        options=set()
        )

    object_diff: bpy.props.StringProperty(
        name="Object",
        default="",
        options=set()
        )

    rotation_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ('AUTO'      , "Auto Euler", "Euler using the rotation order of the target", 'NONE',  0),
            ('XYZ'       , "XYZ Euler" , "Euler using the XYZ rotation order"          , 'NONE',  1),
            ('XZY'       , "XZY Euler" , "Euler using the XZY rotation order"          , 'NONE',  2),
            ('YXZ'       , "YXZ Euler" , "Euler using the YXZ rotation order"          , 'NONE',  3),
            ('YZX'       , "YZX Euler" , "Euler using the YZX rotation order"          , 'NONE',  4),
            ('ZXY'       , "ZXY Euler" , "Euler using the ZXY rotation order"          , 'NONE',  5),
            ('ZYX'       , "ZYX Euler" , "Euler using the ZYX rotation order"          , 'NONE',  6),
            ('QUATERNION', "Quaternion", "Quaternion rotation"                         , 'NONE',  7),
            ('SWING_X'   , "X Swing"   , "Swing rotation to aim the X axis"            , 'NONE',  8),
            ('SWING_Y'   , "Y Swing"   , "Swing rotation to aim the Y axis"            , 'NONE',  9),
            ('SWING_Z'   , "Z Swing"   , "Swing rotation to aim the Z axis"            , 'NONE', 10),
            ('TWIST_X'   , "X Twist"   , "Twist around the X axis"                     , 'NONE', 11),
            ('TWIST_Y'   , "Y Twist"   , "Twist around the Y axis"                     , 'NONE', 12),
            ('TWIST_Z'   , "Z Twist"   , "Twist around the Z axis"                     , 'NONE', 13),
            ],
        default='AUTO',
        options=set()
        )

    shape_keys_active_index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set()
        )

    shape_keys: bpy.props.CollectionProperty(
        name="Shapes",
        type=ShapeKeyTarget,
        options=set()
        )

    transform_channels: bpy.props.BoolVectorProperty(
        name="Channels",
        size=3,
        subtype='XYZ',
        default=(False, False, False),
        options=set()
        )

    transform_space: bpy.props.EnumProperty(
        name="Space",
        items=TRANSFORM_SPACE_ITEMS,
        default='WORLD_SPACE',
        options=set()
        )

    transform_space_diff: bpy.props.EnumProperty(
        name="Space",
        items=TRANSFORM_SPACE_ITEMS,
        default='WORLD_SPACE',
        options=set()
        )

    transform_type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('LOC'  , "Location", "", 'NONE', 0),
            ('ROT'  , "Rotation", "", 'NONE', 1),
            ('SCALE', "Scale"   , "", 'NONE', 2),
            ],
        default='LOC',
        options=set()
        )

    type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('SHAPEKEY'     , "Shape Key"            , "", 'NONE', 0),
            ('SINGLE_PROP'  , "Single Property"      , "", 'NONE', 1),
            ('TRANSFORMS'   , "Transform Channels"   , "", 'NONE', 2),
            ('ROTATION_DIFF', "Rotational Difference", "", 'NONE', 3),
            ('LOC_DIFF'     , "Distance"             , "", 'NONE', 4),
            ],
        default='SHAPEKEY',
        options=set()
        )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> typing.Set[str]:
        source = context.object.data.shape_keys
        target = context.object.data.combination_shape_keys.active.name
        shapes = self.shape_keys
        shapes.clear()
        if source:
            for name in source.key_blocks.keys():
                if name != target:
                    shapes.add().name = name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.prop(self, "type", text="")
        layout.separator()

        type = self.type
        data = context.blend_data

        if type == 'SHAPEKEY':
            layout.template_list(ShapeKeyTargetList.bl_idname, "",
                                 self, "shape_keys",
                                 self, "shape_keys_active_index")

        elif type == 'TRANSFORMS':

            split = layout.row().split(factor=0.3)
            split.label(text="Object")

            col = split.column(align=True)
            col.prop_search(self, "object", data, "objects", text="")

            obj = data.objects.get(self.object)
            if obj and obj.type == 'ARMATURE':
                col.prop_search(self, "bone_target", obj.data, "bones", text="")

            layout.separator()

            split = layout.row().split(factor=0.3)
            split.label(text="Channels")

            col = split.column()
            col.prop(self, "transform_type", text="")

            type = self.transform_type

            if type == 'ROT':
                col.prop(self, "rotation_mode", text="")

            col.prop(self, "transform_space", text="")

            if type in ('LOCATION', 'SCALE') or len(self.rotation_mode) <= 4:
                col.row(align=True).prop(self, "transform_channels", text="", toggle=True)

        elif type == 'SINGLE_PROP':

            split = layout.row().split(factor=0.3)
            split.label(text="Object")

            col = split.column(align=True)

            row = col.row(align=True)
            row.prop(self, "id_type", text="", icon_only=True)
            row.prop_search(self, "object", context.blend_data, BLEND_DATA_LUT[self.id_type], text="")

            col.prop(self, "data_path", icon='RNA', text="")

        else:

            split = layout.row().split(factor=0.3)
            split.label(text="Object")

            col = split.column(align=True)
            col.prop_search(self, "object", data, "objects", text="")

            obj = data.objects.get(self.object)
            if obj and obj.type == 'ARMATURE':
                col.prop_search(self, "bone_target", obj.data, "bones", text="")

            if type == 'LOC_DIFF':
                split = layout.row().split(factor=0.3)
                split.label(text="Space")
                split.prop(self, "transform_space", text="")

            layout.separator()

            split = layout.row().split(factor=0.3)
            split.label(text="Object")

            col = split.column(align=True)
            col.prop_search(self, "object_diff", data, "objects", text="")

            obj = data.objects.get(self.object_diff)
            if obj and obj.type == 'ARMATURE':
                col.prop_search(self, "bone_target", obj.data, "bones", text="")

            if type == 'LOC_DIFF':
                split = layout.row().split(factor=0.3)
                split.label(text="Space")
                split.prop(self, "transform_space_diff", text="")

        layout.separator()

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        target: Target = context.object.data.combination_shape_keys.active
        type = self.type

        if type == 'SHAPEKEY':
            drivers = target.drivers
            keys = [item.name for item in self.shape_keys if item.is_selected]

            if keys:
                target.id_data[f'csk_{target.identifier}'] = [0.0 for _ in range(len(drivers) + len(keys))]

            for key in keys:
                driver: Driver = drivers.data__internal__.add()
                driver["array_index"] = len(drivers) - 1
                driver["data_path"] = f'csk_{target.identifier}'

                variable: DriverVariable = driver.variables.data__internal__.add()
                variable["type"] = 0
                variable["pose_value"] = context.object.data.shape_keys.key_blocks[key].value

                driver_target: DriverTarget = variable.targets.data__internal__.add()
                driver_target["id_type"] = ID_TYPE_INDEX['KEY']
                driver_target["object"] = context.object
                driver_target["shape_target"] = key
                driver_target["data_path"] = f'key_blocks["{key}"].value'

                driver.update()

        else:
            drivers = target.drivers
            driver: Driver = drivers.data__internal__.add()
            driver["array_index"] = len(drivers) - 1
            driver["data_path"] = f'["csk_{target.identifier}"]'

            target.id_data[f'csk_{target.identifier}'] = [0.0 for _ in range(len(drivers))]

            if type == 'TRANSFORMS':
                type = self.transform_type

                object = context.blend_data.objects.get(self.object)
                if object is None:
                    matrix = mathutils.Matrix(4)
                else:
                    matrix = transform_matrix(transform_target(object, self.bone_target), self.transform_space)

                if type in ('LOC', 'SCALE'):
                    driver["type"] = 1
                    vector = matrix.to_translation() if type == 'LOC' else matrix.to_scale()
                    for axis, goal in itertools.compress(zip('XYZ', vector), self.transform_channels):
                        variable: DriverVariable = driver.variables.data__internal__.add()
                        variable["type"] = 2
                        variable["name"] = axis.lower()
                        variable["pose_value"] = goal

                        driver_target: DriverTarget = variable.targets.data__internal__.add()
                        driver_target["object"] = object
                        driver_target["bone_target"] = self.bone_target
                        driver_target["transform_type"] = 'XYZ'.index(axis)
                        driver_target["transform_space"] = TRANSFORM_SPACE_INDEX[self.transform_space]

                else:
                    mode = self.rotation_mode
                    if mode == 'QUATERNION':
                        driver["type"] = 2

                        for axis, goal in zip('WXYZ', matrix.to_quaternion()):
                            variable: DriverVariable = driver.variables.data__internal__.add()
                            variable["type"] = 2
                            variable["name"] = axis.lower()
                            variable["pose_value"] = goal

                            driver_target: DriverTarget = variable.targets.data__internal__.add()
                            driver_target["object"] = object
                            driver_target["bone_target"] = self.bone_target
                            driver_target["transform_type"] = TRANSFORM_TYPE_INDEX[f'ROT_{axis}']
                            driver_target["transform_space"] = TRANSFORM_SPACE_INDEX[self.transform_space]
                            driver_target["rotation_mode"] = ROTATION_MODE_INDEX["QUATERNION"]

                    elif mode.startswith('SWING'):
                        axis = mode[-1]

                        variable: DriverVariable = driver.variables.data__internal__.add()
                        variable["type"] = 2
                        variable["name"] = axis.lower()
                        variable["pose_value"] = math.acos(matrix.to_quaternion().to_swing_twist(axis)[0][0])*2.0

                        driver_target: DriverTarget = variable.targets.data__internal__.add()
                        driver_target["object"] = object
                        driver_target["bone_target"] = self.bone_target
                        driver_target["transform_type"] = TRANSFORM_TYPE_INDEX['ROT_W']
                        driver_target["transform_space"] = TRANSFORM_SPACE_INDEX[self.transform_space]
                        driver_target["rotation_mode"] = ROTATION_MODE_INDEX[f'SWING_TWIST_{axis}']

                    elif mode.startswith('TWIST'):
                        axis = mode[-1]

                        variable: DriverVariable = driver.variables.data__internal__.add()
                        variable["type"] = 2
                        variable["name"] = axis.lower()
                        variable["pose_value"] = math.acos(matrix.to_quaternion().to_swing_twist(axis)[1])

                        driver_target: DriverTarget = variable.targets.data__internal__.add()
                        driver_target["object"] = object
                        driver_target["bone_target"] = self.bone_target
                        driver_target["transform_type"] = TRANSFORM_TYPE_INDEX[f'ROT_{axis}']
                        driver_target["transform_space"] = TRANSFORM_SPACE_INDEX[self.transform_space]
                        driver_target["rotation_mode"] = ROTATION_MODE_INDEX[f'SWING_TWIST_{axis}']

                    else:
                        order = mode if len(mode) == 3 else None
                        euler = matrix.to_euler(order)

                        for axis, goal in itertools.compress(zip('XYZ', euler), self.transform_channels):
                            variable: DriverVariable = driver.variables.data__internal__.add()
                            variable["type"] = 2
                            variable["name"] = axis.lower()
                            variable["pose_value"] = goal

                            driver_target: DriverTarget = variable.targets.data__internal__.add()
                            driver_target["object"] = object
                            driver_target["bone_target"] = self.bone_target
                            driver_target["transform_type"] = TRANSFORM_TYPE_INDEX[f'ROT_{axis}']
                            driver_target["transform_space"] = TRANSFORM_SPACE_INDEX[self.transform_space]
                            driver_target["rotation_mode"] = ROTATION_MODE_INDEX[mode]

            elif type == 'SINGLE_PROP':
                
                id = None
                object = None
                id_name = self.object
                id_type = self.id_type

                if id_type == 'OBJECT':
                    object = context.blend_data.objects.get(id_name)
                    id = object
                else:
                    for item in context.blend_data.objects:
                        if item.type == id_type and item.data.name == id_name:
                            object = item
                            id = item.data
                            break

                value = 0.0
                if id is not None:
                    try:
                        value = id.path_resolve(self.data_path)
                    except ValueError: pass

                variable: DriverVariable = driver.variables.data__internal__.add()
                variable["type"] = 1
                variable["pose_value"] = value if isinstance(value, float) else 0.0

                driver_target: DriverTarget = variable.targets.data__internal__.add()
                driver_target["id_type"] = ID_TYPE_INDEX[id_type]
                driver_target["object"] = object
                driver_target["data_path"] = self.data_path

            elif type == 'ROTATION_DIFF':
                # TODO
                pass

            else: # type == 'LOCL_DIFF
                # TODO
                pass

            driver.update()
            target.update()

        return {'FINISHED'}

class DriverRemove(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_remove"
    bl_label = "Remove"
    bl_description = "Remove the active driver from the active combination shape key"

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        target: DriverTarget = context.object.data.combination_shape_keys.active
        drivers = target.drivers
        driver: Driver = drivers.active
        driver_remove(target.id_data, driver.data_path, driver.array_index)
        drivers.data__internal__.remove(drivers.active_index)
        target.id_data[f'csk_{target.identifier}'] = [0.0 for _ in range(len(drivers))]
        target.update()
        return {'FINISHED'}

class DriverMoveUp(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_move_up"
    bl_label = "Up"
    bl_description = "Move the active combination shape key driver up within the list"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return super().poll(context) and context.object.data.combination_shape_keys.active.drivers.active_index > 0

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        collection = context.object.data.combination_shape_keys.active.drivers
        prev_index = collection.active_index
        next_index = prev_index - 1
        collection.data__internal__.move(prev_index, next_index)
        collection.active_index = next_index
        return{'FINISHED'}

class DriverMoveDown(ActiveDriverOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.driver_move_down"
    bl_label = "Down"
    bl_description = "Move the active combination shape key driver down within the list"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if super().poll(context):
            collection = context.object.data.combination_shape_keys.active.drivers
            return collection.active_index < len(collection) - 1
        return False

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        collection = context.object.data.combination_shape_keys.active.drivers
        prev_index = collection.active_index
        next_index = prev_index + 1
        collection.data__internal__.move(prev_index, next_index)
        collection.active_index = next_index
        return{'FINISHED'}

class TargetAdd(bpy.types.Operator):

    bl_idname = "combination_shape_key.add"
    bl_label = "Add Combination Shape Key"
    bl_description = "Add a combination shape key"

    def _on_target_update(self, context: bpy.types.Context) -> None:
        index = self.drivers.find(self.target)
        if index != -1:
            self.drivers.remove(index)

    active_driver_index: bpy.props.IntProperty(
        name="Index",
        min=0,
        default=0,
        options=set()
        )

    drivers: bpy.props.CollectionProperty(
        name="Drivers",
        type=ShapeKeyTarget,
        options=set()
        )

    targets: bpy.props.CollectionProperty(
        name="Shapes",
        type=ShapeKeyTarget,
        options=set()
        )

    target: bpy.props.StringProperty(
        name="Target",
        default="",
        options=set(),
        update=_on_target_update
        )

    use_existing: bpy.props.BoolProperty(
        name="Use Existing Shape",
        default=True,
        options=set()
        )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return object is not None and object.type == 'MESH'

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> typing.Set[str]:
        key = context.object.data.shape_keys
        targets = self.targets
        drivers = self.drivers
        targets.clear()
        drivers.clear()
        
        self.active_driver_index = 0
        self.target = ""

        if key:
            for item in key.key_blocks[1:]:
                name = item.name
                drivers.add()["name"] = name
                if driver_find(key, f'key_blocks["{name}"].value') is None:
                    targets.add()["name"] = name

        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.separator()

        row = layout.row()
        row.label(text="Target:")

        subrow = row.row(align=True)
        subrow.alignment = 'RIGHT'
        subrow.label(text="Use Existing")
        subrow.prop(self, "use_existing",
                    icon=f'CHECKBOX_{"" if self.use_existing else "DE"}HLT',
                    text="",
                    emboss=False)

        if self.use_existing:
            layout.prop_search(self, "target", self, "targets", icon='SHAPEKEY_DATA', text="")
        else:
            layout.prop(self, "target", text="", icon='SHAPEKEY_DATA')
        
        layout.separator()
        layout.label(text="Drivers:")
        layout.template_list(ShapeKeyTargetList.bl_idname, "", self, "drivers", self, "active_driver_index")
        layout.separator()


    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        object = context.object
        target: Target = object.data.combination_shape_keys.data__internal__.add()
        target["identifier"] = uuid.uuid4().hex
        target["name"] = self.target

        drivers = [item.name for item in self.drivers if item.is_selected]
        target.id_data[f'csk_{target.identifier}'] = [0.0 for _ in range(len(drivers))]

        if not self.use_existing:
            object.shape_key_add(name=self.target, from_mix=False)

        if drivers:
            for key in drivers:
                driver: Driver = target.drivers.data__internal__.add()
                driver["name"] = key
                driver["array_index"] = len(target.drivers) - 1
                driver["data_path"] = f'["csk_{target.identifier}"]'

                variable: DriverVariable = driver.variables.data__internal__.add()
                variable["type"] = 0
                variable["pose_value"] = object.data.shape_keys.key_blocks[key].value

                driver_target: DriverTarget = variable.targets.data__internal__.add()
                driver_target["id_type"] = ID_TYPE_INDEX['KEY']
                driver_target["object"] = object
                driver_target["shape_target"] = key
                driver_target["data_path"] = f'key_blocks["{key}"].value'

                driver.update()

        target.falloff.__init__()
        target.update()
        return {'FINISHED'}

class TargetRemove(ActiveTargetOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.remove"
    bl_label = "Remove"
    bl_description = "Remove the active combination shape key"

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        mesh = context.object.data
        targets = mesh.combination_shape_keys

        target = targets.active

        for driver in target.drivers:
            driver_remove(mesh, driver.data_path, driver.array_index)

        key = mesh.shape_keys
        if key:
            driver.remove(key, target.data_path)

        idprop_remove(mesh, f'csk_{target.identifier}')
        targets.data__internal__.remove(targets.active_index)
        return {'FINISHED'}

class TargetMoveUp(ActiveTargetOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.move_up"
    bl_label = "Up"
    bl_description = "Move the active combination shape key up within the list"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return super().poll(context) and context.object.data.combination_shape_keys.active_index > 0

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        collection = context.object.data.combination_shape_keys
        prev_index = collection.active_index
        next_index = prev_index - 1
        collection.data__internal__.move(prev_index, next_index)
        collection.active_index = next_index
        return{'FINISHED'}

class TargetMoveDown(ActiveTargetOperator, bpy.types.Operator):

    bl_idname = "combination_shape_key.move_down"
    bl_label = "Down"
    bl_description = "Move the active combination shape key down within the list"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if super().poll(context):
            collection = context.object.data.combination_shape_keys
            return collection.active_index < len(collection) - 1
        return False

    def execute(self, context: bpy.types.Context) -> typing.Set[str]:
        collection = context.object.data.combination_shape_keys
        prev_index = collection.active_index
        next_index = prev_index + 1
        collection.data__internal__.move(prev_index, next_index)
        collection.active_index = next_index
        return{'FINISHED'}

#endregion Operators

#region User Interface
###################################################################################################

class ShapeKeyTargetList(bpy.types.UIList):

    bl_idname = 'UL_shape_key_targets'

    def draw_item(self,
                  context: bpy.types.Context,
                  layout: bpy.types.UILayout,
                  data: bpy.types.CollectionProperty,
                  item: ShapeKeyTarget,
                  icon, active_data, active_property, index, fltflag) -> None:

        row = layout.row()
        row.emboss = 'NONE_OR_STATUS'
        row.label(icon='SHAPEKEY_DATA', text=item.name)

        row = row.row()
        row.alignment = 'RIGHT'
        row.prop(item.id_data.shape_keys.ke_blocks[item.name], "value", text="")
        row.prop(item, "is_selected",
                 text="",
                 icon=f'CHECKBOX_{"" if item.is_selected else "DE"}HLT',
                 emboss=False)

class DriverList(bpy.types.UIList):

    bl_idname = 'UL_drivers'

    def draw_item(self, context: bpy.types.Context, layout: bpy.types.UILayout, data: bpy.types.CollectionProperty, item: Driver, icon, active_data, active_property, index, fltflag) -> None:
        layout.prop(item, "name", icon='DRIVER', text="", emboss=False)

class TargetList(bpy.types.UIList):

    bl_idname = 'UL_targets'

    def draw_item(self, context: bpy.types.Context, layout: bpy.types.UILayout, data: bpy.types.CollectionProperty, item: Target, icon, active_data, active_property, index, fltflag) -> None:
        row = layout.row()
        row.emboss = 'NONE_OR_STATUS'
        row.prop(item, "name", icon='SHAPEKEY_DATA', text="", emboss=False)
        if item.is_valid:
            row = row.row()
            row.alignment = 'RIGHT'
            row.prop(item.id.key_blocks[item.name], "value", text="")
            row.prop(item, "mute", text="", icon=f'CHECKBOX_{"DE" if item.mute else ""}HLT')

class TargetsPanel(bpy.types.Panel):

    bl_idname = "PT_targets"
    bl_label = "Combination Shape Keys"
    bl_description = "Combination shape keys"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return object is not None and object.type == 'MESH' and object.data.shape_keys is not None

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        mesh = context.object.data

        if not mesh.is_property_set("combination_shape_keys"):
            layout.operator(TargetAdd.bl_idname, icon='ADD', text="Add")
            return

        targets = mesh.combination_shape_keys
        row = layout.row()
        col = row.column()
        col.template_list(TargetList.bl_idname, "", targets, "data__internal__", targets, "active_index")

        ops = row.column(align=True)
        ops.operator(TargetAdd.bl_idname, icon='ADD', text="")
        ops.operator(TargetRemove.bl_idname, icon='REMOVE', text="")
        ops.separator()
        ops.operator(TargetMoveUp.bl_idname, icon='TRIA_UP', text="")
        ops.operator(TargetMoveDown.bl_idname, icon='TRIA_DOWN', text="")

        target = targets.active
        if target:
            col.separator(factor=0.25)
            split = col.split(factor=0.3)

            label = split.column()
            label.alignment = 'RIGHT'
            label.label(text="Type")

            value = split.column()
            value.prop(target, "activation_mode", text="")

            col.separator(factor=0.25)
            split = col.split(factor=0.3)

            label = split.column()
            label.alignment = 'RIGHT'
            label.label(text="Goal")

            value = split.column()
            value.prop(target, "goal", text="")

            split = col.split(factor=0.3)

            label = split.column()
            label.separator(factor=0.5)
            label.alignment = 'RIGHT'
            label.label(text="Easing")

            value = split.column()
            curve_mapping.draw_curve_manager_ui(value, target.falloff)

            split = col.split(factor=0.3)

            label = split.column()
            label.alignment = 'RIGHT'
            label.label(text="Radius")

            value = split.column()
            value.prop(target, "radius", text="", slider=True)

class DriversPanel(bpy.types.Panel):

    bl_parent_id = "PT_targets"
    bl_idname = "PT_drivers"
    bl_label = "Drivers"
    bl_description = "Combination shape key drivers"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return (object is not None
                and object.type == 'MESH'
                and object.data.is_property_set("combination_shape_keys")
                and object.data.combination_shape_keys.active is not None)

    def draw(self, context: bpy.types.Context) -> None:
        target = context.object.data.combination_shape_keys.active
        layout = self.layout

        drivers = target.drivers

        row = layout.row()
        col = row.column()
        ops = row.column(align=True)

        col.template_list(DriverList.bl_idname, "",
                          drivers, "data__internal__",
                          drivers, "active_index")

        ops.operator(DriverAdd.bl_idname, text="", icon='ADD')
        ops.operator(DriverRemove.bl_idname, text="", icon='REMOVE')
        ops.separator()
        ops.operator(DriverMoveUp.bl_idname, text="", icon='TRIA_UP')
        ops.operator(DriverMoveDown.bl_idname, text="", icon='TRIA_DOWN')

        driver = target.drivers.active
        if driver:
            row = layout.row()
            col = row.column()
            row.column().label(icon='BLANK1')

            split = col.row().split(factor=0.3)
            label = split.column()
            label.alignment = 'RIGHT'
            label.label(text="Metric")

            value = split.column()
            value.prop(driver, "type", text="")
            value.prop(driver, "precision")

class VariablesPanel(bpy.types.Panel):

    bl_parent_id = "PT_drivers"
    bl_idname = "PT_variables"
    bl_label = "Variables"
    bl_description = "Combination shape key driver variables"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        object = context.object
        return (object is not None
                and object.type == 'MESH'
                and object.data.is_property_set("combination_shape_keys")
                and object.data.combination_shape_keys.active is not None
                and object.data.combination_shape_keys.active.drivers.active is not None)

    def draw(self, context: bpy.types.Context) -> None:
        driver = context.object.data.combination_shape_keys.active.drivers.active
        row = self.layout.row()
        layout = row.column()
        row.column().label(icon='BLANK1')

        row = layout.row(align=True)
        row.operator(DriverVariableAdd.bl_idname, text="Add", icon='ADD')
        row.separator()
        row.operator(DriverVariablesCopy.bl_idname, text="", icon='COPYDOWN')
        row.operator(DriverVariablesPaste.bl_idname, text="", icon='PASTEDOWN')

        layout.separator(factor=0.25)

        for index, variable in enumerate(driver.variables):
            col = layout.column(align=True)                            

            header = col.box().row()
            header.prop(variable, "show_expanded",
                        text="",
                        icon=f'DISCLOSURE_TRI_{"DOWN" if variable.show_expanded else "RIGHT"}',
                        emboss=False)

            subrow = header.row(align=True)
            subrow.prop(variable, "type", text="", icon_only=True)
            subrow.prop(variable, "name", text="")

            header.operator(DriverVariableRemove.bl_idname,
                            text="",
                            icon='X',
                            emboss=False).index=index

            if not variable.show_expanded:
                continue

            body = col.box()
            type = variable.type

            if type == 'SHAPEKEY':
                target = variable.targets[0]

                split = body.row().split(factor=0.2)
                label = split.column()
                label.alignment = 'RIGHT'
                label.label(text="Target")

                data = split.column(align=True)
                data.prop(target, "object", text="", icon='MESH_DATA')

                key = target.id
                if isinstance(key, bpy.types.Key):
                    data.prop_search(target, "shape_target",
                                        key, "key_blocks",
                                        icon='SHAPEKEY_DATA', text="")
                else:
                    data.prop(target, "shape_target", icon='SHAPEKEY_DATA', text="")


            elif type == 'SINGLE_PROP':
                target = variable.targets[0]

                split = body.row().split(factor=0.2)
                label = split.column()
                label.alignment = 'RIGHT'
                label.label(text="Target")

                data = split.column(align=True)
                datarow = data.row(align=True)
                datarow.prop(target, "id_type", text="", icon_only=True)

                datacol = datarow.column(align=True)
                datacol.prop(target, "object", text="")
                datacol.prop(target, "data_path", text="", icon='RNA')

            elif type == 'TRANSFORMS':
                target = variable.targets[0]

                split = body.row().split(factor=0.2)

                label = split.column()
                label.alignment = 'RIGHT'
                label.label(text="Target")

                value = split.column()
                value.prop(target, "object", text="", icon='OBJECT_DATA')

                if target.id is not None and target.id.type == 'ARMATURE':
                    value.prop(target, "bone_target", text="", icon='BONE_DATA')

                split = body.row().split(factor=0.2)

                label = split.column()
                label.alignment = 'RIGHT'
                label.label(text="Channel")

                value = split.column()
                value.prop(target, "transform_type", text="")
                
                if target.transform_type.startswith('ROT'):
                    value.prop(target, "rotation_mode", text="")
                
                value.prop(target, "transform_space", text="")

            else:
                for target, suffix in zip(variable.targets, "AB"):
                    split = body.row().split(factor=0.2)

                    label = split.column()
                    label.alignment = 'RIGHT'
                    label.label(text=f'Target {suffix}')

                    value = split.column()
                    value.prop(target, "object", text="")
                    
                    if target.id is not None and target.id.type == 'ARMATURE':
                        value.prop(target, "bone_target", text="", icon='BONE_DATA')
                    
                    if type == 'LOC_DIFF':
                        value.prop(target, "transform_space", text="")

            split = body.row().split(factor=0.2)

            if (type == 'ROTATION_DIFF'
                or (type == 'TRANSFORMS'
                    and variable.targets[0].transform_type.startswith('ROT')
                    and (len(variable.targets[0].rotation_mode) <= 4
                        or variable.targets[0].rotation_mode.startswith('SWING')))
                ):
                suffix = "angle"
            else:
                suffix = "value"

            label = split.column()
            label.alignment = 'RIGHT'
            label.label(text="Value")

            value = split.column()

            row = value.row()
            row.prop(variable, f'rest_{suffix}')
            props = row.operator(DriverVariableValueUpdate.bl_idname,
                                 text="",
                                 icon='EYEDROPPER',
                                 emboss=False)
            props.index = index
            props.value = 'REST'

            row = value.row()
            row.prop(variable, f'pose_{suffix}')
            props = row.operator(DriverVariableValueUpdate.bl_idname,
                                 text="",
                                 icon='EYEDROPPER',
                                 emboss=False)
            props.index = index
            props.value = 'POSE'

#endregion User Interface

CLASSES = [
    curve_mapping.BLCMAP_CurvePoint,
    curve_mapping.BLCMAP_CurvePoints,
    curve_mapping.BLCMAP_Curve,
    curve_mapping.BLCMAP_OT_curve_copy,
    curve_mapping.BLCMAP_OT_curve_paste,
    curve_mapping.BLCMAP_OT_curve_edit,
    DriverTarget,
    DriverTargets,
    DriverVariable,
    DriverVariables,
    Driver,
    Drivers,
    TargetFalloff,
    Target,
    Manager,
    ShapeKeyTarget,
    DriverVariablesCopy,
    DriverVariablesPaste,
    DriverVariableAdd,
    DriverVariableRemove,
    DriverVariableValueUpdate,
    DriverAdd,
    DriverRemove,
    DriverMoveUp,
    DriverMoveDown,
    TargetAdd,
    TargetRemove,
    TargetMoveUp,
    TargetMoveDown,
    ShapeKeyTargetList,
    DriverList,
    TargetList,
    TargetsPanel,
    DriversPanel,
    VariablesPanel,
    ]

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Mesh.combination_shape_keys = bpy.props.PointerProperty(
        name="Combination Shape Keys",
        type=Manager,
        options=set()
        )

def unregister():
    try:
        del bpy.types.Mesh.combination_shape_keys
    except: pass

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

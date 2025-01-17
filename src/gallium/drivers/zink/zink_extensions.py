# Copyright © 2020 Hoe Hao Cheng
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice (including the next
# paragraph) shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
# 

import re
from xml.etree import ElementTree
from typing import List,Tuple

class Version:
    device_version = (1,0,0)
    struct_version = (1,0)

    def __init__(self, version, struct=()):
        self.device_version = version

        if not struct:
            self.struct_version = (version[0], version[1])
        else:
            self.struct_version = struct

    # e.g. "VK_MAKE_VERSION(1,2,0)"
    def version(self):
        return ("VK_MAKE_VERSION("
               + str(self.device_version[0])
               + ","
               + str(self.device_version[1])
               + ","
               + str(self.device_version[2])
               + ")")

    # e.g. "10"
    def struct(self):
        return (str(self.struct_version[0])+str(self.struct_version[1]))

    # the sType of the extension's struct
    # e.g. VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TRANSFORM_FEEDBACK_FEATURES_EXT
    # for VK_EXT_transform_feedback and struct="FEATURES"
    def stype(self, struct: str):
        return ("VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_"
                + str(self.struct_version[0]) + "_" + str(self.struct_version[1])
                + '_' + struct)

class Extension:
    name           = None
    alias          = None
    is_required    = False
    is_nonstandard = False
    enable_conds   = None

    # these are specific to zink_device_info.py:
    has_properties = False
    has_features   = False
    guard          = False

    # these are specific to zink_instance.py:
    core_since     : Version   = None
    instance_funcs : List[str] = None

    def __init__(self, name, alias="", required=False, nonstandard=False,
                 properties=False, features=False, conditions=None, guard=False,
                 core_since=None, functions=None):
        self.name = name
        self.alias = alias
        self.is_required = required
        self.is_nonstandard = nonstandard
        self.has_properties = properties
        self.has_features = features
        self.enable_conds = conditions
        self.guard = guard
        self.core_since = core_since
        self.instance_funcs = functions

        if alias == "" and (properties == True or features == True):
            raise RuntimeError("alias must be available when properties and/or features are used")

    # e.g.: "VK_EXT_robustness2" -> "robustness2"
    def pure_name(self):
        return '_'.join(self.name.split('_')[2:])
    
    # e.g.: "VK_EXT_robustness2" -> "EXT_robustness2"
    def name_with_vendor(self):
        return self.name[3:]
    
    # e.g.: "VK_EXT_robustness2" -> "Robustness2"
    def name_in_camel_case(self):
        return "".join([x.title() for x in self.name.split('_')[2:]])
    
    # e.g.: "VK_EXT_robustness2" -> "VK_EXT_ROBUSTNESS2_EXTENSION_NAME"
    # do note that inconsistencies exist, i.e. we have
    # VK_EXT_ROBUSTNESS_2_EXTENSION_NAME defined in the headers, but then
    # we also have VK_KHR_MAINTENANCE1_EXTENSION_NAME
    def extension_name(self):
        return self.name.upper() + "_EXTENSION_NAME"

    # generate a C string literal for the extension
    def extension_name_literal(self):
        return '"' + self.name + '"'

    # get the field in zink_device_info that refers to the extension's
    # feature/properties struct
    # e.g. rb2_<suffix> for VK_EXT_robustness2
    def field(self, suffix: str):
        return self.alias + '_' + suffix

    def physical_device_struct(self, struct: str):
        if self.name_in_camel_case().endswith(struct):
            struct = ""

        return ("VkPhysicalDevice"
                + self.name_in_camel_case()
                + struct
                + self.vendor())

    # the sType of the extension's struct
    # e.g. VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TRANSFORM_FEEDBACK_FEATURES_EXT
    # for VK_EXT_transform_feedback and struct="FEATURES"
    def stype(self, struct: str):
        return ("VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_" 
                + self.pure_name().upper()
                + '_' + struct + '_' 
                + self.vendor())

    # e.g. EXT in VK_EXT_robustness2
    def vendor(self):
        return self.name.split('_')[1]

# Type aliases
Layer = Extension

class ExtensionRegistryEntry:
    # type of extension - right now it's either "instance" or "device"
    ext_type          = ""
    # the version in which the extension is promoted to core VK
    promoted_in       = None
    # functions added by the extension are referred to as "commands" in the registry
    commands          = None
    constants         = None
    features_struct   = None
    properties_struct = None

class ExtensionRegistry:
    # key = extension name, value = registry entry
    registry = dict()

    def __init__(self, vkxml_path: str):
        vkxml = ElementTree.parse(vkxml_path)

        for ext in vkxml.findall("extensions/extension"):
            # Reserved extensions are marked with `supported="disabled"`
            if ext.get("supported") == "disabled":
                continue

            name = ext.attrib["name"]

            entry = ExtensionRegistryEntry()
            entry.ext_type = ext.attrib["type"]
            entry.promoted_in = self.parse_promotedto(ext.get("promotedto"))

            entry.commands = []
            for cmd in ext.findall("require/command"):
                cmd_name = cmd.get("name")
                if cmd_name:
                    entry.commands.append(cmd_name)

            entry.constants = []
            for enum in ext.findall("require/enum"):
                enum_name = enum.get("name")
                enum_extends = enum.get("extends")
                # we are only interested in VK_*_EXTENSION_NAME, which does not
                # have an "extends" attribute
                if not enum_extends:
                    entry.constants.append(enum_name)

            for ty in ext.findall("require/type"):
                ty_name = ty.get("name")
                if self.is_features_struct(ty_name):
                    entry.features_struct = ty_name
                elif self.is_properties_struct(ty_name):
                    entry.properties_struct = ty_name

            self.registry[name] = entry

    def in_registry(self, ext_name: str):
        return ext_name in self.registry

    def get_registry_entry(self, ext_name: str):
        if self.in_registry(ext_name):
            return self.registry[ext_name]

    # Parses e.g. "VK_VERSION_x_y" to integer tuple (x, y)
    # For any erroneous inputs, None is returned
    def parse_promotedto(self, promotedto: str):
        result = None

        if promotedto and promotedto.startswith("VK_VERSION_"):
            (major, minor) = promotedto.split('_')[-2:]
            result = (int(major), int(minor))

        return result

    def is_features_struct(self, struct: str):
        return re.match(r"VkPhysicalDevice.*Features.*", struct) is not None

    def is_properties_struct(self, struct: str):
        return re.match(r"VkPhysicalDevice.*Properties.*", struct) is not None

import os
import shutil
import nbt
import json
import itertools
import re
import concurrent.futures
import numpy as np
from PIL import Image
from enum import IntEnum
from zipfile import ZipFile
from pathlib import Path


LILY_PAD_IDENTIFICATION = [{"from": [0, 0.25, 0], "to":[16, 0.25, 16], "faces": {"down": {"uv": [16, 16, 0, 0], "texture": "#texture", "tintindex": 0}, "up": {"uv": [16, 0, 0, 16], "texture": "#texture", "tintindex": 0}}}]


listBiomes = {
    # name, temperature, rainfall, (water Red, water Green, water Bleu), (leave Red, Leave Green), (Leave Blue, Grass Red, Grass Green, Grass Blue)
    "ocean": (0.5, 0.5, (63, 118, 228), (255, 255, 255), (142, 185, 113)),
    "plains": (0.8, 0.4, (63, 118, 228), (119, 171, 47), (145, 189, 89)),
    "desert": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "mountains": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "forest": (0.7, 0.8, (63, 118, 228), (89, 174, 48), (121, 192, 90)),
    "taiga": (0.25, 0.8, (63, 118, 228), (104, 164, 100), (134, 183, 131)),
    "swamp": (0.8, 0.9, (97, 123, 100), (106, 112, 57), (106, 112, 57)),
    "river": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "nether_wastes": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "the_end": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "frozen_ocean": (0.0, 0.5, (57, 56, 201), (96, 161, 123), (128, 180, 151)),
    "frozen_river": (0.0, 0.5, (57, 56, 201), (96, 161, 123), (128, 180, 151)),
    "snowy_tundra": (0.0, 0.5, (63, 118, 228), (96, 161, 123), (128, 180, 151)),
    "snowy_mountains": (0.0, 0.5, (63, 118, 228), (96, 161, 123), (128, 180, 151)),
    "mushroom_fields": (0.9, 1.0, (63, 118, 228), (43, 187, 15), (85, 201, 63)),
    "mushroom_field_shore": (0.9, 1.1, (63, 118, 228), (43, 187, 15), (85, 201, 63)),
    "beach": (0.8, 0.4, (63, 118, 228), (119, 171, 47), (145, 189, 89)),
    "desert_hills": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "wooded_hills": (0.7, 0.8, (63, 118, 228), (89, 174, 48), (121, 192, 90)),
    "taiga_hills": (0.25, 0.8, (63, 118, 228), (104, 164, 100), (134, 183, 131)),
    "mountain_edge": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "jungle": (0.95, 0.9, (63, 118, 228), (48, 187, 11), (89, 201, 60)),
    "jungle_hills": (0.95, 0.9, (63, 118, 228), (48, 187, 11), (89, 201, 60)),
    "jungle_edge": (0.95, 0.9, (63, 118, 228), (62, 184, 15), (100, 199, 63)),
    "deep_ocean": (0.5, 0.5, (67, 213, 238), (113, 167, 77), (142, 185, 113)),
    "stone_shore": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "snowy_beach": (0.05, 0.3, (63, 118, 228), (100, 162, 120), (131, 181, 147)),
    "birch_forest": (0.6, 0.6, (63, 118, 228), (107, 169, 65), (136, 187, 103)),
    "birch_forest_hills": (0.6, 0.6, (63, 118, 228), (107, 169, 65), (136, 187, 103)),
    "dark_forest": (0.7, 0.8, (63, 118, 228), (89, 174, 48), (80, 122, 50)),
    "snowy_taiga": (0.5, 0.4, (61, 87, 214), (96, 161, 123), (128, 180, 151)),
    "snowy_taiga_hills": (0.5, 0.4, (61, 87, 214), (96, 161, 123), (128, 180, 151)),
    "giant_tree_taiga": (0.3, 0.8, (63, 118, 228), (104, 165, 95), (134, 184, 127)),
    "giant_tree_taiga_hills": (0.3, 0.8, (63, 118, 104), (165, 95, 100), (134, 184, 127)),
    "wooded_mountains": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "savanna": (1.2, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "savanna_plateau": (1.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "badlands": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "wooded_badlands_plateau": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "badlands_plateau": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "small_end_islands": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "end_midlands": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "end_highlands": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "end_barrens": (0.5, 0.5, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "warm_ocean": (0.5, 0.5, (67, 213, 238), (113, 167, 77), (142, 185, 113)),
    "lukewarm_ocean": (0.5, 0.5, (69, 173, 242), (113, 167, 77), (142, 185, 113)),
    "cold_ocean": (0.5, 0.5, (61, 87, 214), (113, 167, 77), (142, 185, 113)),
    "deep_warm_ocean": (0.5, 0.5, (67, 213, 238), (113, 167, 77), (142, 185, 113)),
    "deep_lukewarm_ocean": (0.5, 0.5, (69, 173, 242), (113, 167, 77), (142, 185, 113)),
    "deep_cold_ocean": (0.5, 0.5, (61, 87, 214), (113, 167, 77), (142, 185, 113)),
    "deep_frozen_ocean": (0.5, 0.5, (57, 56, 201), (113, 167, 77), (142, 185, 113)),
    "the_void": (0.0, 0.0, (63, 118, 228), (113, 167, 77), (142, 185, 113)),
    "sunflower_plains": (0.8, 0.4, (63, 118, 228), (119, 171, 47), (145, 189, 89)),
    "desert_lakes": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "gravelly_mountains": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "flower_forest": (0.7, 0.8, (63, 118, 228), (89, 174, 48), (121, 192, 90)),
    "taiga_mountains": (0.25, 0.8, (63, 118, 228), (104, 164, 100), (134, 183, 131)),
    "swamp_hills": (0.8, 0.9, (97, 123, 100), (106, 112, 57), (106, 112, 57)),
    "ice_spikes": (0.0, 0.5, (63, 118, 228), (96, 161, 123), (128, 180, 151)),
    "modified_jungle": (0.95, 0.9, (63, 118, 228), (48, 187, 11), (89, 201, 60)),
    "modified_jungle_edge": (0.95, 0.8, (63, 118, 228), (62, 184, 15), (100, 199, 63)),
    "tall_birch_forest": (0.6, 0.6, (63, 118, 228), (107, 169, 65), (136, 187, 103)),
    "tall_birch_hills": (0.6, 0.6, (63, 118, 228), (107, 169, 65), (136, 187, 103)),
    "dark_forest_hills": (0.7, 0.8, (63, 118, 228), (89, 174, 48), (80, 122, 50)),
    "snowy_taiga_mountains": (0.5, 0.4, (61, 87, 214), (96, 161, 123), (128, 180, 151)),
    "giant_spruce_taiga": (0.25, 0.8, (63, 118, 228), (104, 164, 100), (134, 183, 131)),
    "giant_spruce_taiga_hills": (0.25, 0.8, (63, 118, 228), (104, 164, 100), (134, 183, 131)),
    "modified_gravelly_mountains": (0.2, 0.3, (63, 118, 228), (109, 163, 107), (138, 182, 137)),
    "shattered_savanna": (1.225, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "shattered_savanna_plateau": (1.2125001, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "eroded_badlands": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "modified_wooded_badlands_plateau": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "modified_badlands_plateau": (2.0, 0.0, (63, 118, 228), (158, 129, 77), (144, 129, 77)),
    "bamboo_jungle": (0.95, 0.9, (63, 118, 228), (48, 187, 11), (89, 201, 60)),
    "bamboo_jungle_hills": (0.95, 0.9, (63, 118, 48), (187, 11, 255), (89, 201, 60)),
    "soul_sand_valley": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "crimson_forest": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "warped_forest": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
    "basalt_deltas": (2.0, 0.0, (63, 118, 228), (174, 164, 42), (191, 183, 85)),
}

# Convert 24-bit color value to RGB
def decToRGB(c):
    r = c >> 16
    c -= r * 65536
    g = c // 256
    b = c % 256

    return (r, g, b)


def hexToRGB(s):
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def isAlmostGrey(imgPath):
    img = Image.open(imgPath)
    imgArr = np.array(img.getdata())
    imgAvgColor = np.average(imgArr, axis=0)[:-1]
    return abs(np.min(imgAvgColor) - np.max(imgAvgColor)) < 10

def findInFolders(fileName, path):
    if fileName in [f.name.lower() for f in path.iterdir() if f.is_file()]:
        return path
    elif len([f for f in path.iterdir() if f.is_dir()]) > 0:
        r = False
        for fol in [f for f in path.iterdir() if f.is_dir()]:
            r = r or findInFolders(fileName=fileName, path=fol)
        return r
    else:
        return False


def zipdir(path, ziph, removeForArcname=""):
    # ziph is zipfile handle
    for root, dirs, files in os.walk(path):
        for file in files:
            if removeForArcname:
                ziph.write(os.path.join(root, file), os.path.join(root, file).replace(removeForArcname, ""))
            else:
                ziph.write(os.path.join(root, file))


def keyPermutation(key, json):
    for k in [','.join(a) for a in list(itertools.permutations(key.split(',')))]:
        if k in json.keys():
            return k


def extractorAssets(zipFile, extractPath):
    with ZipFile(zipFile, 'r') as zipObj:
        for fileName in [lf for lf in zipObj.namelist() if lf.startswith("assets/")]:
            zipObj.extract(fileName, extractPath)


def textureId2Path(textureId):
    if textureId:
        split = textureId.split(":")
        if len(split) == 2:
            mod = split[0]
            t = split[1].split("/")
        else:
            mod = "minecraft"
            t = split[0].split("/")
        if len(t) >= 2:
            folders = t[:-1]
            fileName = t[-1]
            return "assets/%s/textures/%s/%s.png" % (mod, "/".join(folders), fileName)
    return ""


def modelIdToPath(modelId):
    if modelId:
        split = modelId.split(":")
        if len(split) == 2:
            mod = split[0]
            t = split[1].split("/")
            if len(split) >= 2:
                folders = t[:-1]
                fileName = t[-1]
                return "assets/%s/models/%s/%s.json" % (mod, "/".join(folders), fileName)
    return ""


def modelIdtoOverviewId(modelId):
    if modelId:
        split = modelId.split(":")
        if len(split) == 2:
            mod = split[0]
            t = split[1].split("/")
            if len(split) >= 2:
                blockName = t[-1]
                return "block_%s__%s" % (mod, blockName)
    return ""


def jsonPath(json, path):
    key = path[0]
    if len(path) == 1:
        if isinstance(json, list):
            return False
        else:
            return key in json.keys() or any(i in [','.join(a) for a in list(itertools.permutations(key.split(",")))] for i in json.keys())

    if key in json.keys() or any(i in [','.join(a) for a in list(itertools.permutations(key.split(",")))] for i in json.keys()):
        return jsonPath(json[key], path[1:])
    else:
        return False


class Cache(object):
    """docstring for Block """

    def __init__(self):
        super(Cache, self).__init__()
        self.files = {}


class TypeBlock(IntEnum):
    IS_SPRITECROSS = 1
    IS_CUBECOLUMN = 2
    IS_LEAVE = 3
    IS_SLAB = 4
    IS_STAIR = 5
    IS_DOOR = 6
    IS_FENCE = 7
    IS_FENCEGATE = 8
    IS_WALL = 9
    IS_PRESSUREPLATE = 10
    IS_CUBEALL = 11
    IS_CUBEFULLFIX = 12
    IS_BUTTON = 13
    IS_TRAPDOOR = 14
    IS_TALLSPRITE = 15
    IS_CUBEFULL = 16
    IS_CUBEBOTTOMTOP = 17
    IS_AGECROSS = 18
    IS_CARPET = 19
    IS_MUSHROOM = 20
    IS_POTTED = 21  # Not RENDER
    IS_LILYPAD = 22


class InjectionCode(object):
    """docstring for Block """

    def __init__(self, flag, fileFullPath, tempPath, code):
        super(InjectionCode, self).__init__()
        self.startFlag = flag
        self.endFlag = flag.replace('"""', '""" END', 1).replace('/*', '/* END', 1).replace('#', '# END', 1)
        self.fileFullPath = Path(fileFullPath)
        self.tempPath = Path(tempPath)
        self.tempFile = self.tempPath / self.fileFullPath.name
        self.code = code
        self.__insertCode__()

    def __insertCode__(self):
        startDelete = None
        with open(self.tempFile, 'w+') as fileTemp:
            with open(self.fileFullPath, 'r') as fileSource:
                for lineSource in fileSource:
                    if self.endFlag in lineSource:
                        startDelete = False

                    if not startDelete:
                        fileTemp.write(lineSource)

                    if self.startFlag in lineSource:
                        startDelete = True
                        fileTemp.write(self.code + "\n")

        if startDelete is None:
            print("Error: file %s have not %s flag" % (fileSource, self.startFlag))
            return(False)
        elif startDelete:
            print("Error: file %s have not %s flag" % (fileSource, self.endFlag))
            return(False)
        else:
            shutil.move(self.tempFile, self.fileFullPath)


class Block(object):
    """docstring for Block """

    def __init__(self, block, extractPath):
        super(Block, self).__init__()
        self.extractPath = Path(extractPath)
        self.id = block["V"].value
        self.fullName = block["K"].valuestr()
        self.mod = self.fullName.split(":")[0]
        self.shortName = self.fullName.split(":")[1]
        self._cacheFiles_ = {}
        self._cacheIsType_ = {}
        self.__checkType__()

    def cacheFile(self, filePath):
        if filePath not in self._cacheFiles_.keys():
            self._cacheFiles_[filePath] = None
            if filePath.exists():
                with open(filePath) as json_file:
                    self._cacheFiles_[filePath] = json.load(json_file)

    def getBlockstate(self):
        blockstatesFilePath = self.extractPath / "assets" / self.mod / "blockstates" / (self.shortName + ".json")
        self.cacheFile(blockstatesFilePath)
        return self._cacheFiles_[blockstatesFilePath]

    def overviewBlockname(self):
        return "block_%s__%s" % (self.mod, self.shortName)

    def getCodeId(self, python=False):
        return "%s = %s%s" % (self.overviewBlockname(), self.id, '' if python else ',')

    def getFullnameToId(self, switchId=None):
        return "            '%s': (ids.%s, 0)," % (self.fullName, self.overviewBlockname() if switchId is None else switchId)

    def __str__(self):
        return "%s - %s" % (self.id, self.fullName)

    def __repr__(self):
        return "forgeExtractor.Block: %s - %s" % (self.id, self.fullName)

    def __checkType__(self):
        for typeBlock in TypeBlock:
            self._isType_(typeBlock=typeBlock, forse=True)

    def isKnown(self):
        for typeBlock in TypeBlock:
            if self._isType_(typeBlock=typeBlock):
                return True
        return False

    def _isType_(self, typeBlock, forse=False):
        if typeBlock not in self._cacheIsType_.keys() or forse:
            r = False
            if not any(self._cacheIsType_.values()):
                if typeBlock == TypeBlock.IS_SPRITECROSS:
                    if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                        if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/cross":
                            r = True

                elif typeBlock == TypeBlock.IS_CUBECOLUMN:
                    if jsonPath(self.getBlockstate(), ["variants", "axis=y", "model"]) and jsonPath(self.getBlockstate(), ["variants", "axis=z"]) and jsonPath(self.getBlockstate(), ["variants", "axis=x"]):
                        if self.getParent(self.getBlockstate()["variants"]["axis=y"]["model"]) == "block/cube_column":
                            r = True
                    # elif jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                    #     if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/cube_column":
                    #         r = True

                elif typeBlock == TypeBlock.IS_LEAVE:
                    if "leaves" in self.shortName:
                        r = True
                    else:
                        if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                            if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/leaves":
                                r = True

                elif typeBlock == TypeBlock.IS_SLAB:
                    if jsonPath(self.getBlockstate(), ["variants", "type=bottom", "model"]) and jsonPath(self.getBlockstate(), ["variants", "type=double", "model"]) and jsonPath(self.getBlockstate(), ["variants", "type=top", "model"]):
                        r = True

                elif typeBlock == TypeBlock.IS_STAIR:
                    if jsonPath(self.getBlockstate(), ["variants", "facing=east,half=bottom,shape=straight"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=bottom,shape=straight"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=bottom,shape=straight"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=bottom,shape=straight"]):
                        r = True

                elif typeBlock == TypeBlock.IS_DOOR:
                    if jsonPath(self.getBlockstate(), ["variants", "facing=east,half=lower,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=lower,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=lower,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=lower,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,half=lower,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=lower,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=lower,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=lower,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,half=upper,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=upper,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=upper,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=upper,hinge=left,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,half=upper,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=upper,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=upper,hinge=right,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=upper,hinge=right,open=false"]):
                        r = True

                elif typeBlock == TypeBlock.IS_FENCE:
                    if "multipart" in self.getBlockstate().keys():
                        if isinstance(self.getBlockstate()["multipart"], list):
                            for elem in self.getBlockstate()["multipart"]:
                                if isinstance(elem, dict) and "apply" in elem.keys() and len(elem.keys()) == 1:
                                    if isinstance(elem["apply"], dict) and "model" in elem["apply"].keys():
                                        if "block/fence_post" == self.getParent(elem["apply"]["model"]):
                                            r = True

                elif typeBlock == TypeBlock.IS_FENCEGATE:
                    if jsonPath(self.getBlockstate(), ["variants", "facing=south,in_wall=false,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,in_wall=false,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,in_wall=false,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,in_wall=false,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,in_wall=false,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,in_wall=false,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,in_wall=false,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,in_wall=false,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,in_wall=true,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,in_wall=true,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,in_wall=true,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,in_wall=true,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,in_wall=true,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,in_wall=true,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,in_wall=true,open=true"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,in_wall=true,open=true"]):
                        r = True

                elif typeBlock == TypeBlock.IS_WALL:
                    parents = ["block/template_wall_side", "block/template_wall_post"]
                    if "multipart" in self.getBlockstate().keys():
                        if isinstance(self.getBlockstate()["multipart"], list) and len(self.getBlockstate()["multipart"]) == 9:
                            if jsonPath(self.getBlockstate()["multipart"][0], ["apply", "model"]):
                                parent = self.getParent(self.getBlockstate()["multipart"][0]["apply"]["model"])
                                if parent in parents:
                                    r = True

                elif typeBlock == TypeBlock.IS_PRESSUREPLATE:
                    if jsonPath(self.getBlockstate(), ["variants", "powered=false", "model"]) and jsonPath(self.getBlockstate(), ["variants", "powered=true", "model"]):
                        r = True

                elif typeBlock == TypeBlock.IS_CUBEFULLFIX:
                    if jsonPath(self.getBlockstate(), ["variants"]):
                        key = None
                        if "" in self.getBlockstate()["variants"].keys():
                            key = ""
                        elif "axis=y" in self.getBlockstate()["variants"].keys():
                            key = "axis=y"
                        if key is not None:
                            if not isinstance(self.getBlockstate()["variants"][key], list):
                                if self.getParent(self.getBlockstate()["variants"][key]["model"]) in ["block/cube", "block/cube_column"]:
                                    jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                                    if jsonPath(jsonData, ["textures", "south"]) and jsonPath(jsonData, ["textures", "west"]):
                                        if jsonData["textures"]["south"] == jsonData["textures"]["west"]:
                                            r = True
                                    elif jsonPath(jsonData, ["textures", "end"]) and jsonPath(jsonData, ["textures", "side"]):
                                        r = True

                elif typeBlock == TypeBlock.IS_CUBEALL:
                    if jsonPath(self.getBlockstate(), ["variants", ""]):
                        if jsonPath(self.getBlockstate()["variants"][""], ["model"]):
                            if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/cube_all":
                                r = True
                        elif isinstance(self.getBlockstate()["variants"][""], list):
                            if self.getParent(self.getBlockstate()["variants"][""][0]["model"]) == "block/cube_all":
                                r = True

                    elif jsonPath(self.getBlockstate(), ["variants", "axis=y", "model"]) and jsonPath(self.getBlockstate(), ["variants", "axis=z", "model"]) and jsonPath(self.getBlockstate(), ["variants", "axis=x", "model"]):
                        if self.getParent(self.getBlockstate()["variants"]["axis=y"]["model"]) == "block/cube_all":
                            r = True

                elif typeBlock == TypeBlock.IS_BUTTON:
                    if jsonPath(self.getBlockstate(), ["variants", "face=wall,facing=east,powered=false"]) and jsonPath(self.getBlockstate(), ["variants", "face=wall,facing=west,powered=false"]) and jsonPath(self.getBlockstate(), ["variants", "face=wall,facing=south,powered=false"]) and jsonPath(self.getBlockstate(), ["variants", "face=wall,facing=north,powered=false"]):
                        r = True

                elif typeBlock == TypeBlock.IS_TRAPDOOR:
                    if jsonPath(self.getBlockstate(), ["variants", "facing=north,half=bottom,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=bottom,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,half=bottom,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=bottom,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=north,half=top,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=south,half=top,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=east,half=top,open=false"]) and jsonPath(self.getBlockstate(), ["variants", "facing=west,half=top,open=false"]):
                        r = True

                elif typeBlock == TypeBlock.IS_TALLSPRITE:
                    if jsonPath(self.getBlockstate(), ["variants", "half=lower"]) and jsonPath(self.getBlockstate(), ["variants", "half=upper"]):
                        if isinstance(self.getBlockstate()["variants"]["half=lower"], list):
                            jsonTemp = self.getBlockstate()["variants"]["half=lower"][0]
                        else:
                            jsonTemp = self.getBlockstate()["variants"]["half=lower"]
                        if self.getParent(jsonTemp["model"]) == "block/cross":
                            r = True

                elif typeBlock == typeBlock.IS_CUBEFULL:
                    if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                        if len(self.getBlockstate()["variants"].keys()) == 1:
                            jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                            if jsonPath(jsonData, ["textures", "up"]) and jsonPath(jsonData, ["textures", "north"]) and jsonPath(jsonData, ["textures", "east"]) and jsonPath(jsonData, ["textures", "south"]) and jsonPath(jsonData, ["textures", "west"]):
                                r = True

                elif typeBlock == typeBlock.IS_CUBEBOTTOMTOP:
                    if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                        if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/cube_bottom_top":
                            r = True

                elif typeBlock == typeBlock.IS_AGECROSS:
                    if jsonPath(self.getBlockstate(), ["variants", "age=0", "model"]):
                        if self.getParent(self.getBlockstate()["variants"]["age=0"]["model"]) == "block/cross":
                            r = True

                elif typeBlock == typeBlock.IS_CARPET:
                    if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                        if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/carpet":
                            r = True

                elif typeBlock == TypeBlock.IS_MUSHROOM:
                    if "multipart" in self.getBlockstate().keys():
                        if isinstance(self.getBlockstate()["multipart"], list):
                            if jsonPath(self.getBlockstate()["multipart"][0], ["apply", "model"]):
                                jsonModel = self.getModel(modelIdToPath(self.getBlockstate()["multipart"][0]["apply"]["model"]))
                                if jsonPath(jsonModel, ["textures", "texture"]):
                                    r = True

                elif typeBlock == TypeBlock.IS_POTTED:
                    if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
                        if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/flower_pot_cross":
                            r = True

                elif typeBlock == TypeBlock.IS_LILYPAD:
                    if jsonPath(self.getBlockstate(), ["variants", ""]):
                        if len(self.getBlockstate()["variants"][""]) == 4:
                            jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""][0]["model"]))
                            if "elements" in jsonData.keys():
                                if jsonData["elements"] == LILY_PAD_IDENTIFICATION:
                                    r = True

            self._cacheIsType_[typeBlock] = r
        return self._cacheIsType_[typeBlock]

    def getModel(self, modelFile):
        modelFilePath = self.extractPath / modelFile
        self.cacheFile(modelFilePath)
        return self._cacheFiles_[modelFilePath]

    def getParent(self, modelId):
        modelFile = modelIdToPath(modelId)
        jsonData = self.getModel(modelFile)
        if jsonData:
            if "parent" in jsonData.keys():
                return jsonData["parent"].split(":")[::-1][0]
        return None

    def getTextures(self):
        try:
            if self.isCubeColumn():
                if "" in self.getBlockstate()["variants"].keys():
                    key = ""
                elif "axis=y" in self.getBlockstate()["variants"].keys():
                    key = "axis=y"
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                if jsonData and jsonPath(jsonData, ["textures", "side"]) and jsonPath(jsonData, ["textures", "end"]):
                    return [textureId2Path(jsonData["textures"]["end"]), textureId2Path(jsonData["textures"]["side"])]

            elif self.isSpriteCross():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                if "textures" in jsonData.keys():
                    return textureId2Path(jsonData["textures"]["cross"])

            elif self.isLeave():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                if "textures" in jsonData.keys():
                    return textureId2Path(next(iter(jsonData["textures"].values())))

            elif self.isSlab():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"]["type=top"]["model"]))
                return [textureId2Path(jsonData["textures"]["top"]), textureId2Path(jsonData["textures"]["side"])]

            elif self.isStair():
                key = keyPermutation("facing=east,half=bottom,shape=straight", self.getBlockstate()["variants"])
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                return [textureId2Path(jsonData["textures"]["top"]), textureId2Path(jsonData["textures"]["side"])]

            elif self.isDoor():
                key = keyPermutation("facing=west,half=upper,hinge=left,open=false", self.getBlockstate()["variants"])
                jsonDataTop = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                key = keyPermutation("facing=west,half=lower,hinge=left,open=false", self.getBlockstate()["variants"])
                jsonDataBottom = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                return [textureId2Path(jsonDataTop["textures"]["top"]), textureId2Path(jsonDataBottom["textures"]["bottom"])]

            elif self.isFence():
                jsonData = self.getBlockstate()["multipart"]
                for elem in jsonData:
                    if "apply" in elem.keys():
                        jsonData = self.getModel(modelIdToPath(elem["apply"]["model"]))
                        return textureId2Path(jsonData["textures"]["texture"])

            elif self.isFenceGate():
                key = keyPermutation("facing=south,in_wall=false,open=false", self.getBlockstate()["variants"])
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

            elif self.isWall():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["multipart"][0]["apply"]["model"]))
                return textureId2Path(jsonData["textures"]["wall"])

            elif self.isPressurePlate():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"]["powered=false"]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

            elif self.isCubeAll():
                if "" in self.getBlockstate()["variants"].keys():
                    if isinstance(self.getBlockstate()["variants"][""], list):
                        jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""][0]["model"]))
                    else:
                        jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))

                elif "axis=y" in self.getBlockstate()["variants"].keys():
                    jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"]["axis=y"]["model"]))

                return textureId2Path(jsonData["textures"]["all"])

            elif self.isButton():
                key = keyPermutation("face=wall,facing=east,powered=false", self.getBlockstate()["variants"])
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

            elif self.isTrapDoor():
                key = keyPermutation("facing=north,half=top,open=false", self.getBlockstate()["variants"])
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

            elif self.isTallSprite():
                jsonDataBottom = self.getModel(modelIdToPath(self.getBlockstate()["variants"]["half=lower"]["model"]))
                jsonDataUpper = self.getModel(modelIdToPath(self.getBlockstate()["variants"]["half=upper"]["model"]))
                return [textureId2Path(jsonDataUpper["textures"]["cross"]), textureId2Path(jsonDataBottom["textures"]["cross"])]

            elif self.isCubeFullFix():
                key = None
                if "" in self.getBlockstate()["variants"].keys():
                    key = ""
                elif "axis=y" in self.getBlockstate()["variants"].keys():
                    key = "axis=y"

                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][key]["model"]))
                if "south" in jsonData["textures"].keys():
                    return [textureId2Path(jsonData["textures"]["up"]), textureId2Path(jsonData["textures"]["south"])]
                elif "end" in jsonData["textures"].keys():
                    return [textureId2Path(jsonData["textures"]["end"]), textureId2Path(jsonData["textures"]["side"])]

            elif self.isCubeFull():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                return [textureId2Path(jsonData["textures"]["up"]), textureId2Path(jsonData["textures"]["north"]), textureId2Path(jsonData["textures"]["east"]), textureId2Path(jsonData["textures"]["south"]), textureId2Path(jsonData["textures"]["west"])]

            elif self.isCubeBottomTop():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                return [textureId2Path(jsonData["textures"]["top"]), textureId2Path(jsonData["textures"]["side"])]

            elif self.isAgeCross():
                ages = list(self.getBlockstate()["variants"].keys())
                ages.sort()
                r = {}
                for age in ages:
                    jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][age]["model"]))
                    r[int(age.split("=")[1])] = textureId2Path(jsonData["textures"]["cross"])
                return r

            elif self.isCarpet():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""]["model"]))
                return textureId2Path(jsonData["textures"]["wool"])

            elif self.isMushroom():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["multipart"][0]["apply"]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

            elif self.isLilyPad():
                jsonData = self.getModel(modelIdToPath(self.getBlockstate()["variants"][""][0]["model"]))
                return textureId2Path(jsonData["textures"]["texture"])

        except Exception as e:
            print("Error getTextures: %s" % self.fullName)

        return None

    def isTallSprite(self):
        return self._isType_(TypeBlock.IS_TALLSPRITE)

    def isTrapDoor(self):
        return self._isType_(TypeBlock.IS_TRAPDOOR)

    def isCubeAll(self):
        return self._isType_(TypeBlock.IS_CUBEALL)

    def isCubeFullFix(self):
        return self._isType_(TypeBlock.IS_CUBEFULLFIX)

    def isSpriteCross(self):
        return self._isType_(TypeBlock.IS_SPRITECROSS)

    def isCubeColumn(self):
        return self._isType_(TypeBlock.IS_CUBECOLUMN)

    def getCubeColumnTex(self):
        tex = self.getTextures()
        return '    ids.%s: ("%s", "%s")' % (self.overviewBlockname(), tex[0], tex[1])

    def isButton(self):
        return self._isType_(TypeBlock.IS_BUTTON)

    def isLeave(self):
        return self._isType_(TypeBlock.IS_LEAVE)

    def isAlmostGrey(self):
        if self.isLeave():
            r = isAlmostGrey(str(self.extractPath / self.getTextures()))
            print("%s: %s" % (self.fullName, r))
            return r
        return None

    def getLeaveTex(self):
        return 'block(blockid=ids.%s, top_image="%s", transparent=True, solid=True)' % (self.overviewBlockname(), self.getTextures())

    def getGeneriqueTex(self):
        if self.isLeave():
            return 'block(blockid=ids.%s, top_image="%s", transparent=True, solid=True)' % (self.overviewBlockname(), self.getTextures())
        tex = self.getTextures()
        if self.isSpriteCross():
            return 'sprite(blockid=ids.%s, imagename="%s")' % (self.overviewBlockname(), tex)
        if self.isCubeFullFix() or self.isCubeBottomTop():
            if tex[0] and tex[1]:
                return 'block(blockid=ids.%s, top_image="%s", side_image="%s")' % (self.overviewBlockname(), tex[0], tex[1])
            else:
                return '# block(blockid=ids.%s, top_image="%s", side_image="%s")' % (self.overviewBlockname(), tex[0], tex[1])

        return 'block(blockid=ids.%s, top_image="%s")' % (self.overviewBlockname(), self.getTextures())

    def isSlab(self):
        return self._isType_(TypeBlock.IS_SLAB)

    def doubleSlabId(self):
        doubleSlabId = self.getBlockstate()["variants"]["type=double"]["model"]
        return "%s: %s" % (self.overviewBlockname(), modelIdtoOverviewId(doubleSlabId))

    def getSlabTex(self):
        tex = self.getTextures()
        return '    ids.%s: ["%s", "%s"]' % (self.overviewBlockname(), tex[0], tex[1])

    def isStair(self):
        return self._isType_(TypeBlock.IS_STAIR)

    def getStairTex(self):
        tex = self.getTextures()
        return '    ids.%s: ["%s", "%s"]' % (self.overviewBlockname(), tex[0], tex[1])

    def isDoor(self):
        return self._isType_(TypeBlock.IS_DOOR)

    def getDoorTex(self):
        tex = self.getTextures()
        return '    ids.%s: ["%s", "%s"]' % (self.overviewBlockname(), tex[0], tex[1])

    def isFence(self):
        return self._isType_(TypeBlock.IS_FENCE)

    def isFenceGate(self):
        return self._isType_(TypeBlock.IS_FENCEGATE)

    def getFenceGateTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def getFenceTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def isWall(self):
        return self._isType_(TypeBlock.IS_WALL)

    def getWallTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def getLilyPadTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def isPressurePlate(self):
        return self._isType_(TypeBlock.IS_PRESSUREPLATE)

    def getPressurePlateTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def getButtonTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def getTrapDoorTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)

    def getTallSpriteTex(self):
        tex = self.getTextures()
        return '    ids.%s: ["%s", "%s"]' % (self.overviewBlockname(), tex[0], tex[1])

    def isCubeFull(self):
        return self._isType_(TypeBlock.IS_CUBEFULL)

    def isCubeBottomTop(self):
        return self._isType_(TypeBlock.IS_CUBEBOTTOMTOP)

    def getCubeFullTex(self):
        tex = self.getTextures()
        return '    ids.%s: ["%s", "%s", "%s", "%s", "%s"]' % (self.overviewBlockname(), tex[0], tex[1], tex[2], tex[3], tex[4])

    def isAgeCross(self):
        return self._isType_(TypeBlock.IS_AGECROSS)

    def isCarpet(self):
        return self._isType_(TypeBlock.IS_CARPET)

    def isMushroom(self):
        return self._isType_(TypeBlock.IS_MUSHROOM)

    def isLilyPad(self):
        return self._isType_(TypeBlock.IS_LILYPAD)

    def getAgeCrossTex(self):
        tex = self.getTextures()
        r = '    ids.%s: {' % self.overviewBlockname()
        for t in tex:
            r = r + '%s: "%s", ' % (t, tex[t])
        r = r + '}'
        return r

    def getCarpetTex(self):
        tex = self.getTextures()
        return '    ids.%s: "%s"' % (self.overviewBlockname(), tex)


class Biome(object):
    """docstring for Biome"""

    def __init__(self, biome):
        super(Biome, self).__init__()
        self.id = biome["V"].value
        self.fullName = biome["K"].valuestr()
        self.mod = self.fullName.split(":")[0]
        self.shortName = self.fullName.split(":")[1]
        self.sourceFile = self.findSourceFile()
        if self.sourceFile:
            self.waterColor = self.getWaterColor()
            self.foliageColor = self.getFoliageColor()
            self.grassColor = self.getGrassColor()
            self.temperature = self.getTemperature()
            self.rainfall = self.getRainfall()

        elif self.shortName in listBiomes.keys():
            b = listBiomes[self.shortName]
            self.waterColor = b[2]
            self.foliageColor = b[3]
            self.grassColor = b[4]
            self.temperature = b[0]
            self.rainfall = b[1]

    def getStructC(self):
        # , %s, %s, %s, %s, %s, %s, %s, %s, %s
        # , self.waterColor[0], self.waterColor[1], self.waterColor[2], self.foliageColor[0], self.foliageColor[1], self.foliageColor[2], self.grassColor[0], self.grassColor[1], self.grassColor[2]
        return '{"%s", %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s}' % (self.shortName, self.temperature, self.rainfall, self.waterColor[0], self.waterColor[1], self.waterColor[2], self.foliageColor[0], self.foliageColor[1], self.foliageColor[2], self.grassColor[0], self.grassColor[1], self.grassColor[2])

    def getTemperature(self):
        if self.sourceFile:
            m = re.search(r'\.temperature\((.+?)F\)', self.sourceFile)
            if m is not None:
                return float(m.group(1))
            else:
                return 0.7

    def getRainfall(self):
        if self.sourceFile:
            m = re.search(r'\.downfall\((.+?)F\)', self.sourceFile)
            if m is not None:
                return float(m.group(1))
            else:
                return 0.8

    def getWaterColor(self):
        if self.sourceFile:
            m = re.search(r'\.waterColor\((\d+)\)', self.sourceFile)
            if m is not None:
                return decToRGB(int(m.group(1)))
            else:
                m = re.search(r'\.waterColor\(0[xX]([0-9a-fA-F]+)\)', self.sourceFile)
                if m is not None:
                    return hexToRGB(m.group(1))
                else:
                    return (63, 118, 228)

    def getGrassColor(self):
        if self.sourceFile:
            m = re.search(r'(?si)grassColorOverride(.+?)0[xX]([0-9a-fA-F]+)', self.sourceFile)
            if m is not None:
                return hexToRGB(m.group(2))
            else:
                return (145, 189, 89)

    def getFoliageColor(self):
        if self.sourceFile:
            m = re.search(r'(?si)foliageColorOverride(.+?)0[xX]([0-9a-fA-F]+)', self.sourceFile)
            if m is not None:
                return hexToRGB(m.group(2))
            else:
                return (119, 171, 47)

    def findSourceFile(self):
        currentPath = Path(os.getcwd()) / "source"
        sourceFileName = "%sbiome.java" % self.shortName.replace("_", "")
        if Path(currentPath / self.mod / "biome").exists():
            p = findInFolders(sourceFileName, currentPath / self.mod / "biome")
            if p:
                file = open(p / sourceFileName, 'r')
                r = file.read()
                file.close()
                return r
            else:
                return p
        else:
            return False


class Mod(object):
    """docstring for Mod"""

    def __init__(self, modFile, extractPath):
        super(Mod, self).__init__()
        self.modFile = modFile
        self.extractPath = extractPath
        self.__extract__()

    def __extract__(self):
        extractorAssets(self.modFile, self.extractPath)


class ForgeExtrator(object):
    """docstring for ForgeExtractor"""

    def __init__(self, mapPath, modsPath, forgeFullPath, extractPath):
        super(ForgeExtrator, self).__init__()
        self.mapPath = mapPath
        self.modsPath = modsPath
        self.forgeFullPath = forgeFullPath
        self.extractPath = extractPath
        self.levelDat = nbt.nbt.NBTFile(self.mapPath / "level.dat", "rb")
        self.blocks = {}
        self.biomes = {}

    def getBiomes(self):
        return sorted(self.biomes, key=lambda k: self.biomes[k].id)

    def getBlocksIs(self, fnIs, removeMinecraft=True):
        return {k: v for k, v in self.blocks.items() if (v.mod != "minecraft" if removeMinecraft else True) and getattr(v, fnIs)()}

    def getBlocksIsTallSprite(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isTallSprite", removeMinecraft=removeMinecraft)

    def getBlocksIsCarpet(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isCarpet", removeMinecraft=removeMinecraft)

    def getBlocksIsTrapDoor(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isTrapDoor", removeMinecraft=removeMinecraft)

    def getBlocksIsCubeColumn(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isCubeColumn", removeMinecraft=removeMinecraft)

    def getBlocksWithoutData(self, removeMinecraft=True):
        return {k: v for k, v in self.blocks.items() if (v.mod != "minecraft" if removeMinecraft else True) and (v.isLeave() or v.isCubeAll() or v.isCubeFullFix() or v.isCubeBottomTop() or v.isMushroom() or v.isSpriteCross())}

    def getBlocksIsSlab(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isSlab", removeMinecraft=removeMinecraft)

    def getBlocksIsButton(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isButton", removeMinecraft=removeMinecraft)

    def getBlocksIsStair(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isStair", removeMinecraft=removeMinecraft)

    def getBlocksIsDoor(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isDoor", removeMinecraft=removeMinecraft)

    def getBlocksIsLilyPad(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isLilyPad", removeMinecraft=removeMinecraft)

    def getBlocksIsAncilC(self, removeMinecraft=True):
        return {k: v for k, v in self.blocks.items() if (v.mod != "minecraft" if removeMinecraft else True) and v.isLilyPad()}

    def getBlocksIsFenceGate(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isFenceGate", removeMinecraft=removeMinecraft)

    def getBlocksIsFence(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isFence", removeMinecraft=removeMinecraft)

    def getBlocksIsWall(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isWall", removeMinecraft=removeMinecraft)

    def getBlocksIsPressurePlate(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isPressurePlate", removeMinecraft=removeMinecraft)

    def getBlocksIsCubeFull(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isCubeFull", removeMinecraft=removeMinecraft)

    def getBlocksIsAgeCross(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isAgeCross", removeMinecraft=removeMinecraft)

    def getBlocksIsLeave(self, removeMinecraft=True):
        return self.getBlocksIs(fnIs="isLeave", removeMinecraft=removeMinecraft)

    def getBlocksIsLeaveC(self, removeMinecraft=True):
        return {k: v for k, v in self.blocks.items() if (v.mod != "minecraft" if removeMinecraft else True) and (v.isLeave() and v.isAlmostGrey())}

    def extractBlocksWithoutType(self):
        if len(self.blocks) == 0:
            self.__getModedBlocks__()

    def extractBlocks(self):
        self.__extractBlocks__()

    def extractBiomes(self):
        self.__extractBiomes__()

    def extractFiles(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(self.__extractForge__())
            executor.submit(self.__extractMods__())

    def __extractBlocks__(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for block in [block for block in self.levelDat['fml']['Registries']['minecraft:block']['ids']]:
                executor.submit(self.__addOneBlock__(block=block, extractPath=self.extractPath))

    def __addOneBlock__(self, block, extractPath):
        self.blocks[block["K"].value] = Block(block=block, extractPath=extractPath)

    def __extractBiomes__(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for biome in [biome for biome in self.levelDat['fml']['Registries']['minecraft:worldgen/biome']['ids']]:
                self.__addOneBiome__(biome=biome)

                # executor.submit(self.__addOneBiome__(block=block, extractPath=self.extractPath))

    def __addOneBiome__(self, biome):
        self.biomes[biome["K"].value] = Biome(biome=biome)

    def __extractForge__(self):
        extractorAssets(self.forgeFullPath, self.extractPath)

    def __extractMods__(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for modFile in [modFile for modFile in self.modsPath.iterdir() if modFile.is_file() and modFile.suffix == ".jar"]:
                executor.submit(extractorAssets(modFile, self.extractPath))

    def __createTexturesZip__(self):
        currentPath = os.getcwd()

        os.remove(currentPath + "/" + 'textures.zip')

        with ZipFile(currentPath + "/" + 'textures.zip', 'w') as myzip:
            zipdir(self.extractPath + "/assets/", myzip, self.extractPath)


class CodeGenerator(object):
    """docstring for ForgeExtractor"""
    IDS_SWITCH = {
        'minecraft:cave_air': 'minecraft:cave_air',
        'minecraft:void_air': 'minecraft:cave_air',
        'minecraft:brain_coral': 'minecraft:water',
        'minecraft:brain_coral_fan': 'minecraft:water',
        'minecraft:brain_coral_wall_fan': 'minecraft:water',
        'minecraft:bubble_column': 'minecraft:water',
        'minecraft:bubble_coral': 'minecraft:water',
        'minecraft:bubble_coral_fan': 'minecraft:water',
        'minecraft:bubble_coral_wall_fan': 'minecraft:water',
        'minecraft:fire_coral': 'minecraft:water',
        'minecraft:fire_coral_fan': 'minecraft:water',
        'minecraft:fire_coral_wall_fan': 'minecraft:water',
        'minecraft:horn_coral': 'minecraft:water',
        'minecraft:horn_coral_fan': 'minecraft:water',
        'minecraft:horn_coral_wall_fan': 'minecraft:water',
        'minecraft:kelp': 'minecraft:water',
        'minecraft:kelp_plant': 'minecraft:water',
        'minecraft:tube_coral': 'minecraft:water',
        'minecraft:tube_coral_fan': 'minecraft:water',
        'minecraft:tube_coral_wall_fan': 'minecraft:water',
    }

    def __init__(self, forgeExtrator):
        super(CodeGenerator, self).__init__()
        self.forgeExtrator = forgeExtrator

    def _getLines_(self, fnFilter, fnTex, removeMinecraft=True, joinString=',\n', preString=""):
        return preString + joinString.join([getattr(Block, fnTex)(block) for block in fnFilter(removeMinecraft).values()])

    def _generateIdsPy_(self):
        return('\n'.join([block.getCodeId(python=True) for block in self.forgeExtrator.blocks.values()]))

    def _generateIdsC_(self):
        return('\n'.join([block.getCodeId(python=False) for block in self.forgeExtrator.blocks.values()]))

    def _generateCubeColumnId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCubeColumn, fnTex="overviewBlockname")

    def _generateSlabsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsSlab, fnTex="overviewBlockname")

    def _generateButtonId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsButton, fnTex="overviewBlockname")

    def _generateTallSpriteId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsTallSprite, fnTex="overviewBlockname")

    def _generateStairsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsStair, fnTex="overviewBlockname")

    def _generateAgeCrossId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsAgeCross, fnTex="overviewBlockname")

    def _generateCubeFullId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCubeFull, fnTex="overviewBlockname")

    def _generateDoorsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsDoor, fnTex="overviewBlockname")

    def _generateTrapDoorsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsTrapDoor, fnTex="overviewBlockname")

    def _generateFenceId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsFence, fnTex="overviewBlockname")

    def _generateWallId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsWall, fnTex="overviewBlockname")

    def _generateFenceGateId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsFenceGate, fnTex="overviewBlockname")

    def _generateDoubleSlabsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsSlab, fnTex="doubleSlabId")

    def _generatePressurePlateId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsPressurePlate, fnTex="overviewBlockname")

    def _generateLilyPadsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsLilyPad, fnTex="overviewBlockname")

    def _generateCarpetsId_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCarpet, fnTex="overviewBlockname")

    def _generateSlabsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsSlab, fnTex="getSlabTex")

    def _generateCubeColumnTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCubeColumn, fnTex="getCubeColumnTex")

    def _generateDoorsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsDoor, fnTex="getDoorTex")

    def _generateButtonTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsButton, fnTex="getButtonTex")

    def _generateStairsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsStair, fnTex="getStairTex")

    def _generateFenceGateTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsFenceGate, fnTex="getFenceGateTex")

    def _generateFenceTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsFence, fnTex="getFenceTex")

    def _generateWallTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsWall, fnTex="getWallTex")

    def _generatePressurePlateTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsPressurePlate, fnTex="getPressurePlateTex")

    def _generateTrapDoorsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsTrapDoor, fnTex="getTrapDoorTex")

    def _generateCarpetsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCarpet, fnTex="getCarpetTex")

    def _generateCubeFullTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsCubeFull, fnTex="getCubeFullTex")

    def _generateTallSpriteTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsTallSprite, fnTex="getTallSpriteTex")

    def _generateLilyPadsTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsLilyPad, fnTex="getLilyPadTex")

    def _generateAgeCrossTex_(self):
        return self._getLines_(fnFilter=self.forgeExtrator.getBlocksIsAgeCross, fnTex="getAgeCrossTex")

    def _generateGeneriqueBlockTex_(self):
        return self._getLines_(joinString='\n', fnFilter=self.forgeExtrator.getBlocksWithoutData, fnTex="getGeneriqueTex")

    def _generateFullnameToId_(self):
        return('\n'.join([block.getFullnameToId(self.forgeExtrator.blocks[CodeGenerator.IDS_SWITCH[block.fullName]].overviewBlockname() if block.fullName in CodeGenerator.IDS_SWITCH.keys() else None) for block in self.forgeExtrator.blocks.values()]))

    def _generateAncilIdC_(self):
        return self._getLines_(preString=',', joinString=',\n', fnFilter=self.forgeExtrator.getBlocksIsAncilC, fnTex="overviewBlockname")

    def _generateGroupLilyPadsIdC_(self):
        return self._getLines_(preString=',', joinString=',\n', fnFilter=self.forgeExtrator.getBlocksIsLilyPad, fnTex="overviewBlockname")

    def _generateDoorsIdC_(self):
        return self._getLines_(preString=',', joinString=',\n', fnFilter=self.forgeExtrator.getBlocksIsDoor, fnTex="overviewBlockname")

    def _generateLeaveC_(self):
        return self._getLines_(preString=',', joinString=',\n', fnFilter=self.forgeExtrator.getBlocksIsLeaveC, fnTex="overviewBlockname")

    def _generateBiomesC_(self):
        r = []
        nextId = 0
        for biome in self.forgeExtrator.getBiomes():
            while self.forgeExtrator.biomes[biome].id != nextId:
                r.append('{"", 0.8, 0.4, 63, 118, 228, 119, 171, 47, 145, 189, 89}')
                nextId += 1
            if self.forgeExtrator.biomes[biome].id == nextId:
                r.append(self.forgeExtrator.biomes[biome].getStructC())
                nextId += 1

        return ',\n'.join(r)


    def execOperation(self, script):
        for operation in script:
            InjectionCode(flag=operation["flag"], fileFullPath=operation["fileFullPath"], tempPath=self.forgeExtrator.extractPath, code=operation["code"]())

    def generate(self):
        script = [
            # IDS #
            {"flag": '# FLAG IDS #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateIdsPy_},
            {"flag": '/* FLAG IDS */', "fileFullPath": "..\\overviewer_core\\src\\mc_id.h", "code": self._generateIdsC_},
            {"flag": '# FLAG FULLNAME IDS #', "fileFullPath": "..\\overviewer_core\\world.py", "code": self._generateFullnameToId_},
            {"flag": '/* FLAG GROUP ANCIL IDS */', "fileFullPath": "..\\overviewer_core\\src\\block_class.c", "code": self._generateAncilIdC_},
            {"flag": '/* FLAG BIOMES IDS */', "fileFullPath": "..\\overviewer_core\\src\\primitives/biomes.h", "code": self._generateBiomesC_},
            {"flag": '/* FLAG GROUP LEAVE IDS */', "fileFullPath": "..\\overviewer_core\\src\\block_class.c", "code": self._generateLeaveC_},
            # GENERIQUE BLOCK #
            {"flag": '# FLAG BLOCK #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateGeneriqueBlockTex_},
            # CUBE COLUMN #
            {"flag": '# FLAG CUBE_COLUMN ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateCubeColumnId_},
            {"flag": '# FLAG CUBE_COLUMN TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateCubeColumnTex_},
            # SLABS #
            {"flag": '# FLAG SLABS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateSlabsId_},
            {"flag": '# FLAG SLABS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateSlabsTex_},
            {"flag": '# FLAG DOUBLE SLABS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateDoubleSlabsId_},
            # STAIRS #
            {"flag": '# FLAG STAIRS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateStairsId_},
            {"flag": '# FLAG STAIRS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateStairsTex_},
            # DOORS #
            {"flag": '# FLAG DOORS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateDoorsId_},
            {"flag": '/* FLAG GROUP DOORS IDS */', "fileFullPath": "..\\overviewer_core\\src\\block_class.c", "code": self._generateDoorsIdC_},
            {"flag": '# FLAG DOORS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateDoorsTex_},
            # FENCE GATE #
            {"flag": '# FLAG FENCE_GATE ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateFenceGateId_},
            {"flag": '# FLAG FENCE_GATE TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateFenceGateTex_},
            # FENCE #
            {"flag": '# FLAG FENCE ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateFenceId_},
            {"flag": '# FLAG FENCE TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateFenceTex_},
            # WALLS #
            {"flag": '# FLAG WALLS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateWallId_},
            {"flag": '# FLAG WALLS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateWallTex_},
            # PRESSURE PLATE #
            {"flag": '# FLAG PRESSURE PLATE ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generatePressurePlateId_},
            {"flag": '# FLAG PRESSURE PLATE TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generatePressurePlateTex_},
            # BUTTON #
            {"flag": '# FLAG BUTTON ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateButtonId_},
            {"flag": '# FLAG BUTTON TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateButtonTex_},
            # TRAP DOOR #
            {"flag": '# FLAG TRAP DOORS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateTrapDoorsId_},
            {"flag": '# FLAG TRAP DOORS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateTrapDoorsTex_},
            # TALL SPRITE #
            {"flag": '# FLAG GROUP_TALL_SPRITE ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateTallSpriteId_},
            {"flag": '# FLAG TALL_SPRITE TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateTallSpriteTex_},
            # CUBE FULL #
            {"flag": '# FLAG CUBE_FULL ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateCubeFullId_},
            {"flag": '# FLAG CUBE_FULL TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateCubeFullTex_},
            # AGE CROSS #
            {"flag": '# FLAG AGE_CROSS ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateAgeCrossId_},
            {"flag": '# FLAG AGE_CROSS TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateAgeCrossTex_},
            # CARPET #
            {"flag": '# FLAG CARPET ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateCarpetsId_},
            {"flag": '# FLAG CARPET TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateCarpetsTex_},
            # LILY PAD #
            {"flag": '# FLAG LILY_PAD ID #', "fileFullPath": "..\\overviewer_core\\ids.py", "code": self._generateLilyPadsId_},
            {"flag": '# FLAG LILY_PAD TEX_DICT #', "fileFullPath": "..\\overviewer_core\\textures.py", "code": self._generateLilyPadsTex_},
            {"flag": '/* FLAG GROUP LILY_PAD IDS */', "fileFullPath": "..\\overviewer_core\\src\\block_class.c", "code": self._generateGroupLilyPadsIdC_},
        ]
        listFile = [e['fileFullPath'] for e in script]
        listScript = {}
        for file in listFile:
            listScript[file] = [operations for operations in script if operations["fileFullPath"] == file]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(list(set(listFile)))) as executor:
            for operations in listScript.values():
                executor.submit(self.execOperation(operations))

    # def generateTallSprite(self):
    #     return ',\n'.join([block for block in [block.overviewBlockname() for block in self.forgeExtrator.blocks.values() if block.mod != "minecraft"] if block != ""])

    # def generateTallSpriteTex(self):
    #     return '\n'.join([block for block in [block.getTallSpriteTex() for block in self.forgeExtrator.blocks.values() if block.mod != "minecraft"] if block != ""])


# def main():
extractPath = "c:\\f"
modsPath = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\mods")
mapPath = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\saves\\New World (1)")
forgeFullPath = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\versions\\1.16.4-forge-35.1.28\\1.16.4-forge-35.1.28.jar")

m = ForgeExtrator(mapPath=mapPath, modsPath=modsPath, forgeFullPath=forgeFullPath, extractPath=extractPath)
# m.extractFiles()
# m.__createTexturesZip__()
m.extractBiomes()

m.extractBlocks()

# for b in m.getBlocksIs(fnIs="isUnKnown"):
#     print("%s" % (m.getBlocksIs(fnIs="isUnKnown")[b].fullName))

c = CodeGenerator(forgeExtrator=m)
c.generate()




# InjectionCode(flag='### FLAG TALL_SPRITE TEX_DICT ###', fileFullPath="..\\overviewer_core\\textures.py", tempPath=extractPath, code=c.generateTallSpriteTex())

# InjectionCode(flag='### FLAG GROUP_TALL_SPRITE ###', fileFullPath="..\\overviewer_core\\ids.py", tempPath=extractPath, code=c.generateTallSprite())



    # print(c._generateIdsPy_())
    # print(c._generateIdsC_())

    # nbtfile = nbt.nbt.NBTFile(pathMinecraftSave / "level.dat", "rb")
    # for block in [block for block in nbtfile['fml']['Registries']['minecraft:block']['ids'] if not block["K"].value.startswith("minecraft:")]:
    #     print("%s - %s" % (block["K"].valuestr(), block["V"].valuestr()))


# main()
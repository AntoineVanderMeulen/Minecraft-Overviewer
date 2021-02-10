import os
import shutil
import nbt
import json
import numpy as np
from PIL import Image
import concurrent.futures
from enum import IntEnum
from zipfile import ZipFile
from pathlib import Path


def extractorAssets(zipFile, extractPath):
    with ZipFile(zipFile, 'r') as zipObj:
        for fileName in [lf for lf in zipObj.namelist() if lf.startswith("assets/")]:
            zipObj.extract(fileName, extractPath)


def formatTexturePath(textureId):
    if textureId:
        split = textureId.split(":")
        if len(split) == 2:
            mod = split[0]
            t = split[1].split("/")
            if len(split) >= 2:
                folders = t[:-1]
                fileName = t[-1]
                return "assets/%s/textures/%s/%s.png" % (mod, "/".join(folders), fileName)
    return ""


def formatModelPath(variant):
    if variant:
        split = variant.split(":")
        if len(split) == 2:
            mod = split[0]
            t = split[1].split("/")
            if len(split) >= 2:
                folders = t[:-1]
                fileName = t[-1]
                return "assets/%s/models/%s/%s.json" % (mod, "/".join(folders), fileName)
    return ""


def jsonPath(json, path):
    key = path[0]
    if len(path) == 1:
        return key in json.keys()

    if key in json.keys():
        return jsonPath(json[key], path[1:])
    else:
        return False


class Cache(object):
    """docstring for Block """

    def __init__(self):
        super(Cache, self).__init__()
        self.files = {}


class TypeBlock(IntEnum):
    SPRITECROSS = 1
    CUBECOLUMN = 2
    LEAVES = 3
    STAIRS = 4
    CROP = 5
    CUBEALL = 6
    BUTTON = 7
    CUBE = 8
    TALLSPRITES = 9


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
    # FACES = ["texture", "all", "cross", "end", "top", "up", "side", "south", "west", "north", "east", "bottom", "down"]

    def __init__(self, block, extractPath):
        super(Block, self).__init__()
        self.extractPath = Path(extractPath)
        self.id = block["V"].value
        self.fullName = block["K"].valuestr()
        self.mod = self.fullName.split(":")[0]
        self.shortName = self.fullName.split(":")[1]
        self.__cacheFiles = {}
        # self.__isKnown = None
        self.__getParent = None
        # self.__getTypeBlock = None

    def cacheFile(self, filePath):
        if filePath not in self.__cacheFiles.keys():
            self.__cacheFiles[filePath] = None
            if filePath.exists():
                with open(filePath) as json_file:
                    self.__cacheFiles[filePath] = json.load(json_file)

    def getBlockstate(self):
        blockstatesFilePath = self.extractPath / "assets" / self.mod / "blockstates" / (self.shortName + ".json")
        self.cacheFile(blockstatesFilePath)
        return self.__cacheFiles[blockstatesFilePath]

    # def getVariants(self):
    #     if len(self.variants) == 0:
    #         blockstatesFile = self.getBlockstate()
    #         for variant in blockstatesFile.get("variants", []):
    #             if type(blockstatesFile["variants"][variant]) == list:
    #                 v = blockstatesFile["variants"][variant][0]
    #             else:
    #                 v = blockstatesFile["variants"][variant]
    #             self.variants[variant] = Block.convertModelPath(v["model"])
    #     return self.variants

    # def getParent(self):
    #     if self.__getParent is None:
    #         modelFile = self.getModel()
    #         if modelFile:
    #             if "parent" in modelFile.keys():
    #                 self.__getParent = modelFile["parent"]
    #                 return self.__getParent
    #     return self.__getParent

    # def getTextureIds(self):
    #     if len(self.textureIds) == 0:
    #         modelFile = self.getModel()
    #         if modelFile:
    #             for face in Block.FACES:
    #                 tex = Block.convertTexturePath(modelFile["textures"].get(face, None))
    #                 if tex and tex not in self.textureIds.values():
    #                     self.textureIds[face] = tex
    #     # print("%s - %s" % (self.fullName, self.textureIds))
    #     return self.textureIds

    def overviewBlockname(self):
        return "block_%s__%s" % (self.mod, self.shortName)

    def getCodeId(self, python=False):
        return "%s = %s%s" % (self.overviewBlockname(), self.id, '' if python else ',')

    def getFullnameToId(self, switchId=None):
        return "            '%s': (ids.%s, 0)," % (self.fullName, self.overviewBlockname() if switchId is None else switchId)

    # def getTextureDefinition(self):
    #     if len(self.getTextureIds()) == 1:
    #         if next(iter(self.getTextureIds().keys())) == "cross":
    #             if not self.isTallSprite():
    #                 return 'sprite(blockid=ids.%s, imagename="%s")' % (self.overviewBlockname(), self.getTextureIds()["cross"])
    #         else:
    #             return 'block(blockid=ids.%s, top_image="%s"%s)' % (self.overviewBlockname(), next(iter(self.getTextureIds().values())), self.getMoreArgs())
    #     else:
    #         pass
    #     return ""

    # def getMoreArgs(self):
    #     args = ""
    #     if self.isLeave():
    #         args = ", transparent=True, solid=True"

    #     return args

    # def getTallSpriteTex(self):
    #     textureIds = {}
    #     for variant in TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES]:
    #         modelFile = self.getModel(self.getVariants().get(variant, None))
    #         print(modelFile)
    #         if modelFile:
    #             for face in Block.FACES:
    #                 tex = Block.convertTexturePath(modelFile["textures"].get(face, None))
    #                 if tex and tex not in self.textureIds.values():
    #                     textureIds[variant] = tex
    #                     break
    #     if TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES][0] in textureIds and TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES][1] in textureIds:
    #         return '        ids.%s: ["%s", "%s"],' % (self.overviewBlockname(), textureIds[TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES][0]], textureIds[TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES][1]])

    def __str__(self):
        return "%s - %s" % (self.id, self.fullName)

    def __repr__(self):
        return "forgeExtractor.Block: %s - %s" % (self.id, self.fullName)

    # def __parentIs__(self, types):
    #     return self.getParent() in types

    def isType(self, typeBlock):
        return self.getTypeBlock() == typeBlock

    # def isTransparent(self):
    #     self._isTransparent = False
    #     for texture in self.getTextureIds().values():
    #         try:
    #             if (np.asarray(Image.open(Path(self.extractPath) / texture)).T[3] < 255).any():
    #                 self._isTransparent = True
    #         except:
    #             pass
    #     return self._isTransparent

    def getModel(self, modelFile):
        modelFilePath = self.extractPath / modelFile
        self.cacheFile(modelFilePath)
        return self.__cacheFiles[modelFilePath]

    def getParent(self, modelId):
        modelFile = formatModelPath(modelId)
        jsonData = self.getModel(modelFile)
        if jsonData:
            return jsonData.get("parent", None).split(":")[::-1][0]
        return None

    def getTextures(self):
        if self.isCubeColumn():
            jsonData = self.getModel(formatModelPath(self.getBlockstate()["variants"]["axis=y"]["model"]))
            if jsonData and jsonPath(jsonData, ["textures", "side"]) and jsonPath(jsonData, ["textures", "end"]):
                return [formatTexturePath(jsonData["textures"]["end"]), formatTexturePath(jsonData["textures"]["side"])]
        return None

    # def getTypeBlock(self):
    #     if self.__getTypeBlock is None:
    #         pass

    #     return self.__getTypeBlock

    def isSpriteCross(self):
        if jsonPath(self.getBlockstate(), ["variants", "", "model"]):
            if self.getParent(self.getBlockstate()["variants"][""]["model"]) == "block/cross":
                return True
        return False

    def isCubeColumn(self):
        if jsonPath(self.getBlockstate(), ["variants", "axis=y", "model"]) and jsonPath(self.getBlockstate(), ["variants", "axis=z"]) and jsonPath(self.getBlockstate(), ["variants", "axis=x"]):
            if self.getParent(self.getBlockstate()["variants"]["axis=y"]["model"]) == "block/cube_column":
                return True
        return False

    def getCubeColumnTex(self):
        tex = self.getTextures()
        return '        ids.%s: ("%s", "%s")' % (self.overviewBlockname(), tex[0], tex[1])


    # def isLeave(self):
    #     return "leaves" in self.shortName or self.isType(TypeBlock.LEAVES)

    # def isStairs(self):
    #     return self.isType(TypeBlock.STAIRS)

    # def isCrop(self):
    #     return self.isType(TypeBlock.CROP)

    # def isCubeAll(self):
    #     return self.isType(TypeBlock.CUBEALL)

    # def isButton(self):
    #     return self.isType(TypeBlock.BUTTON)

    # def isTallSprite(self):
    #     print(all(elem in self.getVariants().keys() for elem in TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES]))
    #     return all(elem in self.getVariants().keys() for elem in TYPEBLOCKDETAIL[TypeBlockDetail.TALLSPRITES])

    # def isKnown(self):
    #     return self.getTypeBlock() is not None

    # def isUnknown(self):
    #     return(not(self.isKnown()))


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

    def getBlocksIsCubeColumn(self):
        return {k: v for k, v in self.blocks.items() if v.isCubeColumn()}

    def extractBlocksWithoutType(self):
        if len(self.blocks) == 0:
            self.__getModedBlocks__()
        # for block in [block for block in self.blocks.values() if block.isUnknown()]:
        #     print(block)

    # def getBlocksIs(self, TypeBlock):
    #     return {k: v for k, v in self.blocks.items() if v.isType(TypeBlock)}

    def extractBlocks(self):
        self.__extractBlocks__()

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

    def __extractForge__(self):
        extractorAssets(self.forgeFullPath, self.extractPath)

    def __extractMods__(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for modFile in [modFile for modFile in self.modsPath.iterdir() if modFile.is_file() and modFile.suffix == ".jar"]:
                executor.submit(extractorAssets(modFile, self.extractPath))


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

    def generateIdsPy(self):
        return('\n'.join([block.getCodeId(python=True) for block in self.forgeExtrator.blocks.values()]))

    def generateIdsC(self):
        return('\n'.join([block.getCodeId(python=False) for block in self.forgeExtrator.blocks.values()]))

    def generateCubeColumnId(self):
        return ',\n'.join([block.overviewBlockname() for block in self.forgeExtrator.getBlocksIsCubeColumn().values() if block.mod != "minecraft"])

    def generateCubeColumnTex(self):
        return ',\n'.join([block.getCubeColumnTex() for block in self.forgeExtrator.getBlocksIsCubeColumn().values() if block.mod != "minecraft"])

    def generateFullnameToId(self):
        return('\n'.join([block.getFullnameToId(self.forgeExtrator.blocks[CodeGenerator.IDS_SWITCH[block.fullName]].overviewBlockname() if block.fullName in CodeGenerator.IDS_SWITCH.keys() else None) for block in self.forgeExtrator.blocks.values()]))

    def generateBlock(self):
        return '\n'.join([block for block in [block.getTextureDefinition() for block in self.forgeExtrator.blocks.values() if block.mod != "minecraft"] if block != ""])

    def generateTallSprite(self):
        return ',\n'.join([block for block in [block.overviewBlockname() for block in self.forgeExtrator.blocks.values() if block.mod != "minecraft"] if block != ""])

    def generateTallSpriteTex(self):
        return '\n'.join([block for block in [block.getTallSpriteTex() for block in self.forgeExtrator.blocks.values() if block.mod != "minecraft"] if block != ""])

# def main():
extractPath = "c:\\f"
modsPath = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\mods")
mapPath = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\saves\\New World")
forgeFullPath  = Path("C:\\Users\\antoi\\AppData\\Roaming\\.minecraft\\versions\\1.16.4-forge-35.1.28\\1.16.4-forge-35.1.28.jar")

m = ForgeExtrator(mapPath=mapPath, modsPath=modsPath, forgeFullPath=forgeFullPath, extractPath=extractPath)
m.extractBlocks()


c = CodeGenerator(forgeExtrator=m)


InjectionCode(flag='# FLAG IDS #', fileFullPath="..\\overviewer_core\\ids.py", tempPath=extractPath, code=c.generateIdsPy())

InjectionCode(flag='/* FLAG IDS */', fileFullPath="..\\overviewer_core\\src\\mc_id.h", tempPath=extractPath, code=c.generateIdsC())

InjectionCode(flag='# FLAG FULLNAME IDS #', fileFullPath="..\\overviewer_core\\world.py", tempPath=extractPath, code=c.generateFullnameToId())

InjectionCode(flag='# FLAG CUBE_COLUMN ID #', fileFullPath="..\\overviewer_core\\ids.py", tempPath=extractPath, code=c.generateCubeColumnId())

InjectionCode(flag='# FLAG CUBE_COLUMN TEX_DICT #', fileFullPath="..\\overviewer_core\\textures.py", tempPath=extractPath, code=c.generateCubeColumnTex())

# InjectionCode(flag='""" FLAG BLOCK """', fileFullPath="..\\overviewer_core\\textures.py", tempPath=extractPath, code=c.generateBlock())

# InjectionCode(flag='### FLAG TALL_SPRITE TEX_DICT ###', fileFullPath="..\\overviewer_core\\textures.py", tempPath=extractPath, code=c.generateTallSpriteTex())

# InjectionCode(flag='### FLAG GROUP_TALL_SPRITE ###', fileFullPath="..\\overviewer_core\\ids.py", tempPath=extractPath, code=c.generateTallSprite())



    # print(c.generateIdsPy())
    # print(c.generateIdsC())

    # nbtfile = nbt.nbt.NBTFile(pathMinecraftSave / "level.dat", "rb")
    # for block in [block for block in nbtfile['fml']['Registries']['minecraft:block']['ids'] if not block["K"].value.startswith("minecraft:")]:
    #     print("%s - %s" % (block["K"].valuestr(), block["V"].valuestr()))


# main()
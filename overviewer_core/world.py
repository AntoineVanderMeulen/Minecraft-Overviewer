#    This file is part of the Minecraft Overviewer.
#
#    Minecraft Overviewer is free software: you can redistribute it and/or
#    modify it under the terms of the GNU General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or (at
#    your option) any later version.
#
#    Minecraft Overviewer is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
#    Public License for more details.
#
#    You should have received a copy of the GNU General Public License along
#    with the Overviewer.  If not, see <http://www.gnu.org/licenses/>.

import functools
import os
import os.path
import logging
import time
import random
import re
import locale

import numpy
import math

from . import nbt
from . import cache
from .biome import reshape_biome_data

from . import ids

"""
This module has routines for extracting information about available worlds

"""

class ChunkDoesntExist(Exception):
    pass


class UnsupportedVersion(Exception):
    pass


def log_other_exceptions(func):
    """A decorator that prints out any errors that are not
    ChunkDoesntExist errors. This should decorate any functions or
    methods called by the C code, such as get_chunk(), because the C
    code is likely to swallow exceptions. This will at least make them
    visible.
    """
    functools.wraps(func)
    def newfunc(*args):
        try:
            return func(*args)
        except ChunkDoesntExist:
            raise
        except Exception as e:
            logging.exception("%s raised this exception", func.func_name)
            raise
    return newfunc


class World(object):
    """Encapsulates the concept of a Minecraft "world". A Minecraft world is a
    level.dat file, a players directory with info about each player, a data
    directory with info about that world's maps, and one or more "dimension"
    directories containing a set of region files with the actual world data.

    This class deals with reading all the metadata about the world.  Reading
    the actual world data for each dimension from the region files is handled
    by a RegionSet object.

    Note that vanilla Minecraft servers and single player games have a single
    world with multiple dimensions: one for the overworld, the nether, etc.

    On Bukkit enabled servers, to support "multiworld," the server creates
    multiple Worlds, each with a single dimension.

    In this file, the World objects act as an interface for RegionSet objects.
    The RegionSet objects are what's really important and are used for reading
    block data for rendering.  A RegionSet object will always correspond to a
    set of region files, or what is colloquially referred to as a "world," or
    more accurately as a dimension.

    The only thing this class actually stores is a list of RegionSet objects
    and the parsed level.dat data

    """

    def __init__(self, worlddir):
        self.worlddir = worlddir

        # This list, populated below, will hold RegionSet files that are in
        # this world
        self.regionsets = []

        # The level.dat file defines a minecraft world, so assert that this
        # object corresponds to a world on disk
        if not os.path.exists(os.path.join(self.worlddir, "level.dat")):
            raise ValueError("level.dat not found in %s" % self.worlddir)

        data = nbt.load(os.path.join(self.worlddir, "level.dat"))[1]['Data']
        # it seems that reading a level.dat file is unstable, particularly with respect
        # to the spawnX,Y,Z variables.  So we'll try a few times to get a good reading
        # empirically, it seems that 0,50,0 is a "bad" reading
        # update: 0,50,0 is the default spawn, and may be valid is some cases
        # more info is needed
        data = nbt.load(os.path.join(self.worlddir, "level.dat"))[1]['Data']


        # Hard-code this to only work with format version 19133, "Anvil"
        if not ('version' in data and data['version'] == 19133):
            if 'version' in data and data['version'] == 0:
                logging.debug("Note: Allowing a version of zero in level.dat!")
                ## XXX temporary fix for #1194
            else:
                raise UnsupportedVersion(
                    ("Sorry, This version of Minecraft-Overviewer only works "
                     "with the 'Anvil' chunk format\n"
                     "World at %s is not compatible with Overviewer")
                    % self.worlddir)

        # This isn't much data, around 15 keys and values for vanilla worlds.
        self.leveldat = data


        # Scan worlddir to try to identify all region sets. Since different
        # server mods like to arrange regions differently and there does not
        # seem to be any set standard on what dimensions are in each world,
        # just scan the directory heirarchy to find a directory with .mca
        # files.
        for root, dirs, files in os.walk(self.worlddir, followlinks=True):
            # any .mcr files in this directory?
            mcas = [x for x in files if x.endswith(".mca")]
            if mcas:
                # construct a regionset object for this
                rel = os.path.relpath(root, self.worlddir)
                if os.path.basename(rel) != "poi":
                    rset = RegionSet(root, rel)
                    if root == os.path.join(self.worlddir, "region"):
                        self.regionsets.insert(0, rset)
                    else:
                        self.regionsets.append(rset)

        # TODO move a lot of the following code into the RegionSet


        try:
            # level.dat should have the LevelName attribute so we'll use that
            self.name = data['LevelName']
        except KeyError:
            # but very old ones might not? so we'll just go with the world dir name if they don't
            self.name = os.path.basename(os.path.realpath(self.worlddir))

        try:
            # level.dat also has a RandomSeed attribute
            self.seed = data['RandomSeed']
        except KeyError:
            self.seed = 0 # oh well

        # TODO figure out where to handle regionlists

    def get_regionsets(self):
        return self.regionsets
    def get_regionset(self, index):
        if type(index) == int:
            return self.regionsets[index]
        else: # assume a get_type() value
            candids = [x for x in self.regionsets if x.get_type() == index]
            logging.debug("You asked for %r, and I found the following candids: %r", index, candids)
            if len(candids) > 0:
                return candids[0]
            else:
                return None


    def get_level_dat_data(self):
        # Return a copy
        return dict(self.data)

    def find_true_spawn(self):
        """Returns the spawn point for this world. Since there is one spawn
        point for a world across all dimensions (RegionSets), this method makes
        sense as a member of the World class.

        Returns (x, y, z)

        """
        # The spawn Y coordinate is almost always the default of 64.  Find the
        # first air block above the stored spawn location for the true spawn
        # location

        ## read spawn info from level.dat
        data = self.leveldat
        disp_spawnX = spawnX = data['SpawnX']
        spawnY = data['SpawnY']
        disp_spawnZ = spawnZ = data['SpawnZ']

        ## clamp spawnY to a sane value, in-chunk value
        if spawnY < 0:
            spawnY = 0
        if spawnY > 255:
            spawnY = 255
            
        ## The chunk that holds the spawn location
        chunkX = spawnX//16
        chunkY = spawnY//16
        chunkZ = spawnZ//16
        
        ## The block for spawn *within* the chunk
        inChunkX = spawnX % 16
        inChunkZ = spawnZ % 16
        inChunkY = spawnY % 16

        ## Open up the chunk that the spawn is in
        regionset = self.get_regionset(None)
        if not regionset:
            return None
        try:
            chunk = regionset.get_chunk(chunkX, chunkZ)
        except ChunkDoesntExist:
            return (spawnX, spawnY, spawnZ)
        
        ## Check for first air block (0) above spawn
        
        # Get only the spawn section and the ones above, ordered from low to high
        spawnChunkSections = sorted(chunk['Sections'], key=lambda sec: sec['Y'])[chunkY:]
        for section in spawnChunkSections:
            # First section, start at registered local y
            for y in range(inChunkY, 16):
                # If air, return absolute coords
                if section['Blocks'][inChunkX, inChunkZ, y] == 0:
                    return spawnX, spawnY, spawnZ
                # Keep track of the absolute Y
                spawnY += 1
            # Next section, start at local 0
            inChunkY = 0
        return spawnX, 256, spawnZ

class RegionSet(object):
    """This object is the gateway to a particular Minecraft dimension within a
    world. It corresponds to a set of region files containing the actual
    world data. This object has methods for parsing and returning data from the
    chunks from its regions.

    See the docs for the World object for more information on the difference
    between Worlds and RegionSets.


    """

    def __init__(self, regiondir, rel):
        """Initialize a new RegionSet to access the region files in the given
        directory.

        regiondir is a path to a directory containing region files.

        rel is the relative path of this directory, with respect to the
        world directory.

        cachesize, if specified, is the number of chunks to keep parsed and
        in-memory.

        """
        self.regiondir = os.path.normpath(regiondir)
        self.rel = os.path.normpath(rel)
        logging.debug("regiondir is %r" % self.regiondir)
        logging.debug("rel is %r" % self.rel)

        # we want to get rid of /regions, if it exists
        if self.rel.endswith(os.path.normpath("/region")):
            self.type = self.rel[0:-len(os.path.normpath("/region"))]
        elif self.rel == "region":
            # this is the main world
            self.type = None
        else:
            logging.warning("Unknown region type in %r", regiondir)
            self.type = "__unknown"

        logging.debug("Scanning regions.  Type is %r" % self.type)

        # This is populated below. It is a mapping from (x,y) region coords to filename
        self.regionfiles = {}

        # This holds a cache of open regionfile objects
        self.regioncache = cache.LRUCache(size=16, destructor=lambda regionobj: regionobj.close())

        for x, y, regionfile in self._iterate_regionfiles():
            # regionfile is a pathname
            if os.path.getsize(regionfile) != 0:
                self.regionfiles[(x,y)] = (regionfile, os.path.getmtime(regionfile))
            else:
                logging.debug("Skipping zero-size region file {}".format(regionfile))

        self.empty_chunk = [None,None]
        logging.debug("Done scanning regions")

        self._blockmap = {
            'minecraft:air': (0, 0),
            'minecraft:cave_air': (0, 0),
            'minecraft:void_air': (0, 0),

            'minecraft:stone': (ids.block_stone, 0),
            'minecraft:granite': (ids.block_granite, 0),
            'minecraft:polished_granite': (ids.block_polished_granite, 0),
            'minecraft:diorite': (ids.block_diorite, 0),
            'minecraft:polished_diorite': (ids.block_polished_diorite, 0),
            'minecraft:andesite': (ids.block_andesite, 0),
            'minecraft:polished_andesite': (ids.block_polished_andesite, 0),
            'minecraft:grass_block': (ids.block_grass_block, 0),
            'minecraft:dirt': (ids.block_dirt, 0),
            'minecraft:coarse_dirt': (ids.block_coarse_dirt, 0),
            'minecraft:podzol': (ids.block_podzol, 0),
            'minecraft:cobblestone': (ids.block_cobblestone, 0),

            'minecraft:oak_planks': (ids.block_oak_planks, 0),
            'minecraft:spruce_planks': (ids.block_spruce_planks, 0),
            'minecraft:birch_planks': (ids.block_birch_planks, 0),
            'minecraft:jungle_planks': (ids.block_jungle_planks, 0),
            'minecraft:acacia_planks': (ids.block_acacia_planks, 0),
            'minecraft:dark_oak_planks': (ids.block_dark_oak_planks, 0),
            'minecraft:crimson_planks': (ids.block_crimson_planks, 0),
            'minecraft:warped_planks': (ids.block_warped_planks, 0),

            'minecraft:oak_sapling': (ids.block_oak_sapling, 0),
            'minecraft:spruce_sapling': (ids.block_spruce_sapling, 0),
            'minecraft:birch_sapling': (ids.block_birch_sapling, 0),
            'minecraft:jungle_sapling': (ids.block_jungle_sapling, 0),
            'minecraft:acacia_sapling': (ids.block_acacia_sapling, 0),
            'minecraft:dark_oak_sapling': (ids.block_dark_oak_sapling, 0),
            'minecraft:bedrock': (ids.block_bedrock, 0),
            'minecraft:water': (ids.block_water, 0),
            'minecraft:lava': (ids.block_lava, 0),

            # The following blocks are underwater and are not yet rendered.
            # To avoid spurious warnings, we'll treat them as water for now.
            'minecraft:brain_coral': (ids.block_water, 0),
            'minecraft:brain_coral_fan': (ids.block_water, 0),
            'minecraft:brain_coral_wall_fan': (ids.block_water, 0),
            'minecraft:bubble_column': (ids.block_water, 0),
            'minecraft:bubble_coral': (ids.block_water, 0),
            'minecraft:bubble_coral_fan': (ids.block_water, 0),
            'minecraft:bubble_coral_wall_fan': (ids.block_water, 0),
            'minecraft:fire_coral': (ids.block_water, 0),
            'minecraft:fire_coral_fan': (ids.block_water, 0),
            'minecraft:fire_coral_wall_fan': (ids.block_water, 0),
            'minecraft:horn_coral': (ids.block_water, 0),
            'minecraft:horn_coral_fan': (ids.block_water, 0),
            'minecraft:horn_coral_wall_fan': (ids.block_water, 0),
            'minecraft:kelp': (ids.block_water, 0),
            'minecraft:kelp_plant': (ids.block_water, 0),
            'minecraft:sea_pickle': (ids.block_sea_pickle, 0),
            'minecraft:seagrass': (ids.block_water, 0),
            'minecraft:tall_seagrass': (ids.block_water, 0),
            'minecraft:tube_coral': (ids.block_water, 0),
            'minecraft:tube_coral_fan': (ids.block_water, 0),
            'minecraft:tube_coral_wall_fan': (ids.block_water, 0),

            'minecraft:white_stained_glass': (ids.block_white_stained_glass, 0),
            'minecraft:orange_stained_glass': (ids.block_orange_stained_glass, 0),
            'minecraft:magenta_stained_glass': (ids.block_magenta_stained_glass, 0),
            'minecraft:light_blue_stained_glass': (ids.block_light_blue_stained_glass, 0),
            'minecraft:yellow_stained_glass': (ids.block_yellow_stained_glass, 0),
            'minecraft:lime_stained_glass': (ids.block_lime_stained_glass, 0),
            'minecraft:pink_stained_glass': (ids.block_pink_stained_glass, 0),
            'minecraft:gray_stained_glass': (ids.block_gray_stained_glass, 0),
            'minecraft:light_gray_stained_glass': (ids.block_light_gray_stained_glass, 0),
            'minecraft:cyan_stained_glass': (ids.block_cyan_stained_glass, 0),
            'minecraft:purple_stained_glass': (ids.block_purple_stained_glass, 0),
            'minecraft:blue_stained_glass': (ids.block_blue_stained_glass, 0),
            'minecraft:brown_stained_glass': (ids.block_brown_stained_glass, 0),
            'minecraft:green_stained_glass': (ids.block_green_stained_glass, 0),
            'minecraft:red_stained_glass': (ids.block_red_stained_glass, 0),
            'minecraft:black_stained_glass': (ids.block_black_stained_glass, 0),

            'minecraft:glass': (ids.block_glass, 0),
            'minecraft:ice': (ids.block_ice, 0),
            'minecraft:sand': (ids.block_sand, 0),
            'minecraft:red_sand': (ids.block_red_sand, 0),

            'minecraft:gravel': (ids.block_gravel, 0),
            'minecraft:gold_ore': (ids.block_gold_ore, 0),
            'minecraft:iron_ore': (ids.block_iron_ore, 0),
            'minecraft:coal_ore': (ids.block_coal_ore, 0),

            'minecraft:nether_gold_ore': (ids.block_nether_gold_ore, 0),

            'minecraft:oak_log': (ids.block_oak_log, 0),
            'minecraft:spruce_log': (ids.block_spruce_log, 0),
            'minecraft:birch_log': (ids.block_birch_log, 0),
            'minecraft:jungle_log': (ids.block_jungle_log, 0),
            'minecraft:acacia_log': (ids.block_acacia_log, 0),
            'minecraft:dark_oak_log': (ids.block_dark_oak_log, 0),

            'minecraft:stripped_oak_log': (ids.block_stripped_oak_log, 0),
            'minecraft:stripped_spruce_log': (ids.block_stripped_spruce_log, 0),
            'minecraft:stripped_birch_log': (ids.block_stripped_birch_log, 0),
            'minecraft:stripped_jungle_log': (ids.block_stripped_jungle_log, 0),
            'minecraft:stripped_acacia_log': (ids.block_stripped_acacia_log, 0),
            'minecraft:stripped_dark_oak_log': (ids.block_stripped_dark_oak_log, 0),

            'minecraft:oak_wood': (ids.block_oak_wood, 0),
            'minecraft:spruce_wood': (ids.block_spruce_wood, 0),
            'minecraft:birch_wood': (ids.block_birch_wood, 0),
            'minecraft:jungle_wood': (ids.block_jungle_wood, 0),
            'minecraft:acacia_wood': (ids.block_acacia_wood, 0),
            'minecraft:dark_oak_wood': (ids.block_dark_oak_wood, 0),
            'minecraft:stripped_oak_wood': (ids.block_stripped_oak_wood, 0),
            'minecraft:stripped_spruce_wood': (ids.block_stripped_spruce_wood, 0),
            'minecraft:stripped_birch_wood': (ids.block_stripped_birch_wood, 0),
            'minecraft:stripped_jungle_wood': (ids.block_stripped_jungle_wood, 0),
            'minecraft:stripped_acacia_wood': (ids.block_stripped_acacia_wood, 0),
            'minecraft:stripped_dark_oak_wood': (ids.block_stripped_dark_oak_wood, 0),

            'minecraft:oak_leaves': (ids.block_oak_leaves, 0),
            'minecraft:spruce_leaves': (ids.block_spruce_leaves, 0),
            'minecraft:birch_leaves': (ids.block_birch_leaves, 0),
            'minecraft:jungle_leaves': (ids.block_jungle_leaves, 0),
            'minecraft:acacia_leaves': (ids.block_acacia_leaves, 0),
            'minecraft:dark_oak_leaves': (ids.block_dark_oak_leaves, 0),

            'minecraft:sponge': (ids.block_sponge, 0),
            'minecraft:wet_sponge': (ids.block_wet_sponge, 0),
            'minecraft:lapis_ore': (ids.block_lapis_ore, 0),
            'minecraft:lapis_block': (ids.block_lapis_block, 0),

            'minecraft:sandstone': (ids.block_sandstone, 0),
            'minecraft:chiseled_sandstone': (ids.block_chiseled_sandstone, 0),
            'minecraft:cut_sandstone': (ids.block_cut_sandstone, 0),

            'minecraft:note_block': (ids.block_note_block, 0),

            'minecraft:white_bed': (ids.block_white_bed, 0),
            'minecraft:orange_bed': (ids.block_orange_bed, 0),
            'minecraft:magenta_bed': (ids.block_magenta_bed, 0),
            'minecraft:light_blue_bed': (ids.block_light_blue_bed, 0),
            'minecraft:yellow_bed': (ids.block_yellow_bed, 0),
            'minecraft:lime_bed': (ids.block_lime_bed, 0),
            'minecraft:pink_bed': (ids.block_pink_bed, 0),
            'minecraft:gray_bed': (ids.block_gray_bed, 0),
            'minecraft:light_gray_bed': (ids.block_light_gray_bed, 0),
            'minecraft:cyan_bed': (ids.block_cyan_bed, 0),
            'minecraft:purple_bed': (ids.block_purple_bed, 0),
            'minecraft:blue_bed': (ids.block_blue_bed, 0),
            'minecraft:brown_bed': (ids.block_brown_bed, 0),
            'minecraft:green_bed': (ids.block_green_bed, 0),
            'minecraft:red_bed': (ids.block_red_bed, 0),
            'minecraft:black_bed': (ids.block_black_bed, 0),

            'minecraft:powered_rail': (ids.block_powered_rail, 0),
            'minecraft:detector_rail': (ids.block_detector_rail, 0),

            'minecraft:sticky_piston': (ids.block_sticky_piston, 0),
            'minecraft:piston': (ids.block_piston, 0),
            'minecraft:piston_head': (ids.block_piston_head, 0),

            'minecraft:cobweb': (ids.block_cobweb, 0),
            'minecraft:dead_bush': (ids.block_dead_bush, 0),
            'minecraft:grass': (ids.block_grass, 0),
            'minecraft:fern': (ids.block_fern, 0),

            'minecraft:white_wool': (ids.block_white_wool, 0),
            'minecraft:orange_wool': (ids.block_orange_wool, 0),
            'minecraft:magenta_wool': (ids.block_magenta_wool, 0),
            'minecraft:light_blue_wool': (ids.block_light_blue_wool, 0),
            'minecraft:yellow_wool': (ids.block_yellow_wool, 0),
            'minecraft:lime_wool': (ids.block_lime_wool, 0),
            'minecraft:pink_wool': (ids.block_pink_wool, 0),
            'minecraft:gray_wool': (ids.block_gray_wool, 0),
            'minecraft:light_gray_wool': (ids.block_light_gray_wool, 0),
            'minecraft:cyan_wool': (ids.block_cyan_wool, 0),
            'minecraft:purple_wool': (ids.block_purple_wool, 0),
            'minecraft:blue_wool': (ids.block_blue_wool, 0),
            'minecraft:brown_wool': (ids.block_brown_wool, 0),
            'minecraft:green_wool': (ids.block_green_wool, 0),
            'minecraft:red_wool': (ids.block_red_wool, 0),
            'minecraft:black_wool': (ids.block_black_wool, 0),
            # Flowers
            'minecraft:poppy': (ids.block_poppy, 0),
            'minecraft:blue_orchid': (ids.block_blue_orchid, 0),
            'minecraft:allium': (ids.block_allium, 0),
            'minecraft:azure_bluet': (ids.block_azure_bluet, 0),
            'minecraft:red_tulip': (ids.block_red_tulip, 0),
            'minecraft:orange_tulip': (ids.block_orange_tulip, 0),
            'minecraft:white_tulip': (ids.block_white_tulip, 0),
            'minecraft:pink_tulip': (ids.block_pink_tulip, 0),
            'minecraft:oxeye_daisy': (ids.block_oxeye_daisy, 0),
            'minecraft:dandelion': (ids.block_dandelion, 0),
            "minecraft:wither_rose": (ids.block_wither_rose, 0),
            "minecraft:cornflower": (ids.block_cornflower, 0),
            "minecraft:lily_of_the_valley": (ids.block_lily_of_the_valley, 0),

            'minecraft:brown_mushroom': (ids.block_brown_mushroom, 0),
            'minecraft:red_mushroom': (ids.block_red_mushroom, 0),

            'minecraft:gold_block': (ids.block_gold_block, 0),
            'minecraft:iron_block': (ids.block_iron_block, 0),

            'minecraft:stone_slab': (ids.block_stone_slab, 0),  # 44-0
            'minecraft:sandstone_slab': (ids.block_sandstone_slab, 0),  # 44-1
            'minecraft:oak_slab': (ids.block_oak_slab, 0),  # 44-2
            'minecraft:cobblestone_slab': (ids.block_cobblestone_slab, 0),  # 44-3
            'minecraft:brick_slab': (ids.block_brick_slab, 0),  # 44-4
            'minecraft:stone_brick_slab': (ids.block_stone_brick_slab, 0),  # 44-5
            'minecraft:nether_brick_slab': (ids.block_nether_brick_slab, 0),  # 44-6
            'minecraft:quartz_slab': (ids.block_quartz_slab, 0),  # 44-7
            'minecraft:spruce_slab': (ids.block_spruce_slab, 0),  # 126-1
            'minecraft:birch_slab': (ids.block_birch_slab, 0),  # 126-2
            'minecraft:jungle_slab': (ids.block_jungle_slab, 0),  # 126-3
            'minecraft:acacia_slab': (ids.block_acacia_slab, 0),  # 126-4
            'minecraft:dark_oak_slab': (ids.block_dark_oak_slab, 0),  # 126-5
            'minecraft:red_sandstone_slab': (ids.block_red_sandstone_slab, 0),  # 182-0
            'minecraft:purpur_slab': (ids.block_purpur_slab, 0),  # 205-0
            'minecraft:petrified_oak_slab': (ids.block_petrified_oak_slab, 0),  # 126-0
            'minecraft:prismarine_slab': (ids.block_prismarine_slab, 0),  # 11340-0
            'minecraft:dark_prismarine_slab': (ids.block_dark_prismarine_slab, 0),  # 11341-0
            'minecraft:prismarine_brick_slab': (ids.block_prismarine_brick_slab, 0),  # 11342-0
            "minecraft:andesite_slab": (ids.block_andesite_slab, 0),  # 11343-0
            "minecraft:diorite_slab": (ids.block_diorite_slab, 0),  # 11344-0
            "minecraft:granite_slab": (ids.block_granite_slab, 0),  # 11345-0
            "minecraft:polished_andesite_slab": (ids.block_polished_andesite_slab, 0),  # 11346-0
            "minecraft:polished_diorite_slab": (ids.block_polished_diorite_slab, 0),  # 11347-0
            "minecraft:polished_granite_slab": (ids.block_polished_granite_slab, 0),  # 11348-0
            "minecraft:red_nether_brick_slab": (ids.block_red_nether_brick_slab, 0),  # 11349-0
            "minecraft:smooth_sandstone_slab": (ids.block_smooth_sandstone_slab, 0),  # 11350-0
            "minecraft:cut_sandstone_slab": (ids.block_cut_sandstone_slab, 0),  # 11351-0
            "minecraft:smooth_red_sandstone_slab": (ids.block_smooth_red_sandstone_slab, 0),  # 11352-0
            "minecraft:cut_red_sandstone_slab": (ids.block_cut_red_sandstone_slab, 0),  # 11353-0
            "minecraft:end_stone_brick_slab": (ids.block_end_stone_brick_slab, 0),  # 11354-0
            "minecraft:mossy_cobblestone_slab": (ids.block_mossy_cobblestone_slab, 0),  # 11355-0
            "minecraft:mossy_stone_brick_slab": (ids.block_mossy_stone_brick_slab, 0),  # 11356-0
            "minecraft:smooth_quartz_slab": (ids.block_smooth_quartz_slab, 0),  # 11357-0
            "minecraft:smooth_stone_slab": (ids.block_smooth_stone_slab, 0),  # 11358-0
            "minecraft:crimson_slab": (ids.block_crimson_slab, 0),
            "minecraft:warped_slab": (ids.block_warped_slab, 0),
            "minecraft:polished_blackstone_brick_slab": (ids.block_polished_blackstone_brick_slab, 0),
            "minecraft:blackstone_slab": (ids.block_blackstone_slab, 0),
            "minecraft:polished_blackstone_slab": (ids.block_polished_blackstone_slab, 0),


            'minecraft:bricks': (ids.block_bricks, 0),
            'minecraft:tnt': (ids.block_tnt, 0),
            'minecraft:bookshelf': (ids.block_bookshelf, 0),
            'minecraft:mossy_cobblestone': (ids.block_mossy_cobblestone, 0),
            'minecraft:obsidian': (ids.block_obsidian, 0),

            'minecraft:wall_torch': (ids.block_wall_torch, 0),
            'minecraft:torch': (ids.block_torch, 0),
            'minecraft:redstone_wall_torch': (ids.block_redstone_wall_torch, 0),
            'minecraft:redstone_torch': (ids.block_redstone_torch, 0),

            'minecraft:fire': (ids.block_fire, 0),

            'minecraft:spawner': (ids.block_spawner, 0),

            'minecraft:oak_stairs': (ids.block_oak_stairs, 0),  # 53
            'minecraft:cobblestone_stairs': (ids.block_cobblestone_stairs, 0),  # 67
            'minecraft:brick_stairs': (ids.block_brick_stairs, 0),  # 108
            'minecraft:stone_brick_stairs': (ids.block_stone_brick_stairs, 0),  # 109
            'minecraft:nether_brick_stairs': (ids.block_nether_brick_stairs, 0),  # 114
            'minecraft:sandstone_stairs': (ids.block_sandstone_stairs, 0),  # 128
            'minecraft:spruce_stairs': (ids.block_spruce_stairs, 0),  # 134
            'minecraft:birch_stairs': (ids.block_birch_stairs, 0),  # 135
            'minecraft:jungle_stairs': (ids.block_jungle_stairs, 0),  # 136
            'minecraft:quartz_stairs': (ids.block_quartz_stairs, 0),  # 156
            'minecraft:acacia_stairs': (ids.block_acacia_stairs, 0),  # 163
            'minecraft:dark_oak_stairs': (ids.block_dark_oak_stairs, 0),  # 164
            'minecraft:red_sandstone_stairs': (ids.block_red_sandstone_stairs, 0),  # 180
            'minecraft:purpur_stairs': (ids.block_purpur_stairs, 0),  # 203
            'minecraft:prismarine_stairs': (ids.block_prismarine_stairs, 0),  # 11337
            'minecraft:dark_prismarine_stairs': (ids.block_dark_prismarine_stairs, 0),  # 11338
            'minecraft:prismarine_brick_stairs': (ids.block_prismarine_brick_stairs, 0),  # 11339
            'minecraft:mossy_stone_brick_stairs': (ids.block_mossy_stone_brick_stairs, 0),  # 11370
            'minecraft:mossy_cobblestone_stairs': (ids.block_mossy_cobblestone_stairs, 0),  # 11371
            'minecraft:smooth_sandstone_stairs': (ids.block_smooth_sandstone_stairs, 0),  # 11374
            'minecraft:smooth_quartz_stairs': (ids.block_smooth_quartz_stairs, 0),  # 11375
            'minecraft:polished_granite_stairs': (ids.block_polished_granite_stairs, 0),  # 11376
            'minecraft:polished_diorite_stairs': (ids.block_polished_diorite_stairs, 0),  # 11377
            'minecraft:polished_andesite_stairs': (ids.block_polished_andesite_stairs, 0),  # 11378
            'minecraft:stone_stairs': (ids.block_stone_stairs, 0),  # 11379
            'minecraft:granite_stairs': (ids.block_granite_stairs, 0),  # 11380
            'minecraft:diorite_stairs': (ids.block_diorite_stairs, 0),  # 11381
            'minecraft:andesite_stairs': (ids.block_andesite_stairs, 0),  # 11382
            'minecraft:end_stone_brick_stairs': (ids.block_end_stone_brick_stairs, 0),  # 11383
            'minecraft:red_nether_brick_stairs': (ids.block_red_nether_brick_stairs, 0),  # 11384
            'minecraft:smooth_red_sandstone_stairs': (ids.block_smooth_red_sandstone_stairs, 0),  # 11415
            'minecraft:crimson_stairs': (ids.block_crimson_stairs, 0),
            'minecraft:warped_stairs': (ids.block_warped_stairs, 0),
            'minecraft:blackstone_stairs': (ids.block_blackstone_stairs, 0),
            'minecraft:polished_blackstone_brick_stairs': (ids.block_polished_blackstone_brick_stairs, 0),
            'minecraft:polished_blackstone_stairs': (ids.block_polished_blackstone_stairs, 0),

            'minecraft:chest': (ids.block_chest, 0),
            'minecraft:ender_chest': (ids.block_ender_chest, 0),
            'minecraft:trapped_chest': (ids.block_trapped_chest, 0),

            'minecraft:redstone_wire': (ids.block_redstone_wire, 0),

            'minecraft:diamond_ore': (ids.block_diamond_ore, 0),
            'minecraft:diamond_block': (ids.block_diamond_block, 0),

            'minecraft:crafting_table': (ids.block_crafting_table, 0),
            "minecraft:fletching_table": (ids.block_fletching_table, 0),
            "minecraft:cartography_table": (ids.block_cartography_table, 0),
            "minecraft:smithing_table": (ids.block_smithing_table, 0),

            'minecraft:wheat': (ids.block_wheat, 0),

            'minecraft:farmland': (ids.block_farmland, 0),
            'minecraft:grass_path': (ids.block_grass_path, 0),

            'minecraft:dispenser': (ids.block_dispenser, 0),
            'minecraft:furnace': (ids.block_furnace, 0),
            'minecraft:dropper': (ids.block_dropper, 0),
            "minecraft:blast_furnace": (ids.block_blast_furnace, 0),
            "minecraft:smoker": (ids.block_smoker, 0),

            # 'minecraft:sign': (63, 0),
            'minecraft:oak_sign': (ids.block_oak_sign, 0),
            'minecraft:spruce_sign': (ids.block_spruce_sign, 0),
            'minecraft:birch_sign': (ids.block_birch_sign, 0),
            'minecraft:jungle_sign': (ids.block_acacia_sign, 0),
            'minecraft:acacia_sign': (ids.block_jungle_sign, 0),
            'minecraft:dark_oak_sign': (ids.block_dark_oak_sign, 0),
            'minecraft:warped_sign': (ids.block_warped_sign, 0),
            'minecraft:crimson_sign': (ids.block_crimson_sign, 0),

            'minecraft:oak_door': (ids.block_oak_door, 0),
            'minecraft:iron_door': (ids.block_iron_door, 0),
            'minecraft:spruce_door': (ids.block_spruce_door, 0),
            'minecraft:birch_door': (ids.block_birch_door, 0),
            'minecraft:jungle_door': (ids.block_jungle_door, 0),
            'minecraft:acacia_door': (ids.block_acacia_door, 0),
            'minecraft:dark_oak_door': (ids.block_dark_oak_door, 0),
            'minecraft:crimson_door': (ids.block_crimson_door, 0),
            'minecraft:warped_door': (ids.block_warped_door, 0),

            'minecraft:ladder': (ids.block_ladder, 0),

            'minecraft:rail': (ids.block_rail, 0),
            'minecraft:activator_rail': (ids.block_activator_rail, 0),

            'minecraft:oak_wall_sign': (ids.block_oak_wall_sign, 0),
            'minecraft:spruce_wall_sign': (ids.block_spruce_wall_sign, 0),
            'minecraft:birch_wall_sign': (ids.block_birch_wall_sign, 0),
            'minecraft:jungle_wall_sign': (ids.block_acacia_wall_sign, 0),
            'minecraft:acacia_wall_sign': (ids.block_jungle_wall_sign, 0),
            'minecraft:dark_oak_wall_sign': (ids.block_dark_oak_wall_sign, 0),
            'minecraft:warped_wall_sign': (ids.block_warped_wall_sign, 0),
            'minecraft:crimson_wall_sign': (ids.block_crimson_wall_sign, 0),

            'minecraft:lever': (ids.block_lever, 0),

            'minecraft:stone_pressure_plate': (ids.block_stone_pressure_plate, 0),  # 70
            'minecraft:oak_pressure_plate': (ids.block_oak_pressure_plate, 0),  # 72
            'minecraft:spruce_pressure_plate': (ids.block_spruce_pressure_plate, 0),  # 11301
            'minecraft:birch_pressure_plate': (ids.block_birch_pressure_plate, 0),  # 11302
            'minecraft:jungle_pressure_plate': (ids.block_jungle_pressure_plate, 0),  # 11303
            'minecraft:acacia_pressure_plate': (ids.block_acacia_pressure_plate, 0),  # 11304
            'minecraft:dark_oak_pressure_plate': (ids.block_dark_oak_pressure_plate, 0),  # 11305
            'minecraft:light_weighted_pressure_plate': (ids.block_light_weighted_pressure_plate, 0),  # 147
            'minecraft:heavy_weighted_pressure_plate': (ids.block_heavy_weighted_pressure_plate, 0),  # 148
            'minecraft:crimson_pressure_plate': (ids.block_crimson_pressure_plate, 0),
            'minecraft:warped_pressure_plate': (ids.block_warped_pressure_plate, 0),
            'minecraft:polished_blackstone_pressure_plate': (ids.block_polished_blackstone_pressure_plate, 0),

            'minecraft:redstone_ore': (ids.block_redstone_ore, 0),

            'minecraft:stone_button': (ids.block_stone_button, 0),  # 77
            'minecraft:oak_button': (ids.block_oak_button, 0),  # 143
            'minecraft:spruce_button': (ids.block_spruce_button, 0),  # 11326
            'minecraft:birch_button': (ids.block_birch_button, 0),  # 11327
            'minecraft:jungle_button': (ids.block_jungle_button, 0),  # 11328
            'minecraft:acacia_button': (ids.block_acacia_button, 0),  # 11329
            'minecraft:dark_oak_button': (ids.block_dark_oak_button, 0),  # 11330
            'minecraft:crimson_button': (ids.block_crimson_button, 0),
            'minecraft:warped_button': (ids.block_warped_button, 0),
            'minecraft:polished_blackstone_button': (ids.block_polished_blackstone_button, 0),

            'minecraft:snow': (ids.block_snow, 0),
            'minecraft:snow_block': (ids.block_snow_block, 0),

            'minecraft:cactus': (ids.block_cactus, 0),
            'minecraft:clay': (ids.block_clay, 0),

            'minecraft:sugar_cane': (ids.block_sugar_cane, 0),
            'minecraft:jukebox': (ids.block_jukebox, 0),

            'minecraft:oak_fence': (ids.block_oak_fence, 0),  # 85
            'minecraft:nether_brick_fence': (ids.block_nether_brick_fence, 0),  # 113
            'minecraft:spruce_fence': (ids.block_spruce_fence, 0),  # 188
            'minecraft:birch_fence': (ids.block_birch_fence, 0),  # 189
            'minecraft:jungle_fence': (ids.block_jungle_fence, 0),  # 190
            'minecraft:dark_oak_fence': (ids.block_acacia_fence, 0),  # 191
            'minecraft:acacia_fence': (ids.block_dark_oak_fence, 0),  # 192
            'minecraft:crimson_fence': (ids.block_crimson_fence, 0),
            'minecraft:warped_fence': (ids.block_warped_fence, 0),

            'minecraft:oak_fence_gate': (ids.block_oak_fence_gate, 0),  # 107
            'minecraft:spruce_fence_gate': (ids.block_spruce_fence_gate, 0),  # 183
            'minecraft:birch_fence_gate': (ids.block_birch_fence_gate, 0),  # 184
            'minecraft:jungle_fence_gate': (ids.block_jungle_fence_gate, 0),  # 185
            'minecraft:dark_oak_fence_gate': (ids.block_acacia_fence_gate, 0),  # 186
            'minecraft:acacia_fence_gate': (ids.block_dark_oak_fence_gate, 0),  # 187
            'minecraft:crimson_fence_gate': (ids.block_crimson_fence_gate, 0),
            'minecraft:warped_fence_gate': (ids.block_warped_fence_gate, 0),

            'minecraft:pumpkin': (ids.block_pumpkin, 0),
            'minecraft:jack_o_lantern': (ids.block_jack_o_lantern, 0),
            'minecraft:carved_pumpkin': (ids.block_carved_pumpkin, 0),

            'minecraft:netherrack': (ids.block_netherrack, 0),  # 87
            'minecraft:soul_sand': (ids.block_soul_sand, 0),  # 88
            'minecraft:glowstone': (ids.block_glowstone, 0),  # 89

            'minecraft:nether_portal': (ids.block_nether_portal, 0),

            'minecraft:oak_trapdoor': (ids.block_oak_trapdoor, 0),  # 96
            'minecraft:iron_trapdoor': (ids.block_iron_trapdoor, 0),  # 167
            'minecraft:spruce_trapdoor': (ids.block_spruce_trapdoor, 0),  # 11332
            'minecraft:birch_trapdoor': (ids.block_birch_trapdoor, 0),  # 11333
            'minecraft:jungle_trapdoor': (ids.block_jungle_trapdoor, 0),  # 11334
            'minecraft:acacia_trapdoor': (ids.block_acacia_trapdoor, 0),  # 11335
            'minecraft:dark_oak_trapdoor': (ids.block_dark_oak_trapdoor, 0),  # 11336
            'minecraft:crimson_trapdoor': (ids.block_crimson_trapdoor, 0),
            'minecraft:warped_trapdoor': (ids.block_warped_trapdoor, 0),

            'minecraft:infested_cobblestone': (ids.block_infested_cobblestone, 0),
            'minecraft:infested_stone': (ids.block_infested_stone, 0),
            'minecraft:infested_stone_bricks': (ids.block_infested_stone_bricks, 0),
            'minecraft:infested_mossy_stone_bricks': (ids.block_infested_mossy_stone_bricks, 0),
            'minecraft:infested_cracked_stone_bricks': (ids.block_infested_cracked_stone_bricks, 0),
            'minecraft:infested_chiseled_stone_bricks': (ids.block_infested_chiseled_stone_bricks, 0),

            'minecraft:stone_bricks': (ids.block_stone_bricks, 0),
            'minecraft:mossy_stone_bricks': (ids.block_mossy_stone_bricks, 0),
            'minecraft:cracked_stone_bricks': (ids.block_cracked_stone_bricks, 0),
            'minecraft:chiseled_stone_bricks': (ids.block_chiseled_stone_bricks, 0),

            'minecraft:brown_mushroom_block': (ids.block_brown_mushroom_block, 0),
            'minecraft:red_mushroom_block': (ids.block_red_mushroom_block, 0),
            'minecraft:mushroom_stem': (ids.block_mushroom_stem, 0),

            'minecraft:prismarine': (ids.block_prismarine, 0),
            'minecraft:dark_prismarine': (ids.block_dark_prismarine, 0),
            'minecraft:prismarine_bricks': (ids.block_prismarine_bricks, 0),

            'minecraft:cake': (ids.block_cake, 0),

            'minecraft:repeater': (ids.block_repeater, 0),

            'minecraft:potted_oak_sapling': (ids.block_potted_oak_sapling, 0),  # Pots not rendering
            'minecraft:potted_spruce_sapling': (ids.block_potted_spruce_sapling, 0),  # Pots not rendering
            'minecraft:potted_birch_sapling': (ids.block_potted_birch_sapling, 0),  # Pots not rendering
            'minecraft:potted_jungle_sapling': (ids.block_potted_jungle_sapling, 0),  # Pots not rendering

            'minecraft:potted_acacia_sapling': (ids.block_potted_acacia_sapling, 0),  # Pots not rendering
            'minecraft:potted_dark_oak_sapling': (ids.block_potted_dark_oak_sapling, 0),  # Pots not rendering
            'minecraft:potted_dandelion': (ids.block_potted_dandelion, 0),  # Pots not rendering
            'minecraft:potted_fern': (ids.block_potted_fern, 0),  # Pots not rendering
            'minecraft:potted_poppy': (ids.block_potted_poppy, 0),  # Pots not rendering

            'minecraft:potted_blue_orchid': (ids.block_potted_blue_orchid, 0),  # Pots not rendering
            'minecraft:potted_allium': (ids.block_potted_allium, 0),  # Pots not rendering
            'minecraft:potted_azure_bluet': (ids.block_potted_azure_bluet, 0),  # Pots not rendering
            'minecraft:potted_red_tulip': (ids.block_potted_red_tulip, 0),  # Pots not rendering

            'minecraft:potted_orange_tulip': (ids.block_potted_orange_tulip, 0),  # Pots not rendering
            'minecraft:potted_white_tulip': (ids.block_potted_white_tulip, 0),  # Pots not rendering
            'minecraft:potted_pink_tulip': (ids.block_potted_pink_tulip, 0),  # Pots not rendering
            'minecraft:potted_oxeye_daisy': (ids.block_potted_oxeye_daisy, 0),  # Pots not rendering

            'minecraft:potted_cornflower': (ids.block_potted_cornflower, 0),  # Pots not rendering
            'minecraft:potted_lily_of_the_valley': (ids.block_potted_lily_of_the_valley, 0),  # Pots not rendering
            'minecraft:potted_wither_rose': (ids.block_potted_wither_rose, 0),  # Pots not rendering

            'minecraft:potted_red_mushroom': (ids.block_potted_red_mushroom, 0),  # Pots not rendering
            'minecraft:potted_brown_mushroom': (ids.block_potted_brown_mushroom, 0),  # Pots not rendering
            'minecraft:potted_dead_bush': (ids.block_potted_dead_bush, 0),  # Pots not rendering
            'minecraft:potted_cactus': (ids.block_potted_cactus, 0),  # Pots not rendering
            'minecraft:potted_bamboo': (ids.block_potted_bamboo, 0),  # Pots not rendering

            'minecraft:flower_pot': (ids.block_flower_pot, 0),  # Pots not rendering

            'minecraft:potted_crimson_fungus': (ids.block_potted_crimson_fungus, 0),  # Pots not rendering
            'minecraft:potted_warped_fungus': (ids.block_potted_warped_fungus, 0),  # Pots not rendering
            'minecraft:potted_crimson_roots': (ids.block_potted_crimson_roots, 0),  # Pots not rendering
            'minecraft:potted_warped_roots': (ids.block_potted_warped_roots, 0),  # Pots not rendering

            'minecraft:iron_bars': (ids.block_iron_bars, 0),
            'minecraft:glass_pane': (ids.block_glass_pane, 0),
            'minecraft:white_stained_glass_pane': (ids.block_white_stained_glass_pane, 0),
            'minecraft:orange_stained_glass_pane': (ids.block_orange_stained_glass_pane, 0),
            'minecraft:magenta_stained_glass_pane': (ids.block_magenta_stained_glass_pane, 0),
            'minecraft:light_blue_stained_glass_pane': (ids.block_light_blue_stained_glass_pane, 0),
            'minecraft:yellow_stained_glass_pane': (ids.block_yellow_stained_glass_pane, 0),
            'minecraft:lime_stained_glass_pane': (ids.block_lime_stained_glass_pane, 0),
            'minecraft:pink_stained_glass_pane': (ids.block_pink_stained_glass_pane, 0),
            'minecraft:gray_stained_glass_pane': (ids.block_gray_stained_glass_pane, 0),
            'minecraft:light_gray_stained_glass_pane': (ids.block_light_gray_stained_glass_pane, 0),
            'minecraft:cyan_stained_glass_pane': (ids.block_cyan_stained_glass_pane, 0),
            'minecraft:purple_stained_glass_pane': (ids.block_purple_stained_glass_pane, 0),
            'minecraft:blue_stained_glass_pane': (ids.block_blue_stained_glass_pane, 0),
            'minecraft:brown_stained_glass_pane': (ids.block_brown_stained_glass_pane, 0),
            'minecraft:green_stained_glass_pane': (ids.block_green_stained_glass_pane, 0),
            'minecraft:red_stained_glass_pane': (ids.block_red_stained_glass_pane, 0),
            'minecraft:black_stained_glass_pane': (ids.block_black_stained_glass_pane, 0),

            'minecraft:melon': (ids.block_melon, 0),
            'minecraft:attached_pumpkin_stem': (ids.block_attached_pumpkin_stem, 0),  # 104
            'minecraft:attached_melon_stem': (ids.block_attached_melon_stem, 0),
            'minecraft:pumpkin_stem': (ids.block_pumpkin_stem, 0),
            'minecraft:melon_stem': (ids.block_melon_stem, 0),

            'minecraft:terracotta': (ids.block_terracotta, 0),
            'minecraft:white_terracotta': (ids.block_white_terracotta, 0),
            'minecraft:orange_terracotta': (ids.block_orange_terracotta, 0),
            'minecraft:magenta_terracotta': (ids.block_magenta_terracotta, 0),
            'minecraft:light_blue_terracotta': (ids.block_light_blue_terracotta, 0),
            'minecraft:yellow_terracotta': (ids.block_yellow_terracotta, 0),
            'minecraft:lime_terracotta': (ids.block_lime_terracotta, 0),
            'minecraft:pink_terracotta': (ids.block_pink_terracotta, 0),
            'minecraft:gray_terracotta': (ids.block_gray_terracotta, 0),
            'minecraft:light_gray_terracotta': (ids.block_light_gray_terracotta, 0),
            'minecraft:cyan_terracotta': (ids.block_cyan_terracotta, 0),
            'minecraft:purple_terracotta': (ids.block_purple_terracotta, 0),
            'minecraft:blue_terracotta': (ids.block_blue_terracotta, 0),
            'minecraft:brown_terracotta': (ids.block_brown_terracotta, 0),
            'minecraft:green_terracotta': (ids.block_green_terracotta, 0),
            'minecraft:red_terracotta': (ids.block_red_terracotta, 0),
            'minecraft:black_terracotta': (ids.block_black_terracotta, 0),

            'minecraft:white_glazed_terracotta': (ids.block_white_glazed_terracotta, 0),
            'minecraft:orange_glazed_terracotta': (ids.block_orange_glazed_terracotta, 0),
            'minecraft:magenta_glazed_terracotta': (ids.block_magenta_glazed_terracotta, 0),
            'minecraft:light_blue_glazed_terracotta': (ids.block_light_blue_glazed_terracotta, 0),
            'minecraft:yellow_glazed_terracotta': (ids.block_yellow_glazed_terracotta, 0),
            'minecraft:lime_glazed_terracotta': (ids.block_lime_glazed_terracotta, 0),
            'minecraft:pink_glazed_terracotta': (ids.block_pink_glazed_terracotta, 0),
            'minecraft:gray_glazed_terracotta': (ids.block_gray_glazed_terracotta, 0),
            'minecraft:light_gray_glazed_terracotta': (ids.block_light_gray_glazed_terracotta, 0),
            'minecraft:cyan_glazed_terracotta': (ids.block_cyan_glazed_terracotta, 0),
            'minecraft:purple_glazed_terracotta': (ids.block_purple_glazed_terracotta, 0),
            'minecraft:blue_glazed_terracotta': (ids.block_blue_glazed_terracotta, 0),
            'minecraft:brown_glazed_terracotta': (ids.block_brown_glazed_terracotta, 0),
            'minecraft:green_glazed_terracotta': (ids.block_green_glazed_terracotta, 0),
            'minecraft:red_glazed_terracotta': (ids.block_red_glazed_terracotta, 0),
            'minecraft:black_glazed_terracotta': (ids.block_black_glazed_terracotta, 0),

            'minecraft:vine': (ids.block_vine, 0),

            'minecraft:mycelium': (ids.block_mycelium, 0),

            'minecraft:lily_pad': (ids.block_lily_pad, 0),

            'minecraft:nether_bricks': (ids.block_nether_bricks, 0),

            'minecraft:nether_wart': (ids.block_nether_wart, 0),

            'minecraft:enchanting_table': (ids.block_enchanting_table, 0),  # 116
            'minecraft:brewing_stand': (ids.block_brewing_stand, 0),  # 117
            'minecraft:cauldron': (ids.block_cauldron, 0),  # 118
            'minecraft:end_portal': (ids.block_end_portal, 0),  # 119
            'minecraft:end_portal_frame': (ids.block_end_portal_frame, 0),  # 120
            'minecraft:end_stone': (ids.block_end_stone, 0),  # 121
            'minecraft:dragon_egg': (ids.block_dragon_egg, 0),  # 122
            'minecraft:redstone_lamp': (ids.block_redstone_lamp, 0),  # 123


            'minecraft:cocoa': (ids.block_cocoa, 0),  # 127
            'minecraft:emerald_ore': (ids.block_emerald_ore, 0),  # 129
            'minecraft:tripwire': (ids.block_tripwire, 0),  # 131 not rendering
            'minecraft:tripwire_hook': (ids.block_tripwire_hook, 0),  # 132 not rendering
            'minecraft:emerald_block': (ids.block_emerald_block, 0),  # 133

            'minecraft:beacon': (ids.block_beacon, 0),  # 138
            'minecraft:carrots': (ids.block_carrots, 0),  # 141
            'minecraft:potatoes': (ids.block_potatoes, 0),  # 142

            'minecraft:redstone_block': (ids.block_redstone_block, 0),  # 152
            'minecraft:nether_quartz_ore': (ids.block_nether_quartz_ore, 0),  # 153
            'minecraft:quartz_block': (ids.block_quartz_block, 0),  # 155
            'minecraft:smooth_quartz': (ids.block_smooth_quartz, 0),    # Only bottom texture is different  # 155
            'minecraft:quartz_pillar': (ids.block_quartz_pillar, 0),  # 155
            'minecraft:chiseled_quartz_block': (ids.block_chiseled_quartz_block, 0),  # 155

            'minecraft:command_block': (ids.block_command_block, 0),  # 137
            'minecraft:repeating_command_block': (ids.block_repeating_command_block, 0),  # 210
            'minecraft:chain_command_block': (ids.block_chain_command_block, 0),  # 211
            'minecraft:slime_block': (ids.block_slime_block, 0),  # 165

            'minecraft:anvil': (ids.block_anvil, 0),  # 145
            'minecraft:chipped_anvil': (ids.block_chipped_anvil, 0),  # 145
            'minecraft:damaged_anvil': (ids.block_damaged_anvil, 0),  # 145

            'minecraft:chorus_plant': (ids.block_chorus_plant, 0),  # 199
            'minecraft:andesite_wall': (ids.block_andesite_wall, 0),  # 1792
            'minecraft:brick_wall': (ids.block_brick_wall, 0),  # 1793
            'minecraft:cobblestone_wall': (ids.block_cobblestone_wall, 0),  # 1794
            'minecraft:diorite_wall': (ids.block_diorite_wall, 0),  # 1795
            'minecraft:end_stone_brick_wall': (ids.block_end_stone_brick_wall, 0),  # 1796
            'minecraft:granite_wall': (ids.block_granite_wall, 0),  # 1797
            'minecraft:mossy_cobblestone_wall': (ids.block_mossy_cobblestone_wall, 0),  # 1798
            'minecraft:mossy_stone_brick_wall': (ids.block_mossy_stone_brick_wall, 0),  # 1799
            'minecraft:nether_brick_wall': (ids.block_nether_brick_wall, 0),  # 1800
            'minecraft:prismarine_wall': (ids.block_prismarine_wall, 0),  # 1801
            'minecraft:red_nether_brick_wall': (ids.block_red_nether_brick_wall, 0),  # 1802
            'minecraft:red_sandstone_wall': (ids.block_red_sandstone_wall, 0),  # 1803
            'minecraft:sandstone_wall': (ids.block_sandstone_wall, 0),  # 1804
            'minecraft:stone_brick_wall': (ids.block_stone_brick_wall, 0),  # 1805
            'minecraft:polished_blackstone_brick_wall': (ids.block_polished_blackstone_brick_wall, 0),
            'minecraft:polished_blackstone_wall': (ids.block_polished_blackstone_wall, 0),

            'minecraft:comparator': (ids.block_comparator, 0),  # 149
            'minecraft:daylight_detector': (ids.block_daylight_detector, 0),  # 151
            'minecraft:hopper': (ids.block_hopper, 0),  # 154

            'minecraft:white_carpet': (ids.block_white_carpet, 0),  # 171
            'minecraft:orange_carpet': (ids.block_orange_carpet, 0),  # 171
            'minecraft:magenta_carpet': (ids.block_magenta_carpet, 0),  # 171
            'minecraft:light_blue_carpet': (ids.block_light_blue_carpet, 0),  # 171
            'minecraft:yellow_carpet': (ids.block_yellow_carpet, 0),  # 171
            'minecraft:lime_carpet': (ids.block_lime_carpet, 0),  # 171
            'minecraft:pink_carpet': (ids.block_pink_carpet, 0),  # 171
            'minecraft:gray_carpet': (ids.block_gray_carpet, 0),  # 171
            'minecraft:light_gray_carpet': (ids.block_light_gray_carpet, 0),  # 171
            'minecraft:cyan_carpet': (ids.block_cyan_carpet, 0),  # 171
            'minecraft:purple_carpet': (ids.block_purple_carpet, 0),  # 171
            'minecraft:blue_carpet': (ids.block_blue_carpet, 0),  # 171
            'minecraft:brown_carpet': (ids.block_brown_carpet, 0),  # 171
            'minecraft:green_carpet': (ids.block_green_carpet, 0),  # 171
            'minecraft:red_carpet': (ids.block_red_carpet, 0),  # 171
            'minecraft:black_carpet': (ids.block_black_carpet, 0),  # 171

            'minecraft:sunflower': (ids.block_sunflower, 0),  # 175
            'minecraft:lilac': (ids.block_lilac, 0),  # 175
            'minecraft:tall_grass': (ids.block_tall_grass, 0),  # 175
            'minecraft:large_fern': (ids.block_large_fern, 0),  # 175
            'minecraft:rose_bush': (ids.block_rose_bush, 0),  # 175
            'minecraft:peony': (ids.block_peony, 0),  # 175

            'minecraft:chorus_flower': (ids.block_chorus_flower, 0),  # 200
            'minecraft:purpur_block': (ids.block_purpur_block, 0),  # 201
            'minecraft:purpur_pillar': (ids.block_purpur_pillar, 0),  # 202

            'minecraft:sea_lantern': (ids.block_sea_lantern, 0),  # 169
            'minecraft:hay_block': (ids.block_hay_block, 0),  # 170
            'minecraft:coal_block': (ids.block_coal_block, 0),  # 173
            'minecraft:packed_ice': (ids.block_packed_ice, 0),  # 174

            'minecraft:red_sandstone': (ids.block_red_sandstone, 0),  # 179 0
            'minecraft:chiseled_red_sandstone': (ids.block_chiseled_red_sandstone, 0),  # 179 1
            'minecraft:cut_red_sandstone': (ids.block_cut_red_sandstone, 0),  # 179 2

            'minecraft:end_stone_bricks': (ids.block_end_stone_bricks, 0),  # 206
            'minecraft:beetroots': (ids.block_beetroots, 0),  # 207
            'minecraft:sweet_berry_bush': (ids.block_sweet_berry_bush, 0),
            'minecraft:frosted_ice': (ids.block_frosted_ice, 0),  # 212
            'minecraft:magma_block': (ids.block_magma_block, 0),  # 213
            'minecraft:nether_wart_block': (ids.block_nether_wart_block, 0),  # 214
            'minecraft:red_nether_bricks': (ids.block_red_nether_bricks, 0),  # 215
            'minecraft:bone_block': (ids.block_bone_block, 0),  # 216

            'minecraft:shulker_box': (ids.block_shulker_box, 0),  # 257
            'minecraft:white_shulker_box': (ids.block_white_shulker_box, 0),
            'minecraft:orange_shulker_box': (ids.block_orange_shulker_box, 0),
            'minecraft:magenta_shulker_box': (ids.block_magenta_shulker_box, 0),
            'minecraft:light_blue_shulker_box': (ids.block_light_blue_shulker_box, 0),
            'minecraft:yellow_shulker_box': (ids.block_yellow_shulker_box, 0),
            'minecraft:lime_shulker_box': (ids.block_lime_shulker_box, 0),
            'minecraft:pink_shulker_box': (ids.block_pink_shulker_box, 0),
            'minecraft:gray_shulker_box': (ids.block_gray_shulker_box, 0),
            'minecraft:light_gray_shulker_box': (ids.block_light_gray_shulker_box, 0),
            'minecraft:cyan_shulker_box': (ids.block_cyan_shulker_box, 0),
            'minecraft:purple_shulker_box': (ids.block_purple_shulker_box, 0),
            'minecraft:blue_shulker_box': (ids.block_blue_shulker_box, 0),
            'minecraft:brown_shulker_box': (ids.block_brown_shulker_box, 0),
            'minecraft:green_shulker_box': (ids.block_green_shulker_box, 0),
            'minecraft:red_shulker_box': (ids.block_red_shulker_box, 0),
            'minecraft:black_shulker_box': (ids.block_black_shulker_box, 0),

            'minecraft:smooth_stone': (ids.block_smooth_stone, 0),  # 11313
            'minecraft:smooth_sandstone': (ids.block_smooth_sandstone, 0),  # 11314
            'minecraft:smooth_red_sandstone': (ids.block_smooth_red_sandstone, 0),  # 11315
            'minecraft:blue_ice': (ids.block_blue_ice, 0),

            'minecraft:brain_coral_block': (ids.block_brain_coral_block, 0),  # 11316
            'minecraft:bubble_coral_block': (ids.block_bubble_coral_block, 0),  # 11317
            'minecraft:fire_coral_block': (ids.block_fire_coral_block, 0),  # 11318
            'minecraft:horn_coral_block': (ids.block_horn_coral_block, 0),  # 11319
            'minecraft:tube_coral_block': (ids.block_tube_coral_block, 0),  # 11320
            'minecraft:dead_brain_coral_block': (ids.block_dead_brain_coral_block, 0),  # 11321
            'minecraft:dead_bubble_coral_block': (ids.block_dead_bubble_coral_block, 0),  # 11322
            'minecraft:dead_fire_coral_block': (ids.block_dead_fire_coral_block, 0),  # 11323
            'minecraft:dead_horn_coral_block': (ids.block_dead_horn_coral_block, 0),  # 11324
            'minecraft:dead_tube_coral_block': (ids.block_dead_tube_coral_block, 0),  # 11325

            'minecraft:observer': (ids.block_observer, 0),  # 218

            'minecraft:ancient_debris': (ids.block_ancient_debris, 0),  # 1000
            'minecraft:basalt': (ids.block_basalt, 0),  # 1001
            'minecraft:polished_basalt': (ids.block_polished_basalt, 0),  # 1002
            'minecraft:soul_campfire': (ids.block_soul_campfire, 0),  # 1003
            'minecraft:campfire': (ids.block_campfire, 0),
            'minecraft:blackstone': (ids.block_blackstone, 0),  # 1004
            'minecraft:netherite_block': (ids.block_netherite_block, 0),  # 1005

            'minecraft:warped_nylium': (ids.block_warped_nylium, 0),  # 1006
            'minecraft:crimson_nylium': (ids.block_crimson_nylium, 0),  # 1007
            'minecraft:soul_soil': (ids.block_soul_soil, 0),  # 1020

            'minecraft:bell': (ids.block_bell, 0),
            "minecraft:barrel": (ids.block_barrel, 0),

            'minecraft:beehive': (ids.block_beehive, 0),  # 11501
            'minecraft:bee_nest': (ids.block_bee_nest, 0),  # 11502
            'minecraft:honeycomb_block': (ids.block_honeycomb_block, 0),  # 11503
            'minecraft:honey_block': (ids.block_honey_block, 0),  # 11504

            'minecraft:dried_kelp_block': (ids.block_dried_kelp_block, 0),  # 11331
            'minecraft:scaffolding': (ids.block_scaffolding, 0),  # 11414

            'block_white_concrete': (ids.block_white_concrete, 0),
            'block_orange_concrete': (ids.block_orange_concrete, 0),
            'block_magenta_concrete': (ids.block_magenta_concrete, 0),
            'block_light_blue_concrete': (ids.block_light_blue_concrete, 0),
            'block_yellow_concrete': (ids.block_yellow_concrete, 0),
            'block_lime_concrete': (ids.block_lime_concrete, 0),
            'block_pink_concrete': (ids.block_pink_concrete, 0),
            'block_gray_concrete': (ids.block_gray_concrete, 0),
            'block_light_gray_concrete': (ids.block_light_gray_concrete, 0),
            'block_cyan_concrete': (ids.block_cyan_concrete, 0),
            'block_purple_concrete': (ids.block_purple_concrete, 0),
            'block_blue_concrete': (ids.block_blue_concrete, 0),
            'block_brown_concrete': (ids.block_brown_concrete, 0),
            'block_green_concrete': (ids.block_green_concrete, 0),
            'block_red_concrete': (ids.block_red_concrete, 0),
            'block_black_concrete': (ids.block_black_concrete, 0),
            'block_white_concrete_powder': (ids.block_white_concrete_powder, 0),
            'block_orange_concrete_powder': (ids.block_orange_concrete_powder, 0),
            'block_magenta_concrete_powder': (ids.block_magenta_concrete_powder, 0),
            'block_light_blue_concrete_powder': (ids.block_light_blue_concrete_powder, 0),
            'block_yellow_concrete_powder': (ids.block_yellow_concrete_powder, 0),
            'block_lime_concrete_powder': (ids.block_lime_concrete_powder, 0),
            'block_pink_concrete_powder': (ids.block_pink_concrete_powder, 0),
            'block_gray_concrete_powder': (ids.block_gray_concrete_powder, 0),
            'block_light_gray_concrete_powder': (ids.block_light_gray_concrete_powder, 0),
            'block_cyan_concrete_powder': (ids.block_cyan_concrete_powder, 0),
            'block_purple_concrete_powder': (ids.block_purple_concrete_powder, 0),
            'block_blue_concrete_powder': (ids.block_blue_concrete_powder, 0),
            'block_brown_concrete_powder': (ids.block_brown_concrete_powder, 0),
            'block_green_concrete_powder': (ids.block_green_concrete_powder, 0),
            'block_red_concrete_powder': (ids.block_red_concrete_powder, 0),
            'block_black_concrete_powder': (ids.block_black_concrete_powder, 0),

            'minecraft:jigsaw': (ids.block_jigsaw, 0),
            'minecraft:structure_block': (ids.block_structure_block, 0),
            'minecraft:warped_wart_block': (ids.block_warped_wart_block, 0),

            'minecraft:twisting_vines': (ids.block_weeping_vines, 0),  # 1012
            'minecraft:twisting_vines_plant': (ids.block_weeping_vines_plant, 0),  # 1013
            'minecraft:weeping_vines': (ids.block_twisting_vines, 0),  # 1014
            'minecraft:weeping_vines_plant': (ids.block_twisting_vines_plant, 0),  # 1015

            'minecraft:shroomlight': (ids.block_shroomlight, 0),

            "minecraft:grindstone": (ids.block_grindstone, 0),
            "minecraft:loom": (ids.block_loom, 0),  # 11367
            "minecraft:stonecutter": (ids.block_stonecutter, 0),  # 11368
            "minecraft:lectern": (ids.block_lectern, 0),

            "minecraft:composter": (ids.block_composter, 0),

            'minecraft:bamboo': (ids.block_bamboo, 0),
            'minecraft:warped_fungus': (ids.block_warped_fungus, 0),  # 1016
            'minecraft:crimson_fungus': (ids.block_crimson_fungus, 0),  # 1017
            'minecraft:warped_roots': (ids.block_warped_roots, 0),  # 1018
            'minecraft:crimson_roots': (ids.block_crimson_roots, 0),  # 1019

            'minecraft:bamboo_sapling': (ids.block_bamboo_sapling, 0),

            'minecraft:warped_stem': (ids.block_warped_stem, 0),  # 1008 0
            'minecraft:stripped_warped_stem': (ids.block_stripped_warped_stem, 0),  # 1008 1
            'minecraft:crimson_stem': (ids.block_crimson_stem, 0),  # 1008 2
            'minecraft:stripped_crimson_stem': (ids.block_stripped_crimson_stem, 0),  # 1008 3
            'minecraft:warped_hyphae': (ids.block_warped_hyphae, 0),  # 1009 0
            'minecraft:stripped_warped_hyphae': (ids.block_stripped_warped_hyphae, 0),  # 1009 1
            'minecraft:crimson_hyphae': (ids.block_crimson_hyphae, 0),  # 1009 2
            'minecraft:stripped_crimson_hyphae': (ids.block_stripped_crimson_hyphae, 0),  # 1009 3
            'minecraft:white_banner': (ids.block_white_banner, 0),  # not Rendering
            'minecraft:orange_banner': (ids.block_orange_banner, 0),  # not Rendering
            'minecraft:magenta_banner': (ids.block_magenta_banner, 0),  # not Rendering
            'minecraft:light_blue_banner': (ids.block_light_blue_banner, 0),  # not Rendering
            'minecraft:yellow_banner': (ids.block_yellow_banner, 0),  # not Rendering
            'minecraft:lime_banner': (ids.block_lime_banner, 0),  # not Rendering
            'minecraft:pink_banner': (ids.block_pink_banner, 0),  # not Rendering
            'minecraft:gray_banner': (ids.block_gray_banner, 0),  # not Rendering
            'minecraft:light_gray_banner': (ids.block_light_gray_banner, 0),  # not Rendering
            'minecraft:cyan_banner': (ids.block_cyan_banner, 0),  # not Rendering
            'minecraft:purple_banner': (ids.block_purple_banner, 0),  # not Rendering
            'minecraft:blue_banner': (ids.block_blue_banner, 0),  # not Rendering
            'minecraft:brown_banner': (ids.block_brown_banner, 0),  # not Rendering
            'minecraft:green_banner': (ids.block_green_banner, 0),  # not Rendering
            'minecraft:red_banner': (ids.block_red_banner, 0),  # not Rendering
            'minecraft:black_banner': (ids.block_black_banner, 0),  # not Rendering
            'minecraft:white_wall_banner': (ids.block_white_wall_banner, 0),  # not Rendering
            'minecraft:orange_wall_banner': (ids.block_orange_wall_banner, 0),  # not Rendering
            'minecraft:magenta_wall_banner': (ids.block_magenta_wall_banner, 0),  # not Rendering
            'minecraft:light_blue_wall_banner': (ids.block_light_blue_wall_banner, 0),  # not Rendering
            'minecraft:yellow_wall_banner': (ids.block_yellow_wall_banner, 0),  # not Rendering
            'minecraft:lime_wall_banner': (ids.block_lime_wall_banner, 0),  # not Rendering
            'minecraft:pink_wall_banner': (ids.block_pink_wall_banner, 0),  # not Rendering
            'minecraft:gray_wall_banner': (ids.block_gray_wall_banner, 0),  # not Rendering
            'minecraft:light_gray_wall_banner': (ids.block_light_gray_wall_banner, 0),  # not Rendering
            'minecraft:cyan_wall_banner': (ids.block_cyan_wall_banner, 0),  # not Rendering
            'minecraft:purple_wall_banner': (ids.block_purple_wall_banner, 0),  # not Rendering
            'minecraft:blue_wall_banner': (ids.block_blue_wall_banner, 0),  # not Rendering
            'minecraft:brown_wall_banner': (ids.block_brown_wall_banner, 0),  # not Rendering
            'minecraft:green_wall_banner': (ids.block_green_wall_banner, 0),  # not Rendering
            'minecraft:red_wall_banner': (ids.block_red_wall_banner, 0),  # not Rendering
            'minecraft:black_wall_banner': (ids.block_black_wall_banner, 0),  # not Rendering

            "minecraft:lantern": (ids.block_lantern, 0),

            "minecraft:polished_blackstone_bricks": (ids.block_polished_blackstone_bricks, 0),
            "minecraft:polished_blackstone": (ids.block_polished_blackstone, 0),

            'minecraft:soul_wall_torch': (ids.block_soul_wall_torch, 0),
            'minecraft:soul_torch': (ids.block_soul_torch, 0),

            'minecraft:gilded_blackstone': (ids.block_gilded_blackstone, 0),
            'minecraft:lodestone': (ids.block_lodestone, 0),
            'minecraft:crying_obsidian': (ids.block_crying_obsidian, 0),

            'minecraft:nether_sprouts': (ids.block_nether_sprouts, 0),

            'minecraft:target': (ids.block_target, 0),
            'minecraft:cracked_polished_blackstone_bricks': (ids.block_cracked_polished_blackstone_bricks, 0),
            'minecraft:chiseled_polished_blackstone': (ids.block_chiseled_polished_blackstone, 0),
            'minecraft:chiseled_nether_bricks': (ids.block_chiseled_nether_bricks, 0),
            'minecraft:cracked_nether_bricks': (ids.block_cracked_nether_bricks, 0),
            'minecraft:quartz_bricks': (ids.block_quartz_bricks, 0),

            'minecraft:dead_brain_coral': (ids.block_dead_brain_coral, 0),
            'minecraft:dead_bubble_coral': (ids.block_dead_bubble_coral, 0),
            'minecraft:dead_fire_coral': (ids.block_dead_fire_coral, 0),
            'minecraft:dead_horn_coral': (ids.block_dead_horn_coral, 0),
            'minecraft:dead_tube_coral': (ids.block_dead_tube_coral, 0),

            'minecraft:dead_tube_coral_fan': (ids.block_dead_tube_coral_fan, 0),
            'minecraft:dead_brain_coral_fan': (ids.block_dead_brain_coral_fan, 0),
            'minecraft:dead_bubble_coral_fan': (ids.block_dead_bubble_coral_fan, 0),
            'minecraft:dead_fire_coral_fan': (ids.block_dead_fire_coral_fan, 0),
            'minecraft:dead_horn_coral_fan': (ids.block_dead_horn_coral_fan, 0),

            'minecraft:tube_coral': (ids.block_tube_coral, 0),
            'minecraft:brain_coral': (ids.block_brain_coral, 0),
            'minecraft:bubble_coral': (ids.block_bubble_coral, 0),
            'minecraft:fire_coral': (ids.block_fire_coral, 0),
            'minecraft:horn_coral': (ids.block_horn_coral, 0),

            'minecraft:tube_coral_fan': (ids.block_tube_coral_fan, 0),
            'minecraft:brain_coral_fan': (ids.block_brain_coral_fan, 0),
            'minecraft:bubble_coral_fan': (ids.block_bubble_coral_fan, 0),
            'minecraft:fire_coral_fan': (ids.block_fire_coral_fan, 0),
            'minecraft:horn_coral_fan': (ids.block_horn_coral_fan, 0),

            'minecraft:respawn_anchor': (ids.block_respawn_anchor, 0),

            # minecraft:white_banner  # Not rendering
            # minecraft:orange_banner  # Not rendering
            # minecraft:magenta_banner  # Not rendering
            # minecraft:light_blue_banner  # Not rendering
            # minecraft:yellow_banner  # Not rendering
            # minecraft:lime_banner  # Not rendering
            # minecraft:pink_banner  # Not rendering
            # minecraft:gray_banner  # Not rendering
            # minecraft:light_gray_banner  # Not rendering
            # minecraft:cyan_banner  # Not rendering
            # minecraft:purple_banner  # Not rendering
            # minecraft:blue_banner  # Not rendering
            # minecraft:brown_banner  # Not rendering
            # minecraft:green_banner  # Not rendering
            # minecraft:red_banner  # Not rendering
            # minecraft:black_banner  # Not rendering
            # minecraft:white_wall_banner  # Not rendering
            # minecraft:orange_wall_banner  # Not rendering
            # minecraft:magenta_wall_banner  # Not rendering
            # minecraft:light_blue_wall_banner  # Not rendering
            # minecraft:yellow_wall_banner  # Not rendering
            # minecraft:lime_wall_banner  # Not rendering
            # minecraft:pink_wall_banner  # Not rendering
            # minecraft:gray_wall_banner  # Not rendering
            # minecraft:light_gray_wall_banner  # Not rendering
            # minecraft:cyan_wall_banner  # Not rendering
            # minecraft:purple_wall_banner  # Not rendering
            # minecraft:blue_wall_banner  # Not rendering
            # minecraft:brown_wall_banner  # Not rendering
            # minecraft:green_wall_banner  # Not rendering
            # minecraft:red_wall_banner  # Not rendering
            # minecraft:black_wall_banner  # Not rendering
            # minecraft:moving_piston  # Not rendering
            # minecraft:soul_fire  # Not rendering
            # minecraft:chain  # Not rendering
            # minecraft:skeleton_skull  # Not rendering
            # minecraft:skeleton_wall_skull  # Not rendering
            # minecraft:wither_skeleton_skull  # Not rendering
            # minecraft:wither_skeleton_wall_skull  # Not rendering
            # minecraft:zombie_head  # Not rendering
            # minecraft:zombie_wall_head  # Not rendering
            # minecraft:player_head  # Not rendering
            # minecraft:player_wall_head  # Not rendering
            # minecraft:creeper_head  # Not rendering
            # minecraft:creeper_wall_head  # Not rendering
            # minecraft:dragon_head  # Not rendering
            # minecraft:dragon_wall_head  # Not rendering
            # minecraft:barrier  # Not rendering
            # minecraft:end_rod  # Not rendering
            # minecraft:structure_void  # Not rendering
            # minecraft:kelp  # Not rendering
            # minecraft:kelp_plant  # Not rendering
            # minecraft:turtle_egg  # Not rendering
            # minecraft:dead_tube_coral_wall_fan  # Not rendering
            # minecraft:dead_brain_coral_wall_fan  # Not rendering
            # minecraft:dead_bubble_coral_wall_fan  # Not rendering
            # minecraft:dead_fire_coral_wall_fan  # Not rendering
            # minecraft:dead_horn_coral_wall_fan  # Not rendering
            # minecraft:tube_coral_wall_fan  # Not rendering
            # minecraft:brain_coral_wall_fan  # Not rendering
            # minecraft:bubble_coral_wall_fan  # Not rendering
            # minecraft:fire_coral_wall_fan  # Not rendering
            # minecraft:horn_coral_wall_fan  # Not rendering
            # minecraft:conduit  # Not rendering
            # minecraft:void_air  # Not rendering
            # minecraft:cave_air  # Not rendering
            # minecraft:bubble_column  # Not rendering
            # minecraft:soul_lantern  # Not rendering
        }

    # Re-initialize upon unpickling
    def __getstate__(self):
        return (self.regiondir, self.rel)

    def __setstate__(self, state):
        return self.__init__(*state)

    def __repr__(self):
        return "<RegionSet regiondir=%r>" % self.regiondir

    def _get_block(self, palette_entry):
        key = palette_entry['Name']
        (block, data) = self._blockmap[key]

        if block in [ids.block_redstone_ore, ids.block_redstone_lamp]:
            if palette_entry['Properties']['lit'] == 'true':
                data = 1

        elif block in ids.group_gate:
            facing = palette_entry['Properties']['facing']
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if palette_entry['Properties']['open'] == 'true':
                data += 4

        elif block in ids.group_rail:
            shape = palette_entry['Properties']['shape']
            data = {'north_south': 0, 'east_west': 1, 'ascending_east': 2, 'ascending_west': 3, 'ascending_north': 4, 'ascending_south': 5, 'south_east': 6, 'south_west': 7, 'north_west': 8, 'north_east': 9}[shape]
            if block in ids.group_power_rail and palette_entry['Properties']['powered'] == 'true':
                data |= 8

        elif block in ids.group_redstone_device:
            # Bits 1-2 indicates facing, bits 3-4 indicates delay
            if palette_entry['Properties']['powered'] == 'true':
                block += 1
            facing = palette_entry['Properties']['facing']
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            data |= (int(palette_entry['Properties'].get('delay', '1')) - 1) << 2

        elif block == ids.block_daylight_detector:
            if palette_entry['Properties']['inverted'] == 'true':
                block = 178

        elif block == ids.block_redstone_wire:
            data = int(palette_entry['Properties']['power'] != '0')
            index = 1
            for direction in ['east', 'north', 'west', 'south']:
                if palette_entry['Properties'][direction] == "side":
                    data += (0b1 << index)
                elif palette_entry['Properties'][direction] == "up":
                    data += (0b10 << index)
                index += 2

        elif block == ids.block_grass_block:
            if palette_entry['Properties']['snowy'] == 'true':
                data |= 0x10

        elif block in ids.group_tall_sprite:
            if palette_entry['Properties']['half'] == 'upper':
                data = 1

        elif block in ids.group_slabs:
            if palette_entry['Properties']['type'] == 'top':
                data = 1
            elif palette_entry['Properties']['type'] == 'double':
                block = ids.double_slabs[key]

        elif block in ids.group_only_facing:
            facing = palette_entry['Properties']['facing']
            data = {'north': 2, 'south': 3, 'west': 4, 'east': 5}[facing]

        elif block in ids.group_chest:
            facing = palette_entry['Properties']['facing']
            data = {'north': 2, 'south': 3, 'west': 4, 'east': 5}[facing]
            # type property should exist, but default to 'single' just in case
            chest_type = palette_entry['Properties'].get('type', 'single')
            data |= {'left': 0x8, 'right': 0x10, 'single': 0x0}[chest_type]

        elif block in ids.group_furnace_smoker:
            facing = palette_entry['Properties']['facing']
            data = {'north': 2, 'south': 3, 'west': 4, 'east': 5}[facing]
            data |= 8 if palette_entry['Properties'].get('lit', 'false') == 'true' else 0

        elif block in ids.group_bee:
            facing = palette_entry['Properties']['facing']
            honey_level = int(palette_entry['Properties']['honey_level'])
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if honey_level == 5:
                data = {'south': 4, 'west': 5, 'north': 6, 'east': 7}[facing]

        elif block in ids.group_button:
            facing = palette_entry['Properties']['facing']
            face = palette_entry['Properties']['face']
            if face == 'ceiling':
                block = 0
                data = 0
            elif face == 'wall':
                data = {'east': 1, 'west': 2, 'south': 3, 'north': 4}[facing]
            elif face == 'floor':
                data = {'east': 6, 'west': 6, 'south': 5, 'north': 5}[facing]

        elif block in ids.group_age:
            data = palette_entry['Properties']['age']

        elif block in ids.group_shulker or block in ids.group_piston or block in ids.group_oddj:
            p = palette_entry['Properties']
            data = {'down': 0, 'up': 1, 'north': 2, 'south': 3, 'west': 4, 'east': 5}[p['facing']]
            if ((block in ids.group_piston and p.get('extended', 'false') == 'true') or (block == ids.block_piston_head and p.get('type', 'normal') == 'sticky') or (block == ids.block_observer and p.get('powered', 'false') == 'true')):
                data |= 0x08

        elif block in ids.group_log_wood_bone:
            axis = palette_entry['Properties']['axis']
            if axis == 'x':
                data |= 4
            elif axis == 'z':
                data |= 8

        elif block == ids.block_quartz_pillar:
            axis = palette_entry['Properties']['axis']
            if axis == 'x':
                data = 3
            if axis == 'z':
                data = 4

        elif block in ids.group_basalt:
            axis = palette_entry['Properties']['axis']
            data = {'y': 0, 'x': 1, 'z': 2}[axis]

        elif block in ids.group_torch:
            if block in ids.group_lit_torch and palette_entry['Properties']['lit'] == 'true':
                data += 0b1000
            if block in ids.group_wall_torch:
                facing = palette_entry['Properties'].get('facing')
                data += {'east': 1, 'west': 2, 'south': 3, 'north': 4}[facing]
            # else:
            #     data = 5

        elif (block in ids.group_cjsl or block in ids.group_glazed_terracotta):
            facing = palette_entry['Properties']['facing']
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]

        elif block in ids.group_vbrm:
            p = palette_entry['Properties']
            if p['south'] == 'true':
                data |= 1
            if p['west'] == 'true':
                data |= 2
            if p['north'] == 'true':
                data |= 4
            if p['east'] == 'true':
                data |= 8
            if p['up'] == 'true':
                data |= 16
            # Not all blocks here have the down property, so use dict.get() to avoid errors
            if p.get('down', 'false') == 'true':
                data |= 32

        elif block in ids.group_anvil:
            facing = palette_entry['Properties']['facing']
            if facing == 'west':
                data += 1
            if facing == 'north':
                data += 2
            if facing == 'east':
                data += 3

        elif block in ids.group_all_sign:
            if block in ids.group_wall_sign:
                facing = palette_entry['Properties']['facing']
                if facing == 'north':
                    data = 2
                elif facing == 'west':
                    data = 4
                elif facing == 'south':
                    data = 3
                elif facing == 'east':
                    data = 5
            else:
                p = palette_entry['Properties']
                data = p['rotation']

        elif block in ids.group_fence:
            p = palette_entry['Properties']
            if p['north'] == 'true':
                data |= 1
            if p['west'] == 'true':
                data |= 2
            if p['south'] == 'true':
                data |= 4
            if p['east'] == 'true':
                data |= 8

        elif block in ids.group_stairs:
            facing = palette_entry['Properties']['facing']
            if facing == 'south':
                data = 2
            elif facing == 'east':
                data = 0
            elif facing == 'north':
                data = 3
            elif facing == 'west':
                data = 1
            if palette_entry['Properties']['half'] == 'top':
                data |= 0x4

        elif block in ids.group_door:
            p = palette_entry['Properties']
            if p['hinge'] == 'left':
                data |= 0x10
            if p['open'] == 'true':
                data |= 0x04
            if p['half'] == 'upper':
                data |= 0x08
            data |= {'north': 0x03, 'west': 0x02, 'south': 0x01, 'east': 0x00}[p['facing']]

        elif block in ids.group_trapdoor:
            p = palette_entry['Properties']
            data = {'south': 1, 'north': 0, 'east': 3, 'west': 2}[p['facing']]
            if p['open'] == 'true':
                data |= 0x04
            if p['half'] == 'top':
                data |= 0x08

        elif block == ids.block_lantern:
            if palette_entry['Properties']['hanging'] == 'true':
                data = 1
            else:
                data = 0

        elif block == ids.block_composter:
            data = palette_entry['Properties']['level']

        elif block == ids.block_barrel:
            facing_data = {'up': 0, 'down': 1, 'south': 2, 'east': 3, 'north': 4, 'west': 5}
            data = ((facing_data[palette_entry['Properties']['facing']] << 1) + (1 if palette_entry['Properties']['open'] == 'true' else 0))

        elif block in ids.group_bed:
            facing = palette_entry['Properties']['facing']
            data |= {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if palette_entry['Properties'].get('part', 'foot') == 'head':
                data |= 8

        elif block == ids.block_end_portal_frame:
            facing = palette_entry['Properties']['facing']
            data |= {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if palette_entry['Properties'].get('eye', 'false') == 'true':
                data |= 4

        elif block == ids.block_cauldron:
            data = int(palette_entry['Properties'].get('level', '0'))

        elif block == ids.block_structure_block:
            block_mode = palette_entry['Properties'].get('mode', 'save')
            data = {'save': 0, 'load': 1, 'corner': 2, 'data': 3}.get(block_mode, 0)

        elif block == ids.block_cake:
            data = int(palette_entry['Properties'].get('bites', '0'))

        elif block == ids.block_farmland:
            # A moisture level of 7 has a different texture from other farmland
            data = 1 if palette_entry['Properties'].get('moisture', '0') == '7' else 0

        elif block in ids.group_glcbs:
            p = palette_entry['Properties']
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[p['facing']]
            if block == ids.block_grindstone:
                data |= {'floor': 0, 'wall': 4, 'ceiling': 8}[p['face']]
            elif block == ids.block_lectern:
                if p['has_book'] == 'true':
                    data |= 4
            elif block == ids.block_campfire or block == ids.block_soul_campfire:
                if p['lit'] == 'true':
                    data |= 4
            elif block == ids.block_bell:
                data |= {'floor': 0, 'ceiling': 4, 'single_wall': 8,
                         'double_wall': 12}[p['attachment']]

        elif block == ids.block_respawn_anchor:
            data = palette_entry['Properties']['charges']

        elif block == ids.block_sea_pickle:
            if palette_entry['Properties'].get('waterlogged', False):
                block = ids.block_air
            else:
                block = ids.block_water

        return (block, data)

    def get_type(self):
        """Attempts to return a string describing the dimension
        represented by this regionset.  Usually this is the relative
        path of the regionset within the world, minus the suffix
        /region, but for the main world it's None.
        """
        # path will be normalized in __init__
        return self.type

    def _get_regionobj(self, regionfilename):
        # Check the cache first. If it's not there, create the
        # nbt.MCRFileReader object, cache it, and return it
        # May raise an nbt.CorruptRegionError
        try:
            return self.regioncache[regionfilename]
        except KeyError:
            region = nbt.load_region(regionfilename)
            self.regioncache[regionfilename] = region
            return region

    def _packed_longarray_to_shorts(self, long_array, n, num_palette):
        bits_per_value = (len(long_array) * 64) / n
        if bits_per_value < 4 or 12 < bits_per_value:
            raise nbt.CorruptChunkError()
        b = numpy.frombuffer(numpy.asarray(long_array, dtype=numpy.uint64), dtype=numpy.uint8)
        # give room for work, later
        b = b.astype(numpy.uint16)
        if bits_per_value == 8:
            return b

        result = numpy.zeros((n,), dtype=numpy.uint16)
        if bits_per_value == 4:
            result[0::2] =  b & 0x0f
            result[1::2] = (b & 0xf0) >> 4
        elif bits_per_value == 5:
            result[0::8] =   b[0::5] & 0x1f
            result[1::8] = ((b[1::5] & 0x03) << 3) | ((b[0::5] & 0xe0) >> 5)
            result[2::8] =  (b[1::5] & 0x7c) >> 2
            result[3::8] = ((b[2::5] & 0x0f) << 1) | ((b[1::5] & 0x80) >> 7)
            result[4::8] = ((b[3::5] & 0x01) << 4) | ((b[2::5] & 0xf0) >> 4)
            result[5::8] =  (b[3::5] & 0x3e) >> 1
            result[6::8] = ((b[4::5] & 0x07) << 2) | ((b[3::5] & 0xc0) >> 6)
            result[7::8] =  (b[4::5] & 0xf8) >> 3
        elif bits_per_value == 6:
            result[0::4] =   b[0::3] & 0x3f
            result[1::4] = ((b[1::3] & 0x0f) << 2) | ((b[0::3] & 0xc0) >> 6)
            result[2::4] = ((b[2::3] & 0x03) << 4) | ((b[1::3] & 0xf0) >> 4)
            result[3::4] =  (b[2::3] & 0xfc) >> 2
        elif bits_per_value == 7:
            result[0::8] =   b[0::7] & 0x7f
            result[1::8] = ((b[1::7] & 0x3f) << 1) | ((b[0::7] & 0x80) >> 7)
            result[2::8] = ((b[2::7] & 0x1f) << 2) | ((b[1::7] & 0xc0) >> 6)
            result[3::8] = ((b[3::7] & 0x0f) << 3) | ((b[2::7] & 0xe0) >> 5)
            result[4::8] = ((b[4::7] & 0x07) << 4) | ((b[3::7] & 0xf0) >> 4)
            result[5::8] = ((b[5::7] & 0x03) << 5) | ((b[4::7] & 0xf8) >> 3)
            result[6::8] = ((b[6::7] & 0x01) << 6) | ((b[5::7] & 0xfc) >> 2)
            result[7::8] =  (b[6::7] & 0xfe) >> 1
        # bits_per_value == 8 is handled above
        elif bits_per_value == 9:
            result[0::8] = ((b[1::9] & 0x01) << 8) |   b[0::9]
            result[1::8] = ((b[2::9] & 0x03) << 7) | ((b[1::9] & 0xfe) >> 1)
            result[2::8] = ((b[3::9] & 0x07) << 6) | ((b[2::9] & 0xfc) >> 2)
            result[3::8] = ((b[4::9] & 0x0f) << 5) | ((b[3::9] & 0xf8) >> 3)
            result[4::8] = ((b[5::9] & 0x1f) << 4) | ((b[4::9] & 0xf0) >> 4)
            result[5::8] = ((b[6::9] & 0x3f) << 3) | ((b[5::9] & 0xe0) >> 5)
            result[6::8] = ((b[7::9] & 0x7f) << 2) | ((b[6::9] & 0xc0) >> 6)
            result[7::8] = ( b[8::9]         << 1) | ((b[7::9] & 0x80) >> 7)
        elif bits_per_value == 10:
            result[0::4] = ((b[1::5] & 0x03) << 8) |   b[0::5]
            result[1::4] = ((b[2::5] & 0x0f) << 6) | ((b[1::5] & 0xfc) >> 2)
            result[2::4] = ((b[3::5] & 0x3f) << 4) | ((b[2::5] & 0xf0) >> 4)
            result[3::4] = ( b[4::5]         << 2) | ((b[3::5] & 0xc0) >> 6)
        elif bits_per_value == 11:
            result[0::8] = ((b[ 1::11] & 0x07) << 8 ) |   b[ 0::11]
            result[1::8] = ((b[ 2::11] & 0x3f) << 5 ) | ((b[ 1::11] & 0xf8) >> 3 )
            result[2::8] = ((b[ 4::11] & 0x01) << 10) | ( b[ 3::11]         << 2 ) | ((b[ 2::11] & 0xc0) >> 6 )
            result[3::8] = ((b[ 5::11] & 0x0f) << 7 ) | ((b[ 4::11] & 0xfe) >> 1 )
            result[4::8] = ((b[ 6::11] & 0x7f) << 4 ) | ((b[ 5::11] & 0xf0) >> 4 )
            result[5::8] = ((b[ 8::11] & 0x03) << 9 ) | ( b[ 7::11]         << 1 ) | ((b[ 6::11] & 0x80) >> 7 )
            result[6::8] = ((b[ 9::11] & 0x1f) << 2 ) | ((b[ 8::11] & 0xfc) >> 2 )
            result[7::8] = ( b[10::11]         << 3 ) | ((b[ 9::11] & 0xe0) >> 5 )
        elif bits_per_value == 12:
            result[0::2] = ((b[1::3] & 0x0f) << 8) |   b[0::3]
            result[1::2] = ( b[2::3]         << 4) | ((b[1::3] & 0xf0) >> 4)

        return result
    
    def _packed_longarray_to_shorts_v116(self, long_array, n, num_palette):
        bits_per_value = max(4, (len(long_array) * 64) // n)

        b = numpy.asarray(long_array, dtype=numpy.uint64)
        result = numpy.zeros((n,), dtype=numpy.uint16)
        shorts_per_long = 64 // bits_per_value
        mask = (1 << bits_per_value) - 1

        for i in range(shorts_per_long):
            j = (n + shorts_per_long - 1 - i) // shorts_per_long
            result[i::shorts_per_long] = (b[:j] >> (bits_per_value * i)) & mask
        
        return result

    def _get_blockdata_v113(self, section, unrecognized_block_types, longarray_unpacker):
        # Translate each entry in the palette to a 1.2-era (block, data) int pair.
        num_palette_entries = len(section['Palette'])
        translated_blocks = numpy.zeros((num_palette_entries,), dtype=numpy.uint16) # block IDs
        translated_data = numpy.zeros((num_palette_entries,), dtype=numpy.uint8) # block data
        for i in range(num_palette_entries):
            key = section['Palette'][i]
            try:
                translated_blocks[i], translated_data[i] = self._get_block(key)
            except KeyError:
                pass    # We already have initialised arrays with 0 (= air)

        # Turn the BlockStates array into a 16x16x16 numpy matrix of shorts.
        blocks = numpy.empty((4096,), dtype=numpy.uint16)
        data = numpy.empty((4096,), dtype=numpy.uint8)
        block_states = longarray_unpacker(section['BlockStates'], 4096, num_palette_entries)
        blocks[:] = translated_blocks[block_states]
        data[:] = translated_data[block_states]

        # Turn the Data array into a 16x16x16 matrix, same as SkyLight
        blocks  = blocks.reshape((16, 16, 16))
        data = data.reshape((16, 16, 16))

        return (blocks, data)

    def _get_blockdata_v112(self, section):
        # Turn the Data array into a 16x16x16 matrix, same as SkyLight
        data = numpy.frombuffer(section['Data'], dtype=numpy.uint8)
        data = data.reshape((16,16,8))
        data_expanded = numpy.empty((16,16,16), dtype=numpy.uint8)
        data_expanded[:,:,::2] = data & 0x0F
        data_expanded[:,:,1::2] = (data & 0xF0) >> 4

        # Turn the Blocks array into a 16x16x16 numpy matrix of shorts,
        # adding in the additional block array if included.
        blocks = numpy.frombuffer(section['Blocks'], dtype=numpy.uint8)
        # Cast up to uint16, blocks can have up to 12 bits of data
        blocks = blocks.astype(numpy.uint16)
        blocks = blocks.reshape((16,16,16))
        if "Add" in section:
            # This section has additional bits to tack on to the blocks
            # array. Add is a packed array with 4 bits per slot, so
            # it needs expanding
            additional = numpy.frombuffer(section['Add'], dtype=numpy.uint8)
            additional = additional.astype(numpy.uint16).reshape((16,16,8))
            additional_expanded = numpy.empty((16,16,16), dtype=numpy.uint16)
            additional_expanded[:,:,::2] = (additional & 0x0F) << 8
            additional_expanded[:,:,1::2] = (additional & 0xF0) << 4
            blocks += additional_expanded
            del additional
            del additional_expanded
            del section['Add'] # Save some memory

        return (blocks, data_expanded)

    #@log_other_exceptions
    def get_chunk(self, x, z):
        """Returns a dictionary object representing the "Level" NBT Compound
        structure for a chunk given its x, z coordinates. The coordinates given
        are chunk coordinates. Raises ChunkDoesntExist exception if the given
        chunk does not exist.

        The returned dictionary corresponds to the "Level" structure in the
        chunk file, with a few changes:

        * The Biomes array is transformed into a 16x16 numpy array

        * For each chunk section:

          * The "Blocks" byte string is transformed into a 16x16x16 numpy array
          * The Add array, if it exists, is bitshifted left 8 bits and
            added into the Blocks array
          * The "SkyLight" byte string is transformed into a 16x16x128 numpy
            array
          * The "BlockLight" byte string is transformed into a 16x16x128 numpy
            array
          * The "Data" byte string is transformed into a 16x16x128 numpy array

        Warning: the returned data may be cached and thus should not be
        modified, lest it affect the return values of future calls for the same
        chunk.
        """
        regionfile = self._get_region_path(x, z)
        if regionfile is None:
            raise ChunkDoesntExist("Chunk %s,%s doesn't exist (and neither does its region)" % (x,z))

        # Try a few times to load and parse this chunk before giving up and
        # raising an error
        tries = 5
        while True:
            try:
                region = self._get_regionobj(regionfile)
                data = region.load_chunk(x, z)
            except nbt.CorruptionError as e:
                tries -= 1
                if tries > 0:
                    # Flush the region cache to possibly read a new region file header
                    logging.debug("Encountered a corrupt chunk or read error at %s,%s. "
                                  "Flushing cache and retrying", x, z)
                    del self.regioncache[regionfile]
                    time.sleep(0.25)
                    continue
                else:
                    logging.warning("The following was encountered while reading from %s:", self.regiondir)
                    if isinstance(e, nbt.CorruptRegionError):
                        logging.warning("Tried several times to read chunk %d,%d. Its region (%d,%d) may be corrupt. Giving up.",
                                x, z,x//32,z//32)
                    elif isinstance(e, nbt.CorruptChunkError):
                        logging.warning("Tried several times to read chunk %d,%d. It may be corrupt. Giving up.",
                                x, z)
                    else:
                        logging.warning("Tried several times to read chunk %d,%d. Unknown error. Giving up.",
                                x, z)
                    logging.debug("Full traceback:", exc_info=1)
                    # Let this exception propagate out through the C code into
                    # tileset.py, where it is caught and gracefully continues
                    # with the next chunk
                    raise
            else:
                # no exception raised: break out of the loop
                break

        if data is None:
            raise ChunkDoesntExist("Chunk %s,%s doesn't exist" % (x,z))

        level = data[1]['Level']
        chunk_data = level

        longarray_unpacker = self._packed_longarray_to_shorts
        if data[1].get('DataVersion', 0) >= 2529:
            # starting with 1.16 snapshot 20w17a, block states are packed differently
            longarray_unpacker = self._packed_longarray_to_shorts_v116

        # From the interior of a map to the edge, a chunk's status may be one of:
        # - postprocessed (interior, or next to fullchunk)
        # - fullchunk (next to decorated)
        # - decorated (next to liquid_carved)
        # - liquid_carved (next to carved)
        # - carved (edge of world)
        # - empty
        # Empty is self-explanatory, and liquid_carved and carved seem to correspond
        # to SkyLight not being calculated, which results in mostly-black chunks,
        # so we'll just pretend they aren't there.
        if chunk_data.get("Status", "") not in ("full", "postprocessed", "fullchunk",
                                                "mobs_spawned", "spawn", ""):
            raise ChunkDoesntExist("Chunk %s,%s doesn't exist" % (x,z))

        # Turn the Biomes array into a 16x16 numpy array
        if 'Biomes' in chunk_data and len(chunk_data['Biomes']) > 0:
            biomes = chunk_data['Biomes']
            if isinstance(biomes, bytes):
                biomes = numpy.frombuffer(biomes, dtype=numpy.uint8)
            else:
                biomes = numpy.asarray(biomes)
            biomes = reshape_biome_data(biomes)
        else:
            # Worlds converted by Jeb's program may be missing the Biomes key.
            # Additionally, 19w09a worlds have an empty array as biomes key
            # in some cases.
            biomes = numpy.zeros((16, 16), dtype=numpy.uint8)
        chunk_data['Biomes'] = biomes
        chunk_data['NewBiomes'] = (len(biomes.shape) == 3)

        unrecognized_block_types = {}
        for section in chunk_data['Sections']:

            # Turn the skylight array into a 16x16x16 matrix. The array comes
            # packed 2 elements per byte, so we need to expand it.
            try:
                # Sometimes, Minecraft loves generating chunks with no light info.
                # These mostly appear to have those two properties, and in this case
                # we default to full-bright as it's less jarring to look at than all-black.
                if chunk_data.get("Status", "") == "spawn" and 'Lights' in chunk_data:
                    section['SkyLight'] = numpy.full((16,16,16), 255, dtype=numpy.uint8)
                else:
                    if 'SkyLight' in section:
                        skylight = numpy.frombuffer(section['SkyLight'], dtype=numpy.uint8)
                        skylight = skylight.reshape((16,16,8))
                    else:   # Special case introduced with 1.14
                        skylight = numpy.zeros((16,16,8), dtype=numpy.uint8)
                    skylight_expanded = numpy.empty((16,16,16), dtype=numpy.uint8)
                    skylight_expanded[:,:,::2] = skylight & 0x0F
                    skylight_expanded[:,:,1::2] = (skylight & 0xF0) >> 4
                    del skylight
                    section['SkyLight'] = skylight_expanded

                # Turn the BlockLight array into a 16x16x16 matrix, same as SkyLight
                if 'BlockLight' in section:
                    blocklight = numpy.frombuffer(section['BlockLight'], dtype=numpy.uint8)
                    blocklight = blocklight.reshape((16,16,8))
                else:   # Special case introduced with 1.14
                    blocklight = numpy.zeros((16,16,8), dtype=numpy.uint8)
                blocklight_expanded = numpy.empty((16,16,16), dtype=numpy.uint8)
                blocklight_expanded[:,:,::2] = blocklight & 0x0F
                blocklight_expanded[:,:,1::2] = (blocklight & 0xF0) >> 4
                del blocklight
                section['BlockLight'] = blocklight_expanded

                if 'Palette' in section:
                    (blocks, data) = self._get_blockdata_v113(section, unrecognized_block_types, longarray_unpacker)
                elif 'Data' in section:
                    (blocks, data) = self._get_blockdata_v112(section)
                else:   # Special case introduced with 1.14
                    blocks = numpy.zeros((16,16,16), dtype=numpy.uint16)
                    data = numpy.zeros((16,16,16), dtype=numpy.uint8)
                (section['Blocks'], section['Data']) = (blocks, data)

            except ValueError:
                # iv'e seen at least 1 case where numpy raises a value error during the reshapes.  i'm not
                # sure what's going on here, but let's treat this as a corrupt chunk error
                logging.warning("There was a problem reading chunk %d,%d.  It might be corrupt.  I am giving up and will not render this particular chunk.", x, z)

                logging.debug("Full traceback:", exc_info=1)
                raise nbt.CorruptChunkError()

        for k in unrecognized_block_types:
            logging.debug("Found %d blocks of unknown type %s" % (unrecognized_block_types[k], k))

        return chunk_data


    def iterate_chunks(self):
        """Returns an iterator over all chunk metadata in this world. Iterates
        over tuples of integers (x,z,mtime) for each chunk.  Other chunk data
        is not returned here.

        """

        for (regionx, regiony), (regionfile, filemtime) in self.regionfiles.items():
            try:
                mcr = self._get_regionobj(regionfile)
            except nbt.CorruptRegionError:
                logging.warning("Found a corrupt region file at %s,%s in %s, Skipping it.", regionx, regiony, self.regiondir)
                continue
            for chunkx, chunky in mcr.get_chunks():
                yield chunkx+32*regionx, chunky+32*regiony, mcr.get_chunk_timestamp(chunkx, chunky)

    def iterate_newer_chunks(self, mtime):
        """Returns an iterator over all chunk metadata in this world. Iterates
        over tuples of integers (x,z,mtime) for each chunk.  Other chunk data
        is not returned here.

        """

        for (regionx, regiony), (regionfile, filemtime) in self.regionfiles.items():
            """ SKIP LOADING A REGION WHICH HAS NOT BEEN MODIFIED! """
            if (filemtime < mtime):
                continue

            try:
                mcr = self._get_regionobj(regionfile)
            except nbt.CorruptRegionError:
                logging.warning("Found a corrupt region file at %s,%s in %s, Skipping it.", regionx, regiony, self.regiondir)
                continue

            for chunkx, chunky in mcr.get_chunks():
                yield chunkx+32*regionx, chunky+32*regiony, mcr.get_chunk_timestamp(chunkx, chunky)

    def get_chunk_mtime(self, x, z):
        """Returns a chunk's mtime, or False if the chunk does not exist.  This
        is therefore a dual purpose method. It corrects for the given north
        direction as described in the docs for get_chunk()

        """

        regionfile = self._get_region_path(x,z)
        if regionfile is None:
            return None
        try:
            data = self._get_regionobj(regionfile)
        except nbt.CorruptRegionError:
            logging.warning("Ignoring request for chunk %s,%s; region %s,%s seems to be corrupt",
                    x,z, x//32,z//32)
            return None
        if data.chunk_exists(x,z):
            return data.get_chunk_timestamp(x,z)
        return None

    def _get_region_path(self, chunkX, chunkY):
        """Returns the path to the region that contains chunk (chunkX, chunkY)
        Coords can be either be global chunk coords, or local to a region

        """
        (regionfile,filemtime) = self.regionfiles.get((chunkX//32, chunkY//32),(None, None))
        return regionfile

    def _iterate_regionfiles(self):
        """Returns an iterator of all of the region files, along with their
        coordinates

        Returns (regionx, regiony, filename)"""

        logging.debug("regiondir is %s, has type %r", self.regiondir, self.type)

        for f in os.listdir(self.regiondir):
            if re.match(r"^r\.-?\d+\.-?\d+\.mca$", f):
                p = f.split(".")
                x = int(p[1])
                y = int(p[2])
                if abs(x) > 500000 or abs(y) > 500000:
                    logging.warning("Holy shit what is up with region file %s !?" % f)
                yield (x, y, os.path.join(self.regiondir, f))

class RegionSetWrapper(object):
    """This is the base class for all "wrappers" of RegionSet objects. A
    wrapper is an object that acts similarly to a subclass: some methods are
    overridden and functionality is changed, others may not be. The difference
    here is that these wrappers may wrap each other, forming chains.

    In fact, subclasses of this object may act exactly as if they've subclassed
    the original RegionSet object, except the first parameter of the
    constructor is a regionset object, not a regiondir.

    This class must implement the full public interface of RegionSet objects

    """
    def __init__(self, rsetobj):
        self._r = rsetobj

    @property
    def regiondir(self):
        """
        RegionSetWrapper are wrappers around a RegionSet and thus should have all variables the RegionSet has.

        Reason for addition: Issue #1706
        The __lt__ check in RegionSet did not check if it is a RegionSetWrapper Instance
        """
        return self._r.regiondir

    @regiondir.setter
    def regiondir(self, value):
        """
        For completeness adding the setter to the property
        """
        self._r.regiondir = value

    def get_type(self):
        return self._r.get_type()
    def get_biome_data(self, x, z):
        return self._r.get_biome_data(x,z)
    def get_chunk(self, x, z):
        return self._r.get_chunk(x,z)
    def iterate_chunks(self):
        return self._r.iterate_chunks()
    def iterate_newer_chunks(self,filemtime):
        return self._r.iterate_newer_chunks(filemtime)
    def get_chunk_mtime(self, x, z):
        return self._r.get_chunk_mtime(x,z)

# see RegionSet.rotate.  These values are chosen so that they can be
# passed directly to rot90; this means that they're the number of
# times to rotate by 90 degrees CCW
UPPER_LEFT  = 0 ## - Return the world such that north is down the -Z axis (no rotation)
UPPER_RIGHT = 1 ## - Return the world such that north is down the +X axis (rotate 90 degrees counterclockwise)
LOWER_RIGHT = 2 ## - Return the world such that north is down the +Z axis (rotate 180 degrees)
LOWER_LEFT  = 3 ## - Return the world such that north is down the -X axis (rotate 90 degrees clockwise)

class RotatedRegionSet(RegionSetWrapper):
    """A regionset, only rotated such that north points in the given direction

    """

    # some class-level rotation constants
    _NO_ROTATION =               lambda x,z: (x,z)
    _ROTATE_CLOCKWISE =          lambda x,z: (-z,x)
    _ROTATE_COUNTERCLOCKWISE =   lambda x,z: (z,-x)
    _ROTATE_180 =                lambda x,z: (-x,-z)

    # These take rotated coords and translate into un-rotated coords
    _unrotation_funcs = [
        _NO_ROTATION,
        _ROTATE_COUNTERCLOCKWISE,
        _ROTATE_180,
        _ROTATE_CLOCKWISE,
    ]

    # These translate un-rotated coordinates into rotated coordinates
    _rotation_funcs = [
        _NO_ROTATION,
        _ROTATE_CLOCKWISE,
        _ROTATE_180,
        _ROTATE_COUNTERCLOCKWISE,
    ]

    def __init__(self, rsetobj, north_dir):
        self.north_dir = north_dir
        self.unrotate = self._unrotation_funcs[north_dir]
        self.rotate = self._rotation_funcs[north_dir]

        super(RotatedRegionSet, self).__init__(rsetobj)


    # Re-initialize upon unpickling. This is needed because we store a couple
    # lambda functions as instance variables
    def __getstate__(self):
        return (self._r, self.north_dir)
    def __setstate__(self, args):
        self.__init__(args[0], args[1])

    def get_chunk(self, x, z):
        x,z = self.unrotate(x,z)
        chunk_data = dict(super(RotatedRegionSet, self).get_chunk(x,z))
        newsections = []
        for section in chunk_data['Sections']:
            section = dict(section)
            newsections.append(section)
            for arrayname in ['Blocks', 'Data', 'SkyLight', 'BlockLight']:
                array = section[arrayname]
                # Since the anvil change, arrays are arranged with axes Y,Z,X
                # numpy.rot90 always rotates the first two axes, so for it to
                # work, we need to temporarily move the X axis to the 0th axis.
                array = numpy.swapaxes(array, 0,2)
                array = numpy.rot90(array, self.north_dir)
                array = numpy.swapaxes(array, 0,2)
                section[arrayname] = array
        chunk_data['Sections'] = newsections

        if chunk_data['NewBiomes']:
            array = numpy.swapaxes(chunk_data['Biomes'], 0, 2)
            array = numpy.rot90(array, self.north_dir)
            chunk_data['Biomes'] = numpy.swapaxes(array, 0, 2)
        else:
            # same as above, for biomes (Z/X indexed)
            biomes = numpy.swapaxes(chunk_data['Biomes'], 0, 1)
            biomes = numpy.rot90(biomes, self.north_dir)
            chunk_data['Biomes'] = numpy.swapaxes(biomes, 0, 1)
        return chunk_data

    def get_chunk_mtime(self, x, z):
        x,z = self.unrotate(x,z)
        return super(RotatedRegionSet, self).get_chunk_mtime(x, z)

    def iterate_chunks(self):
        for x,z,mtime in super(RotatedRegionSet, self).iterate_chunks():
            x,z = self.rotate(x,z)
            yield x,z,mtime

    def iterate_newer_chunks(self, filemtime):
        for x,z,mtime in super(RotatedRegionSet, self).iterate_newer_chunks(filemtime):
            x,z = self.rotate(x,z)
            yield x,z,mtime

class CroppedRegionSet(RegionSetWrapper):
    def __init__(self, rsetobj, xmin, zmin, xmax, zmax):
        super(CroppedRegionSet, self).__init__(rsetobj)
        self.xmin = xmin//16
        self.xmax = xmax//16
        self.zmin = zmin//16
        self.zmax = zmax//16

    def get_chunk(self,x,z):
        if (
                self.xmin <= x <= self.xmax and
                self.zmin <= z <= self.zmax
                ):
            return super(CroppedRegionSet, self).get_chunk(x,z)
        else:
            raise ChunkDoesntExist("This chunk is out of the requested bounds")

    def iterate_chunks(self):
        return ((x,z,mtime) for (x,z,mtime) in super(CroppedRegionSet,self).iterate_chunks()
                if
                    self.xmin <= x <= self.xmax and
                    self.zmin <= z <= self.zmax
                )

    def iterate_newer_chunks(self, filemtime):
        return ((x,z,mtime) for (x,z,mtime) in super(CroppedRegionSet,self).iterate_newer_chunks(filemtime)
                if
                    self.xmin <= x <= self.xmax and
                    self.zmin <= z <= self.zmax
                )

    def get_chunk_mtime(self,x,z):
        if (
                self.xmin <= x <= self.xmax and
                self.zmin <= z <= self.zmax
                ):
            return super(CroppedRegionSet, self).get_chunk_mtime(x,z)
        else:
            return None

class CachedRegionSet(RegionSetWrapper):
    """A regionset wrapper that implements caching of the results from
    get_chunk()

    """
    def __init__(self, rsetobj, cacheobjects):
        """Initialize this wrapper around the given regionset object and with
        the given list of cache objects. The cache objects may be shared among
        other CachedRegionSet objects.

        """
        super(CachedRegionSet, self).__init__(rsetobj)
        self.caches = cacheobjects

        # Construct a key from the sequence of transformations and the real
        # RegionSet object, so that items we place in the cache don't conflict
        # with other worlds/transformation combinations.
        obj = self._r
        s = ""
        while isinstance(obj, RegionSetWrapper):
            s += obj.__class__.__name__ + "."
            obj = obj._r
        # obj should now be the actual RegionSet object
        try:
            s += obj.regiondir
        except AttributeError:
            s += repr(obj)

        logging.debug("Initializing a cache with key '%s'", s)

        self.key = s

    def get_chunk(self, x, z):
        key = (self.key, x, z)
        for i, cache in enumerate(self.caches):
            try:
                retval = cache[key]
                # This did have it, no need to re-add it to this cache, just
                # the ones before it
                i -= 1
                break
            except KeyError:
                pass
        else:
            retval = super(CachedRegionSet, self).get_chunk(x,z)

        # Now add retval to all the caches that didn't have it, all the caches
        # up to and including index i
        for cache in self.caches[:i+1]:
            cache[key] = retval

        return retval


def get_save_dir():
    """Returns the path to the local saves directory
      * On Windows, at %APPDATA%/.minecraft/saves/
      * On Darwin, at $HOME/Library/Application Support/minecraft/saves/
      * at $HOME/.minecraft/saves/

    """

    savepaths = []
    if "APPDATA" in os.environ:
        savepaths += [os.path.join(os.environ['APPDATA'], ".minecraft", "saves")]
    if "HOME" in os.environ:
        savepaths += [os.path.join(os.environ['HOME'], "Library",
                "Application Support", "minecraft", "saves")]
        savepaths += [os.path.join(os.environ['HOME'], ".minecraft", "saves")]

    for path in savepaths:
        if os.path.exists(path):
            return path

def get_worlds():
    "Returns {world # or name : level.dat information}"
    ret = {}
    save_dir = get_save_dir()

    # No dirs found - most likely not running from inside minecraft-dir
    if not save_dir is None:
        for dir in os.listdir(save_dir):
            world_path = os.path.join(save_dir, dir)
            world_dat = os.path.join(world_path, "level.dat")
            if not os.path.exists(world_dat): continue
            try:
                info = nbt.load(world_dat)[1]
                info['Data']['path'] = os.path.join(save_dir, dir)
                if 'LevelName' in info['Data'].keys():
                    ret[info['Data']['LevelName']] = info['Data']
            except nbt.CorruptNBTError:
                ret[os.path.basename(world_path) + " (corrupt)"] = {
                    'path': world_path,
                    'LastPlayed': 0,
                    'Time': 0,
                    'IsCorrupt': True}


    for dir in os.listdir("."):
        world_dat = os.path.join(dir, "level.dat")
        if not os.path.exists(world_dat): continue
        world_path = os.path.join(".", dir)
        try:
            info = nbt.load(world_dat)[1]
            info['Data']['path'] = world_path
            if 'LevelName' in info['Data'].keys():
                ret[info['Data']['LevelName']] = info['Data']
        except nbt.CorruptNBTError:
            ret[os.path.basename(world_path) + " (corrupt)"] = {'path': world_path,
                    'LastPlayed': 0,
                    'Time': 0,
                    'IsCorrupt': True}

    return ret

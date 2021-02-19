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
            ### FLAG FULLNAME IDS ###
            'minecraft:acacia_button': (ids.block_minecraft__acacia_button, 0),
            'minecraft:acacia_door': (ids.block_minecraft__acacia_door, 0),
            'minecraft:acacia_fence': (ids.block_minecraft__acacia_fence, 0),
            'minecraft:acacia_fence_gate': (ids.block_minecraft__acacia_fence_gate, 0),
            'minecraft:acacia_leaves': (ids.block_minecraft__acacia_leaves, 0),
            'minecraft:acacia_log': (ids.block_minecraft__acacia_log, 0),
            'minecraft:acacia_planks': (ids.block_minecraft__acacia_planks, 0),
            'minecraft:acacia_pressure_plate': (ids.block_minecraft__acacia_pressure_plate, 0),
            'minecraft:acacia_sapling': (ids.block_minecraft__acacia_sapling, 0),
            'minecraft:acacia_sign': (ids.block_minecraft__acacia_sign, 0),
            'minecraft:acacia_slab': (ids.block_minecraft__acacia_slab, 0),
            'minecraft:acacia_stairs': (ids.block_minecraft__acacia_stairs, 0),
            'minecraft:acacia_trapdoor': (ids.block_minecraft__acacia_trapdoor, 0),
            'minecraft:acacia_wall_sign': (ids.block_minecraft__acacia_wall_sign, 0),
            'minecraft:acacia_wood': (ids.block_minecraft__acacia_wood, 0),
            'minecraft:activator_rail': (ids.block_minecraft__activator_rail, 0),
            'minecraft:air': (ids.block_minecraft__air, 0),
            'minecraft:allium': (ids.block_minecraft__allium, 0),
            'minecraft:ancient_debris': (ids.block_minecraft__ancient_debris, 0),
            'minecraft:andesite': (ids.block_minecraft__andesite, 0),
            'minecraft:andesite_slab': (ids.block_minecraft__andesite_slab, 0),
            'minecraft:andesite_stairs': (ids.block_minecraft__andesite_stairs, 0),
            'minecraft:andesite_wall': (ids.block_minecraft__andesite_wall, 0),
            'minecraft:anvil': (ids.block_minecraft__anvil, 0),
            'minecraft:attached_melon_stem': (ids.block_minecraft__attached_melon_stem, 0),
            'minecraft:attached_pumpkin_stem': (ids.block_minecraft__attached_pumpkin_stem, 0),
            'minecraft:azure_bluet': (ids.block_minecraft__azure_bluet, 0),
            'minecraft:bamboo': (ids.block_minecraft__bamboo, 0),
            'minecraft:bamboo_sapling': (ids.block_minecraft__bamboo_sapling, 0),
            'minecraft:barrel': (ids.block_minecraft__barrel, 0),
            'minecraft:barrier': (ids.block_minecraft__barrier, 0),
            'minecraft:basalt': (ids.block_minecraft__basalt, 0),
            'minecraft:beacon': (ids.block_minecraft__beacon, 0),
            'minecraft:bedrock': (ids.block_minecraft__bedrock, 0),
            'minecraft:bee_nest': (ids.block_minecraft__bee_nest, 0),
            'minecraft:beehive': (ids.block_minecraft__beehive, 0),
            'minecraft:beetroots': (ids.block_minecraft__beetroots, 0),
            'minecraft:bell': (ids.block_minecraft__bell, 0),
            'minecraft:birch_button': (ids.block_minecraft__birch_button, 0),
            'minecraft:birch_door': (ids.block_minecraft__birch_door, 0),
            'minecraft:birch_fence': (ids.block_minecraft__birch_fence, 0),
            'minecraft:birch_fence_gate': (ids.block_minecraft__birch_fence_gate, 0),
            'minecraft:birch_leaves': (ids.block_minecraft__birch_leaves, 0),
            'minecraft:birch_log': (ids.block_minecraft__birch_log, 0),
            'minecraft:birch_planks': (ids.block_minecraft__birch_planks, 0),
            'minecraft:birch_pressure_plate': (ids.block_minecraft__birch_pressure_plate, 0),
            'minecraft:birch_sapling': (ids.block_minecraft__birch_sapling, 0),
            'minecraft:birch_sign': (ids.block_minecraft__birch_sign, 0),
            'minecraft:birch_slab': (ids.block_minecraft__birch_slab, 0),
            'minecraft:birch_stairs': (ids.block_minecraft__birch_stairs, 0),
            'minecraft:birch_trapdoor': (ids.block_minecraft__birch_trapdoor, 0),
            'minecraft:birch_wall_sign': (ids.block_minecraft__birch_wall_sign, 0),
            'minecraft:birch_wood': (ids.block_minecraft__birch_wood, 0),
            'minecraft:black_banner': (ids.block_minecraft__black_banner, 0),
            'minecraft:black_bed': (ids.block_minecraft__black_bed, 0),
            'minecraft:black_carpet': (ids.block_minecraft__black_carpet, 0),
            'minecraft:black_concrete': (ids.block_minecraft__black_concrete, 0),
            'minecraft:black_concrete_powder': (ids.block_minecraft__black_concrete_powder, 0),
            'minecraft:black_glazed_terracotta': (ids.block_minecraft__black_glazed_terracotta, 0),
            'minecraft:black_shulker_box': (ids.block_minecraft__black_shulker_box, 0),
            'minecraft:black_stained_glass': (ids.block_minecraft__black_stained_glass, 0),
            'minecraft:black_stained_glass_pane': (ids.block_minecraft__black_stained_glass_pane, 0),
            'minecraft:black_terracotta': (ids.block_minecraft__black_terracotta, 0),
            'minecraft:black_wall_banner': (ids.block_minecraft__black_wall_banner, 0),
            'minecraft:black_wool': (ids.block_minecraft__black_wool, 0),
            'minecraft:blackstone': (ids.block_minecraft__blackstone, 0),
            'minecraft:blackstone_slab': (ids.block_minecraft__blackstone_slab, 0),
            'minecraft:blackstone_stairs': (ids.block_minecraft__blackstone_stairs, 0),
            'minecraft:blackstone_wall': (ids.block_minecraft__blackstone_wall, 0),
            'minecraft:blast_furnace': (ids.block_minecraft__blast_furnace, 0),
            'minecraft:blue_banner': (ids.block_minecraft__blue_banner, 0),
            'minecraft:blue_bed': (ids.block_minecraft__blue_bed, 0),
            'minecraft:blue_carpet': (ids.block_minecraft__blue_carpet, 0),
            'minecraft:blue_concrete': (ids.block_minecraft__blue_concrete, 0),
            'minecraft:blue_concrete_powder': (ids.block_minecraft__blue_concrete_powder, 0),
            'minecraft:blue_glazed_terracotta': (ids.block_minecraft__blue_glazed_terracotta, 0),
            'minecraft:blue_ice': (ids.block_minecraft__blue_ice, 0),
            'minecraft:blue_orchid': (ids.block_minecraft__blue_orchid, 0),
            'minecraft:blue_shulker_box': (ids.block_minecraft__blue_shulker_box, 0),
            'minecraft:blue_stained_glass': (ids.block_minecraft__blue_stained_glass, 0),
            'minecraft:blue_stained_glass_pane': (ids.block_minecraft__blue_stained_glass_pane, 0),
            'minecraft:blue_terracotta': (ids.block_minecraft__blue_terracotta, 0),
            'minecraft:blue_wall_banner': (ids.block_minecraft__blue_wall_banner, 0),
            'minecraft:blue_wool': (ids.block_minecraft__blue_wool, 0),
            'minecraft:bone_block': (ids.block_minecraft__bone_block, 0),
            'minecraft:bookshelf': (ids.block_minecraft__bookshelf, 0),
            'minecraft:brain_coral': (ids.block_minecraft__water, 0),
            'minecraft:brain_coral_block': (ids.block_minecraft__brain_coral_block, 0),
            'minecraft:brain_coral_fan': (ids.block_minecraft__water, 0),
            'minecraft:brain_coral_wall_fan': (ids.block_minecraft__water, 0),
            'minecraft:brewing_stand': (ids.block_minecraft__brewing_stand, 0),
            'minecraft:brick_slab': (ids.block_minecraft__brick_slab, 0),
            'minecraft:brick_stairs': (ids.block_minecraft__brick_stairs, 0),
            'minecraft:brick_wall': (ids.block_minecraft__brick_wall, 0),
            'minecraft:bricks': (ids.block_minecraft__bricks, 0),
            'minecraft:brown_banner': (ids.block_minecraft__brown_banner, 0),
            'minecraft:brown_bed': (ids.block_minecraft__brown_bed, 0),
            'minecraft:brown_carpet': (ids.block_minecraft__brown_carpet, 0),
            'minecraft:brown_concrete': (ids.block_minecraft__brown_concrete, 0),
            'minecraft:brown_concrete_powder': (ids.block_minecraft__brown_concrete_powder, 0),
            'minecraft:brown_glazed_terracotta': (ids.block_minecraft__brown_glazed_terracotta, 0),
            'minecraft:brown_mushroom': (ids.block_minecraft__brown_mushroom, 0),
            'minecraft:brown_mushroom_block': (ids.block_minecraft__brown_mushroom_block, 0),
            'minecraft:brown_shulker_box': (ids.block_minecraft__brown_shulker_box, 0),
            'minecraft:brown_stained_glass': (ids.block_minecraft__brown_stained_glass, 0),
            'minecraft:brown_stained_glass_pane': (ids.block_minecraft__brown_stained_glass_pane, 0),
            'minecraft:brown_terracotta': (ids.block_minecraft__brown_terracotta, 0),
            'minecraft:brown_wall_banner': (ids.block_minecraft__brown_wall_banner, 0),
            'minecraft:brown_wool': (ids.block_minecraft__brown_wool, 0),
            'minecraft:bubble_column': (ids.block_minecraft__water, 0),
            'minecraft:bubble_coral': (ids.block_minecraft__water, 0),
            'minecraft:bubble_coral_block': (ids.block_minecraft__bubble_coral_block, 0),
            'minecraft:bubble_coral_fan': (ids.block_minecraft__water, 0),
            'minecraft:bubble_coral_wall_fan': (ids.block_minecraft__water, 0),
            'minecraft:cactus': (ids.block_minecraft__cactus, 0),
            'minecraft:cake': (ids.block_minecraft__cake, 0),
            'minecraft:campfire': (ids.block_minecraft__campfire, 0),
            'minecraft:carrots': (ids.block_minecraft__carrots, 0),
            'minecraft:cartography_table': (ids.block_minecraft__cartography_table, 0),
            'minecraft:carved_pumpkin': (ids.block_minecraft__carved_pumpkin, 0),
            'minecraft:cauldron': (ids.block_minecraft__cauldron, 0),
            'minecraft:cave_air': (ids.block_minecraft__cave_air, 0),
            'minecraft:chain': (ids.block_minecraft__chain, 0),
            'minecraft:chain_command_block': (ids.block_minecraft__chain_command_block, 0),
            'minecraft:chest': (ids.block_minecraft__chest, 0),
            'minecraft:chipped_anvil': (ids.block_minecraft__chipped_anvil, 0),
            'minecraft:chiseled_nether_bricks': (ids.block_minecraft__chiseled_nether_bricks, 0),
            'minecraft:chiseled_polished_blackstone': (ids.block_minecraft__chiseled_polished_blackstone, 0),
            'minecraft:chiseled_quartz_block': (ids.block_minecraft__chiseled_quartz_block, 0),
            'minecraft:chiseled_red_sandstone': (ids.block_minecraft__chiseled_red_sandstone, 0),
            'minecraft:chiseled_sandstone': (ids.block_minecraft__chiseled_sandstone, 0),
            'minecraft:chiseled_stone_bricks': (ids.block_minecraft__chiseled_stone_bricks, 0),
            'minecraft:chorus_flower': (ids.block_minecraft__chorus_flower, 0),
            'minecraft:chorus_plant': (ids.block_minecraft__chorus_plant, 0),
            'minecraft:clay': (ids.block_minecraft__clay, 0),
            'minecraft:coal_block': (ids.block_minecraft__coal_block, 0),
            'minecraft:coal_ore': (ids.block_minecraft__coal_ore, 0),
            'minecraft:coarse_dirt': (ids.block_minecraft__coarse_dirt, 0),
            'minecraft:cobblestone': (ids.block_minecraft__cobblestone, 0),
            'minecraft:cobblestone_slab': (ids.block_minecraft__cobblestone_slab, 0),
            'minecraft:cobblestone_stairs': (ids.block_minecraft__cobblestone_stairs, 0),
            'minecraft:cobblestone_wall': (ids.block_minecraft__cobblestone_wall, 0),
            'minecraft:cobweb': (ids.block_minecraft__cobweb, 0),
            'minecraft:cocoa': (ids.block_minecraft__cocoa, 0),
            'minecraft:command_block': (ids.block_minecraft__command_block, 0),
            'minecraft:comparator': (ids.block_minecraft__comparator, 0),
            'minecraft:composter': (ids.block_minecraft__composter, 0),
            'minecraft:conduit': (ids.block_minecraft__conduit, 0),
            'minecraft:cornflower': (ids.block_minecraft__cornflower, 0),
            'minecraft:cracked_nether_bricks': (ids.block_minecraft__cracked_nether_bricks, 0),
            'minecraft:cracked_polished_blackstone_bricks': (ids.block_minecraft__cracked_polished_blackstone_bricks, 0),
            'minecraft:cracked_stone_bricks': (ids.block_minecraft__cracked_stone_bricks, 0),
            'minecraft:crafting_table': (ids.block_minecraft__crafting_table, 0),
            'minecraft:creeper_head': (ids.block_minecraft__creeper_head, 0),
            'minecraft:creeper_wall_head': (ids.block_minecraft__creeper_wall_head, 0),
            'minecraft:crimson_button': (ids.block_minecraft__crimson_button, 0),
            'minecraft:crimson_door': (ids.block_minecraft__crimson_door, 0),
            'minecraft:crimson_fence': (ids.block_minecraft__crimson_fence, 0),
            'minecraft:crimson_fence_gate': (ids.block_minecraft__crimson_fence_gate, 0),
            'minecraft:crimson_fungus': (ids.block_minecraft__crimson_fungus, 0),
            'minecraft:crimson_hyphae': (ids.block_minecraft__crimson_hyphae, 0),
            'minecraft:crimson_nylium': (ids.block_minecraft__crimson_nylium, 0),
            'minecraft:crimson_planks': (ids.block_minecraft__crimson_planks, 0),
            'minecraft:crimson_pressure_plate': (ids.block_minecraft__crimson_pressure_plate, 0),
            'minecraft:crimson_roots': (ids.block_minecraft__crimson_roots, 0),
            'minecraft:crimson_sign': (ids.block_minecraft__crimson_sign, 0),
            'minecraft:crimson_slab': (ids.block_minecraft__crimson_slab, 0),
            'minecraft:crimson_stairs': (ids.block_minecraft__crimson_stairs, 0),
            'minecraft:crimson_stem': (ids.block_minecraft__crimson_stem, 0),
            'minecraft:crimson_trapdoor': (ids.block_minecraft__crimson_trapdoor, 0),
            'minecraft:crimson_wall_sign': (ids.block_minecraft__crimson_wall_sign, 0),
            'minecraft:crying_obsidian': (ids.block_minecraft__crying_obsidian, 0),
            'minecraft:cut_red_sandstone': (ids.block_minecraft__cut_red_sandstone, 0),
            'minecraft:cut_red_sandstone_slab': (ids.block_minecraft__cut_red_sandstone_slab, 0),
            'minecraft:cut_sandstone': (ids.block_minecraft__cut_sandstone, 0),
            'minecraft:cut_sandstone_slab': (ids.block_minecraft__cut_sandstone_slab, 0),
            'minecraft:cyan_banner': (ids.block_minecraft__cyan_banner, 0),
            'minecraft:cyan_bed': (ids.block_minecraft__cyan_bed, 0),
            'minecraft:cyan_carpet': (ids.block_minecraft__cyan_carpet, 0),
            'minecraft:cyan_concrete': (ids.block_minecraft__cyan_concrete, 0),
            'minecraft:cyan_concrete_powder': (ids.block_minecraft__cyan_concrete_powder, 0),
            'minecraft:cyan_glazed_terracotta': (ids.block_minecraft__cyan_glazed_terracotta, 0),
            'minecraft:cyan_shulker_box': (ids.block_minecraft__cyan_shulker_box, 0),
            'minecraft:cyan_stained_glass': (ids.block_minecraft__cyan_stained_glass, 0),
            'minecraft:cyan_stained_glass_pane': (ids.block_minecraft__cyan_stained_glass_pane, 0),
            'minecraft:cyan_terracotta': (ids.block_minecraft__cyan_terracotta, 0),
            'minecraft:cyan_wall_banner': (ids.block_minecraft__cyan_wall_banner, 0),
            'minecraft:cyan_wool': (ids.block_minecraft__cyan_wool, 0),
            'minecraft:damaged_anvil': (ids.block_minecraft__damaged_anvil, 0),
            'minecraft:dandelion': (ids.block_minecraft__dandelion, 0),
            'minecraft:dark_oak_button': (ids.block_minecraft__dark_oak_button, 0),
            'minecraft:dark_oak_door': (ids.block_minecraft__dark_oak_door, 0),
            'minecraft:dark_oak_fence': (ids.block_minecraft__dark_oak_fence, 0),
            'minecraft:dark_oak_fence_gate': (ids.block_minecraft__dark_oak_fence_gate, 0),
            'minecraft:dark_oak_leaves': (ids.block_minecraft__dark_oak_leaves, 0),
            'minecraft:dark_oak_log': (ids.block_minecraft__dark_oak_log, 0),
            'minecraft:dark_oak_planks': (ids.block_minecraft__dark_oak_planks, 0),
            'minecraft:dark_oak_pressure_plate': (ids.block_minecraft__dark_oak_pressure_plate, 0),
            'minecraft:dark_oak_sapling': (ids.block_minecraft__dark_oak_sapling, 0),
            'minecraft:dark_oak_sign': (ids.block_minecraft__dark_oak_sign, 0),
            'minecraft:dark_oak_slab': (ids.block_minecraft__dark_oak_slab, 0),
            'minecraft:dark_oak_stairs': (ids.block_minecraft__dark_oak_stairs, 0),
            'minecraft:dark_oak_trapdoor': (ids.block_minecraft__dark_oak_trapdoor, 0),
            'minecraft:dark_oak_wall_sign': (ids.block_minecraft__dark_oak_wall_sign, 0),
            'minecraft:dark_oak_wood': (ids.block_minecraft__dark_oak_wood, 0),
            'minecraft:dark_prismarine': (ids.block_minecraft__dark_prismarine, 0),
            'minecraft:dark_prismarine_slab': (ids.block_minecraft__dark_prismarine_slab, 0),
            'minecraft:dark_prismarine_stairs': (ids.block_minecraft__dark_prismarine_stairs, 0),
            'minecraft:daylight_detector': (ids.block_minecraft__daylight_detector, 0),
            'minecraft:dead_brain_coral': (ids.block_minecraft__dead_brain_coral, 0),
            'minecraft:dead_brain_coral_block': (ids.block_minecraft__dead_brain_coral_block, 0),
            'minecraft:dead_brain_coral_fan': (ids.block_minecraft__dead_brain_coral_fan, 0),
            'minecraft:dead_brain_coral_wall_fan': (ids.block_minecraft__dead_brain_coral_wall_fan, 0),
            'minecraft:dead_bubble_coral': (ids.block_minecraft__dead_bubble_coral, 0),
            'minecraft:dead_bubble_coral_block': (ids.block_minecraft__dead_bubble_coral_block, 0),
            'minecraft:dead_bubble_coral_fan': (ids.block_minecraft__dead_bubble_coral_fan, 0),
            'minecraft:dead_bubble_coral_wall_fan': (ids.block_minecraft__dead_bubble_coral_wall_fan, 0),
            'minecraft:dead_bush': (ids.block_minecraft__dead_bush, 0),
            'minecraft:dead_fire_coral': (ids.block_minecraft__dead_fire_coral, 0),
            'minecraft:dead_fire_coral_block': (ids.block_minecraft__dead_fire_coral_block, 0),
            'minecraft:dead_fire_coral_fan': (ids.block_minecraft__dead_fire_coral_fan, 0),
            'minecraft:dead_fire_coral_wall_fan': (ids.block_minecraft__dead_fire_coral_wall_fan, 0),
            'minecraft:dead_horn_coral': (ids.block_minecraft__dead_horn_coral, 0),
            'minecraft:dead_horn_coral_block': (ids.block_minecraft__dead_horn_coral_block, 0),
            'minecraft:dead_horn_coral_fan': (ids.block_minecraft__dead_horn_coral_fan, 0),
            'minecraft:dead_horn_coral_wall_fan': (ids.block_minecraft__dead_horn_coral_wall_fan, 0),
            'minecraft:dead_tube_coral': (ids.block_minecraft__dead_tube_coral, 0),
            'minecraft:dead_tube_coral_block': (ids.block_minecraft__dead_tube_coral_block, 0),
            'minecraft:dead_tube_coral_fan': (ids.block_minecraft__dead_tube_coral_fan, 0),
            'minecraft:dead_tube_coral_wall_fan': (ids.block_minecraft__dead_tube_coral_wall_fan, 0),
            'minecraft:detector_rail': (ids.block_minecraft__detector_rail, 0),
            'minecraft:diamond_block': (ids.block_minecraft__diamond_block, 0),
            'minecraft:diamond_ore': (ids.block_minecraft__diamond_ore, 0),
            'minecraft:diorite': (ids.block_minecraft__diorite, 0),
            'minecraft:diorite_slab': (ids.block_minecraft__diorite_slab, 0),
            'minecraft:diorite_stairs': (ids.block_minecraft__diorite_stairs, 0),
            'minecraft:diorite_wall': (ids.block_minecraft__diorite_wall, 0),
            'minecraft:dirt': (ids.block_minecraft__dirt, 0),
            'minecraft:dispenser': (ids.block_minecraft__dispenser, 0),
            'minecraft:dragon_egg': (ids.block_minecraft__dragon_egg, 0),
            'minecraft:dragon_head': (ids.block_minecraft__dragon_head, 0),
            'minecraft:dragon_wall_head': (ids.block_minecraft__dragon_wall_head, 0),
            'minecraft:dried_kelp_block': (ids.block_minecraft__dried_kelp_block, 0),
            'minecraft:dropper': (ids.block_minecraft__dropper, 0),
            'minecraft:emerald_block': (ids.block_minecraft__emerald_block, 0),
            'minecraft:emerald_ore': (ids.block_minecraft__emerald_ore, 0),
            'minecraft:enchanting_table': (ids.block_minecraft__enchanting_table, 0),
            'minecraft:end_gateway': (ids.block_minecraft__end_gateway, 0),
            'minecraft:end_portal': (ids.block_minecraft__end_portal, 0),
            'minecraft:end_portal_frame': (ids.block_minecraft__end_portal_frame, 0),
            'minecraft:end_rod': (ids.block_minecraft__end_rod, 0),
            'minecraft:end_stone': (ids.block_minecraft__end_stone, 0),
            'minecraft:end_stone_brick_slab': (ids.block_minecraft__end_stone_brick_slab, 0),
            'minecraft:end_stone_brick_stairs': (ids.block_minecraft__end_stone_brick_stairs, 0),
            'minecraft:end_stone_brick_wall': (ids.block_minecraft__end_stone_brick_wall, 0),
            'minecraft:end_stone_bricks': (ids.block_minecraft__end_stone_bricks, 0),
            'minecraft:ender_chest': (ids.block_minecraft__ender_chest, 0),
            'minecraft:farmland': (ids.block_minecraft__farmland, 0),
            'minecraft:fern': (ids.block_minecraft__fern, 0),
            'minecraft:fire': (ids.block_minecraft__fire, 0),
            'minecraft:fire_coral': (ids.block_minecraft__water, 0),
            'minecraft:fire_coral_block': (ids.block_minecraft__fire_coral_block, 0),
            'minecraft:fire_coral_fan': (ids.block_minecraft__water, 0),
            'minecraft:fire_coral_wall_fan': (ids.block_minecraft__water, 0),
            'minecraft:fletching_table': (ids.block_minecraft__fletching_table, 0),
            'minecraft:flower_pot': (ids.block_minecraft__flower_pot, 0),
            'minecraft:frosted_ice': (ids.block_minecraft__frosted_ice, 0),
            'minecraft:furnace': (ids.block_minecraft__furnace, 0),
            'minecraft:gilded_blackstone': (ids.block_minecraft__gilded_blackstone, 0),
            'minecraft:glass': (ids.block_minecraft__glass, 0),
            'minecraft:glass_pane': (ids.block_minecraft__glass_pane, 0),
            'minecraft:glowstone': (ids.block_minecraft__glowstone, 0),
            'minecraft:gold_block': (ids.block_minecraft__gold_block, 0),
            'minecraft:gold_ore': (ids.block_minecraft__gold_ore, 0),
            'minecraft:granite': (ids.block_minecraft__granite, 0),
            'minecraft:granite_slab': (ids.block_minecraft__granite_slab, 0),
            'minecraft:granite_stairs': (ids.block_minecraft__granite_stairs, 0),
            'minecraft:granite_wall': (ids.block_minecraft__granite_wall, 0),
            'minecraft:grass': (ids.block_minecraft__grass, 0),
            'minecraft:grass_block': (ids.block_minecraft__grass_block, 0),
            'minecraft:grass_path': (ids.block_minecraft__grass_path, 0),
            'minecraft:gravel': (ids.block_minecraft__gravel, 0),
            'minecraft:gray_banner': (ids.block_minecraft__gray_banner, 0),
            'minecraft:gray_bed': (ids.block_minecraft__gray_bed, 0),
            'minecraft:gray_carpet': (ids.block_minecraft__gray_carpet, 0),
            'minecraft:gray_concrete': (ids.block_minecraft__gray_concrete, 0),
            'minecraft:gray_concrete_powder': (ids.block_minecraft__gray_concrete_powder, 0),
            'minecraft:gray_glazed_terracotta': (ids.block_minecraft__gray_glazed_terracotta, 0),
            'minecraft:gray_shulker_box': (ids.block_minecraft__gray_shulker_box, 0),
            'minecraft:gray_stained_glass': (ids.block_minecraft__gray_stained_glass, 0),
            'minecraft:gray_stained_glass_pane': (ids.block_minecraft__gray_stained_glass_pane, 0),
            'minecraft:gray_terracotta': (ids.block_minecraft__gray_terracotta, 0),
            'minecraft:gray_wall_banner': (ids.block_minecraft__gray_wall_banner, 0),
            'minecraft:gray_wool': (ids.block_minecraft__gray_wool, 0),
            'minecraft:green_banner': (ids.block_minecraft__green_banner, 0),
            'minecraft:green_bed': (ids.block_minecraft__green_bed, 0),
            'minecraft:green_carpet': (ids.block_minecraft__green_carpet, 0),
            'minecraft:green_concrete': (ids.block_minecraft__green_concrete, 0),
            'minecraft:green_concrete_powder': (ids.block_minecraft__green_concrete_powder, 0),
            'minecraft:green_glazed_terracotta': (ids.block_minecraft__green_glazed_terracotta, 0),
            'minecraft:green_shulker_box': (ids.block_minecraft__green_shulker_box, 0),
            'minecraft:green_stained_glass': (ids.block_minecraft__green_stained_glass, 0),
            'minecraft:green_stained_glass_pane': (ids.block_minecraft__green_stained_glass_pane, 0),
            'minecraft:green_terracotta': (ids.block_minecraft__green_terracotta, 0),
            'minecraft:green_wall_banner': (ids.block_minecraft__green_wall_banner, 0),
            'minecraft:green_wool': (ids.block_minecraft__green_wool, 0),
            'minecraft:grindstone': (ids.block_minecraft__grindstone, 0),
            'minecraft:hay_block': (ids.block_minecraft__hay_block, 0),
            'minecraft:heavy_weighted_pressure_plate': (ids.block_minecraft__heavy_weighted_pressure_plate, 0),
            'minecraft:honey_block': (ids.block_minecraft__honey_block, 0),
            'minecraft:honeycomb_block': (ids.block_minecraft__honeycomb_block, 0),
            'minecraft:hopper': (ids.block_minecraft__hopper, 0),
            'minecraft:horn_coral': (ids.block_minecraft__water, 0),
            'minecraft:horn_coral_block': (ids.block_minecraft__horn_coral_block, 0),
            'minecraft:horn_coral_fan': (ids.block_minecraft__water, 0),
            'minecraft:horn_coral_wall_fan': (ids.block_minecraft__water, 0),
            'minecraft:ice': (ids.block_minecraft__ice, 0),
            'minecraft:infested_chiseled_stone_bricks': (ids.block_minecraft__infested_chiseled_stone_bricks, 0),
            'minecraft:infested_cobblestone': (ids.block_minecraft__infested_cobblestone, 0),
            'minecraft:infested_cracked_stone_bricks': (ids.block_minecraft__infested_cracked_stone_bricks, 0),
            'minecraft:infested_mossy_stone_bricks': (ids.block_minecraft__infested_mossy_stone_bricks, 0),
            'minecraft:infested_stone': (ids.block_minecraft__infested_stone, 0),
            'minecraft:infested_stone_bricks': (ids.block_minecraft__infested_stone_bricks, 0),
            'minecraft:iron_bars': (ids.block_minecraft__iron_bars, 0),
            'minecraft:iron_block': (ids.block_minecraft__iron_block, 0),
            'minecraft:iron_door': (ids.block_minecraft__iron_door, 0),
            'minecraft:iron_ore': (ids.block_minecraft__iron_ore, 0),
            'minecraft:iron_trapdoor': (ids.block_minecraft__iron_trapdoor, 0),
            'minecraft:jack_o_lantern': (ids.block_minecraft__jack_o_lantern, 0),
            'minecraft:jigsaw': (ids.block_minecraft__jigsaw, 0),
            'minecraft:jukebox': (ids.block_minecraft__jukebox, 0),
            'minecraft:jungle_button': (ids.block_minecraft__jungle_button, 0),
            'minecraft:jungle_door': (ids.block_minecraft__jungle_door, 0),
            'minecraft:jungle_fence': (ids.block_minecraft__jungle_fence, 0),
            'minecraft:jungle_fence_gate': (ids.block_minecraft__jungle_fence_gate, 0),
            'minecraft:jungle_leaves': (ids.block_minecraft__jungle_leaves, 0),
            'minecraft:jungle_log': (ids.block_minecraft__jungle_log, 0),
            'minecraft:jungle_planks': (ids.block_minecraft__jungle_planks, 0),
            'minecraft:jungle_pressure_plate': (ids.block_minecraft__jungle_pressure_plate, 0),
            'minecraft:jungle_sapling': (ids.block_minecraft__jungle_sapling, 0),
            'minecraft:jungle_sign': (ids.block_minecraft__jungle_sign, 0),
            'minecraft:jungle_slab': (ids.block_minecraft__jungle_slab, 0),
            'minecraft:jungle_stairs': (ids.block_minecraft__jungle_stairs, 0),
            'minecraft:jungle_trapdoor': (ids.block_minecraft__jungle_trapdoor, 0),
            'minecraft:jungle_wall_sign': (ids.block_minecraft__jungle_wall_sign, 0),
            'minecraft:jungle_wood': (ids.block_minecraft__jungle_wood, 0),
            'minecraft:kelp': (ids.block_minecraft__water, 0),
            'minecraft:kelp_plant': (ids.block_minecraft__water, 0),
            'minecraft:ladder': (ids.block_minecraft__ladder, 0),
            'minecraft:lantern': (ids.block_minecraft__lantern, 0),
            'minecraft:lapis_block': (ids.block_minecraft__lapis_block, 0),
            'minecraft:lapis_ore': (ids.block_minecraft__lapis_ore, 0),
            'minecraft:large_fern': (ids.block_minecraft__large_fern, 0),
            'minecraft:lava': (ids.block_minecraft__lava, 0),
            'minecraft:lectern': (ids.block_minecraft__lectern, 0),
            'minecraft:lever': (ids.block_minecraft__lever, 0),
            'minecraft:light_blue_banner': (ids.block_minecraft__light_blue_banner, 0),
            'minecraft:light_blue_bed': (ids.block_minecraft__light_blue_bed, 0),
            'minecraft:light_blue_carpet': (ids.block_minecraft__light_blue_carpet, 0),
            'minecraft:light_blue_concrete': (ids.block_minecraft__light_blue_concrete, 0),
            'minecraft:light_blue_concrete_powder': (ids.block_minecraft__light_blue_concrete_powder, 0),
            'minecraft:light_blue_glazed_terracotta': (ids.block_minecraft__light_blue_glazed_terracotta, 0),
            'minecraft:light_blue_shulker_box': (ids.block_minecraft__light_blue_shulker_box, 0),
            'minecraft:light_blue_stained_glass': (ids.block_minecraft__light_blue_stained_glass, 0),
            'minecraft:light_blue_stained_glass_pane': (ids.block_minecraft__light_blue_stained_glass_pane, 0),
            'minecraft:light_blue_terracotta': (ids.block_minecraft__light_blue_terracotta, 0),
            'minecraft:light_blue_wall_banner': (ids.block_minecraft__light_blue_wall_banner, 0),
            'minecraft:light_blue_wool': (ids.block_minecraft__light_blue_wool, 0),
            'minecraft:light_gray_banner': (ids.block_minecraft__light_gray_banner, 0),
            'minecraft:light_gray_bed': (ids.block_minecraft__light_gray_bed, 0),
            'minecraft:light_gray_carpet': (ids.block_minecraft__light_gray_carpet, 0),
            'minecraft:light_gray_concrete': (ids.block_minecraft__light_gray_concrete, 0),
            'minecraft:light_gray_concrete_powder': (ids.block_minecraft__light_gray_concrete_powder, 0),
            'minecraft:light_gray_glazed_terracotta': (ids.block_minecraft__light_gray_glazed_terracotta, 0),
            'minecraft:light_gray_shulker_box': (ids.block_minecraft__light_gray_shulker_box, 0),
            'minecraft:light_gray_stained_glass': (ids.block_minecraft__light_gray_stained_glass, 0),
            'minecraft:light_gray_stained_glass_pane': (ids.block_minecraft__light_gray_stained_glass_pane, 0),
            'minecraft:light_gray_terracotta': (ids.block_minecraft__light_gray_terracotta, 0),
            'minecraft:light_gray_wall_banner': (ids.block_minecraft__light_gray_wall_banner, 0),
            'minecraft:light_gray_wool': (ids.block_minecraft__light_gray_wool, 0),
            'minecraft:light_weighted_pressure_plate': (ids.block_minecraft__light_weighted_pressure_plate, 0),
            'minecraft:lilac': (ids.block_minecraft__lilac, 0),
            'minecraft:lily_of_the_valley': (ids.block_minecraft__lily_of_the_valley, 0),
            'minecraft:lily_pad': (ids.block_minecraft__lily_pad, 0),
            'minecraft:lime_banner': (ids.block_minecraft__lime_banner, 0),
            'minecraft:lime_bed': (ids.block_minecraft__lime_bed, 0),
            'minecraft:lime_carpet': (ids.block_minecraft__lime_carpet, 0),
            'minecraft:lime_concrete': (ids.block_minecraft__lime_concrete, 0),
            'minecraft:lime_concrete_powder': (ids.block_minecraft__lime_concrete_powder, 0),
            'minecraft:lime_glazed_terracotta': (ids.block_minecraft__lime_glazed_terracotta, 0),
            'minecraft:lime_shulker_box': (ids.block_minecraft__lime_shulker_box, 0),
            'minecraft:lime_stained_glass': (ids.block_minecraft__lime_stained_glass, 0),
            'minecraft:lime_stained_glass_pane': (ids.block_minecraft__lime_stained_glass_pane, 0),
            'minecraft:lime_terracotta': (ids.block_minecraft__lime_terracotta, 0),
            'minecraft:lime_wall_banner': (ids.block_minecraft__lime_wall_banner, 0),
            'minecraft:lime_wool': (ids.block_minecraft__lime_wool, 0),
            'minecraft:lodestone': (ids.block_minecraft__lodestone, 0),
            'minecraft:loom': (ids.block_minecraft__loom, 0),
            'minecraft:magenta_banner': (ids.block_minecraft__magenta_banner, 0),
            'minecraft:magenta_bed': (ids.block_minecraft__magenta_bed, 0),
            'minecraft:magenta_carpet': (ids.block_minecraft__magenta_carpet, 0),
            'minecraft:magenta_concrete': (ids.block_minecraft__magenta_concrete, 0),
            'minecraft:magenta_concrete_powder': (ids.block_minecraft__magenta_concrete_powder, 0),
            'minecraft:magenta_glazed_terracotta': (ids.block_minecraft__magenta_glazed_terracotta, 0),
            'minecraft:magenta_shulker_box': (ids.block_minecraft__magenta_shulker_box, 0),
            'minecraft:magenta_stained_glass': (ids.block_minecraft__magenta_stained_glass, 0),
            'minecraft:magenta_stained_glass_pane': (ids.block_minecraft__magenta_stained_glass_pane, 0),
            'minecraft:magenta_terracotta': (ids.block_minecraft__magenta_terracotta, 0),
            'minecraft:magenta_wall_banner': (ids.block_minecraft__magenta_wall_banner, 0),
            'minecraft:magenta_wool': (ids.block_minecraft__magenta_wool, 0),
            'minecraft:magma_block': (ids.block_minecraft__magma_block, 0),
            'minecraft:melon': (ids.block_minecraft__melon, 0),
            'minecraft:melon_stem': (ids.block_minecraft__melon_stem, 0),
            'minecraft:mossy_cobblestone': (ids.block_minecraft__mossy_cobblestone, 0),
            'minecraft:mossy_cobblestone_slab': (ids.block_minecraft__mossy_cobblestone_slab, 0),
            'minecraft:mossy_cobblestone_stairs': (ids.block_minecraft__mossy_cobblestone_stairs, 0),
            'minecraft:mossy_cobblestone_wall': (ids.block_minecraft__mossy_cobblestone_wall, 0),
            'minecraft:mossy_stone_brick_slab': (ids.block_minecraft__mossy_stone_brick_slab, 0),
            'minecraft:mossy_stone_brick_stairs': (ids.block_minecraft__mossy_stone_brick_stairs, 0),
            'minecraft:mossy_stone_brick_wall': (ids.block_minecraft__mossy_stone_brick_wall, 0),
            'minecraft:mossy_stone_bricks': (ids.block_minecraft__mossy_stone_bricks, 0),
            'minecraft:moving_piston': (ids.block_minecraft__moving_piston, 0),
            'minecraft:mushroom_stem': (ids.block_minecraft__mushroom_stem, 0),
            'minecraft:mycelium': (ids.block_minecraft__mycelium, 0),
            'minecraft:nether_brick_fence': (ids.block_minecraft__nether_brick_fence, 0),
            'minecraft:nether_brick_slab': (ids.block_minecraft__nether_brick_slab, 0),
            'minecraft:nether_brick_stairs': (ids.block_minecraft__nether_brick_stairs, 0),
            'minecraft:nether_brick_wall': (ids.block_minecraft__nether_brick_wall, 0),
            'minecraft:nether_bricks': (ids.block_minecraft__nether_bricks, 0),
            'minecraft:nether_gold_ore': (ids.block_minecraft__nether_gold_ore, 0),
            'minecraft:nether_portal': (ids.block_minecraft__nether_portal, 0),
            'minecraft:nether_quartz_ore': (ids.block_minecraft__nether_quartz_ore, 0),
            'minecraft:nether_sprouts': (ids.block_minecraft__nether_sprouts, 0),
            'minecraft:nether_wart': (ids.block_minecraft__nether_wart, 0),
            'minecraft:nether_wart_block': (ids.block_minecraft__nether_wart_block, 0),
            'minecraft:netherite_block': (ids.block_minecraft__netherite_block, 0),
            'minecraft:netherrack': (ids.block_minecraft__netherrack, 0),
            'minecraft:note_block': (ids.block_minecraft__note_block, 0),
            'minecraft:oak_button': (ids.block_minecraft__oak_button, 0),
            'minecraft:oak_door': (ids.block_minecraft__oak_door, 0),
            'minecraft:oak_fence': (ids.block_minecraft__oak_fence, 0),
            'minecraft:oak_fence_gate': (ids.block_minecraft__oak_fence_gate, 0),
            'minecraft:oak_leaves': (ids.block_minecraft__oak_leaves, 0),
            'minecraft:oak_log': (ids.block_minecraft__oak_log, 0),
            'minecraft:oak_planks': (ids.block_minecraft__oak_planks, 0),
            'minecraft:oak_pressure_plate': (ids.block_minecraft__oak_pressure_plate, 0),
            'minecraft:oak_sapling': (ids.block_minecraft__oak_sapling, 0),
            'minecraft:oak_sign': (ids.block_minecraft__oak_sign, 0),
            'minecraft:oak_slab': (ids.block_minecraft__oak_slab, 0),
            'minecraft:oak_stairs': (ids.block_minecraft__oak_stairs, 0),
            'minecraft:oak_trapdoor': (ids.block_minecraft__oak_trapdoor, 0),
            'minecraft:oak_wall_sign': (ids.block_minecraft__oak_wall_sign, 0),
            'minecraft:oak_wood': (ids.block_minecraft__oak_wood, 0),
            'minecraft:observer': (ids.block_minecraft__observer, 0),
            'minecraft:obsidian': (ids.block_minecraft__obsidian, 0),
            'minecraft:orange_banner': (ids.block_minecraft__orange_banner, 0),
            'minecraft:orange_bed': (ids.block_minecraft__orange_bed, 0),
            'minecraft:orange_carpet': (ids.block_minecraft__orange_carpet, 0),
            'minecraft:orange_concrete': (ids.block_minecraft__orange_concrete, 0),
            'minecraft:orange_concrete_powder': (ids.block_minecraft__orange_concrete_powder, 0),
            'minecraft:orange_glazed_terracotta': (ids.block_minecraft__orange_glazed_terracotta, 0),
            'minecraft:orange_shulker_box': (ids.block_minecraft__orange_shulker_box, 0),
            'minecraft:orange_stained_glass': (ids.block_minecraft__orange_stained_glass, 0),
            'minecraft:orange_stained_glass_pane': (ids.block_minecraft__orange_stained_glass_pane, 0),
            'minecraft:orange_terracotta': (ids.block_minecraft__orange_terracotta, 0),
            'minecraft:orange_tulip': (ids.block_minecraft__orange_tulip, 0),
            'minecraft:orange_wall_banner': (ids.block_minecraft__orange_wall_banner, 0),
            'minecraft:orange_wool': (ids.block_minecraft__orange_wool, 0),
            'minecraft:oxeye_daisy': (ids.block_minecraft__oxeye_daisy, 0),
            'minecraft:packed_ice': (ids.block_minecraft__packed_ice, 0),
            'minecraft:peony': (ids.block_minecraft__peony, 0),
            'minecraft:petrified_oak_slab': (ids.block_minecraft__petrified_oak_slab, 0),
            'minecraft:pink_banner': (ids.block_minecraft__pink_banner, 0),
            'minecraft:pink_bed': (ids.block_minecraft__pink_bed, 0),
            'minecraft:pink_carpet': (ids.block_minecraft__pink_carpet, 0),
            'minecraft:pink_concrete': (ids.block_minecraft__pink_concrete, 0),
            'minecraft:pink_concrete_powder': (ids.block_minecraft__pink_concrete_powder, 0),
            'minecraft:pink_glazed_terracotta': (ids.block_minecraft__pink_glazed_terracotta, 0),
            'minecraft:pink_shulker_box': (ids.block_minecraft__pink_shulker_box, 0),
            'minecraft:pink_stained_glass': (ids.block_minecraft__pink_stained_glass, 0),
            'minecraft:pink_stained_glass_pane': (ids.block_minecraft__pink_stained_glass_pane, 0),
            'minecraft:pink_terracotta': (ids.block_minecraft__pink_terracotta, 0),
            'minecraft:pink_tulip': (ids.block_minecraft__pink_tulip, 0),
            'minecraft:pink_wall_banner': (ids.block_minecraft__pink_wall_banner, 0),
            'minecraft:pink_wool': (ids.block_minecraft__pink_wool, 0),
            'minecraft:piston': (ids.block_minecraft__piston, 0),
            'minecraft:piston_head': (ids.block_minecraft__piston_head, 0),
            'minecraft:player_head': (ids.block_minecraft__player_head, 0),
            'minecraft:player_wall_head': (ids.block_minecraft__player_wall_head, 0),
            'minecraft:podzol': (ids.block_minecraft__podzol, 0),
            'minecraft:polished_andesite': (ids.block_minecraft__polished_andesite, 0),
            'minecraft:polished_andesite_slab': (ids.block_minecraft__polished_andesite_slab, 0),
            'minecraft:polished_andesite_stairs': (ids.block_minecraft__polished_andesite_stairs, 0),
            'minecraft:polished_basalt': (ids.block_minecraft__polished_basalt, 0),
            'minecraft:polished_blackstone': (ids.block_minecraft__polished_blackstone, 0),
            'minecraft:polished_blackstone_brick_slab': (ids.block_minecraft__polished_blackstone_brick_slab, 0),
            'minecraft:polished_blackstone_brick_stairs': (ids.block_minecraft__polished_blackstone_brick_stairs, 0),
            'minecraft:polished_blackstone_brick_wall': (ids.block_minecraft__polished_blackstone_brick_wall, 0),
            'minecraft:polished_blackstone_bricks': (ids.block_minecraft__polished_blackstone_bricks, 0),
            'minecraft:polished_blackstone_button': (ids.block_minecraft__polished_blackstone_button, 0),
            'minecraft:polished_blackstone_pressure_plate': (ids.block_minecraft__polished_blackstone_pressure_plate, 0),
            'minecraft:polished_blackstone_slab': (ids.block_minecraft__polished_blackstone_slab, 0),
            'minecraft:polished_blackstone_stairs': (ids.block_minecraft__polished_blackstone_stairs, 0),
            'minecraft:polished_blackstone_wall': (ids.block_minecraft__polished_blackstone_wall, 0),
            'minecraft:polished_diorite': (ids.block_minecraft__polished_diorite, 0),
            'minecraft:polished_diorite_slab': (ids.block_minecraft__polished_diorite_slab, 0),
            'minecraft:polished_diorite_stairs': (ids.block_minecraft__polished_diorite_stairs, 0),
            'minecraft:polished_granite': (ids.block_minecraft__polished_granite, 0),
            'minecraft:polished_granite_slab': (ids.block_minecraft__polished_granite_slab, 0),
            'minecraft:polished_granite_stairs': (ids.block_minecraft__polished_granite_stairs, 0),
            'minecraft:poppy': (ids.block_minecraft__poppy, 0),
            'minecraft:potatoes': (ids.block_minecraft__potatoes, 0),
            'minecraft:potted_acacia_sapling': (ids.block_minecraft__potted_acacia_sapling, 0),
            'minecraft:potted_allium': (ids.block_minecraft__potted_allium, 0),
            'minecraft:potted_azure_bluet': (ids.block_minecraft__potted_azure_bluet, 0),
            'minecraft:potted_bamboo': (ids.block_minecraft__potted_bamboo, 0),
            'minecraft:potted_birch_sapling': (ids.block_minecraft__potted_birch_sapling, 0),
            'minecraft:potted_blue_orchid': (ids.block_minecraft__potted_blue_orchid, 0),
            'minecraft:potted_brown_mushroom': (ids.block_minecraft__potted_brown_mushroom, 0),
            'minecraft:potted_cactus': (ids.block_minecraft__potted_cactus, 0),
            'minecraft:potted_cornflower': (ids.block_minecraft__potted_cornflower, 0),
            'minecraft:potted_crimson_fungus': (ids.block_minecraft__potted_crimson_fungus, 0),
            'minecraft:potted_crimson_roots': (ids.block_minecraft__potted_crimson_roots, 0),
            'minecraft:potted_dandelion': (ids.block_minecraft__potted_dandelion, 0),
            'minecraft:potted_dark_oak_sapling': (ids.block_minecraft__potted_dark_oak_sapling, 0),
            'minecraft:potted_dead_bush': (ids.block_minecraft__potted_dead_bush, 0),
            'minecraft:potted_fern': (ids.block_minecraft__potted_fern, 0),
            'minecraft:potted_jungle_sapling': (ids.block_minecraft__potted_jungle_sapling, 0),
            'minecraft:potted_lily_of_the_valley': (ids.block_minecraft__potted_lily_of_the_valley, 0),
            'minecraft:potted_oak_sapling': (ids.block_minecraft__potted_oak_sapling, 0),
            'minecraft:potted_orange_tulip': (ids.block_minecraft__potted_orange_tulip, 0),
            'minecraft:potted_oxeye_daisy': (ids.block_minecraft__potted_oxeye_daisy, 0),
            'minecraft:potted_pink_tulip': (ids.block_minecraft__potted_pink_tulip, 0),
            'minecraft:potted_poppy': (ids.block_minecraft__potted_poppy, 0),
            'minecraft:potted_red_mushroom': (ids.block_minecraft__potted_red_mushroom, 0),
            'minecraft:potted_red_tulip': (ids.block_minecraft__potted_red_tulip, 0),
            'minecraft:potted_spruce_sapling': (ids.block_minecraft__potted_spruce_sapling, 0),
            'minecraft:potted_warped_fungus': (ids.block_minecraft__potted_warped_fungus, 0),
            'minecraft:potted_warped_roots': (ids.block_minecraft__potted_warped_roots, 0),
            'minecraft:potted_white_tulip': (ids.block_minecraft__potted_white_tulip, 0),
            'minecraft:potted_wither_rose': (ids.block_minecraft__potted_wither_rose, 0),
            'minecraft:powered_rail': (ids.block_minecraft__powered_rail, 0),
            'minecraft:prismarine': (ids.block_minecraft__prismarine, 0),
            'minecraft:prismarine_brick_slab': (ids.block_minecraft__prismarine_brick_slab, 0),
            'minecraft:prismarine_brick_stairs': (ids.block_minecraft__prismarine_brick_stairs, 0),
            'minecraft:prismarine_bricks': (ids.block_minecraft__prismarine_bricks, 0),
            'minecraft:prismarine_slab': (ids.block_minecraft__prismarine_slab, 0),
            'minecraft:prismarine_stairs': (ids.block_minecraft__prismarine_stairs, 0),
            'minecraft:prismarine_wall': (ids.block_minecraft__prismarine_wall, 0),
            'minecraft:pumpkin': (ids.block_minecraft__pumpkin, 0),
            'minecraft:pumpkin_stem': (ids.block_minecraft__pumpkin_stem, 0),
            'minecraft:purple_banner': (ids.block_minecraft__purple_banner, 0),
            'minecraft:purple_bed': (ids.block_minecraft__purple_bed, 0),
            'minecraft:purple_carpet': (ids.block_minecraft__purple_carpet, 0),
            'minecraft:purple_concrete': (ids.block_minecraft__purple_concrete, 0),
            'minecraft:purple_concrete_powder': (ids.block_minecraft__purple_concrete_powder, 0),
            'minecraft:purple_glazed_terracotta': (ids.block_minecraft__purple_glazed_terracotta, 0),
            'minecraft:purple_shulker_box': (ids.block_minecraft__purple_shulker_box, 0),
            'minecraft:purple_stained_glass': (ids.block_minecraft__purple_stained_glass, 0),
            'minecraft:purple_stained_glass_pane': (ids.block_minecraft__purple_stained_glass_pane, 0),
            'minecraft:purple_terracotta': (ids.block_minecraft__purple_terracotta, 0),
            'minecraft:purple_wall_banner': (ids.block_minecraft__purple_wall_banner, 0),
            'minecraft:purple_wool': (ids.block_minecraft__purple_wool, 0),
            'minecraft:purpur_block': (ids.block_minecraft__purpur_block, 0),
            'minecraft:purpur_pillar': (ids.block_minecraft__purpur_pillar, 0),
            'minecraft:purpur_slab': (ids.block_minecraft__purpur_slab, 0),
            'minecraft:purpur_stairs': (ids.block_minecraft__purpur_stairs, 0),
            'minecraft:quartz_block': (ids.block_minecraft__quartz_block, 0),
            'minecraft:quartz_bricks': (ids.block_minecraft__quartz_bricks, 0),
            'minecraft:quartz_pillar': (ids.block_minecraft__quartz_pillar, 0),
            'minecraft:quartz_slab': (ids.block_minecraft__quartz_slab, 0),
            'minecraft:quartz_stairs': (ids.block_minecraft__quartz_stairs, 0),
            'minecraft:rail': (ids.block_minecraft__rail, 0),
            'minecraft:red_banner': (ids.block_minecraft__red_banner, 0),
            'minecraft:red_bed': (ids.block_minecraft__red_bed, 0),
            'minecraft:red_carpet': (ids.block_minecraft__red_carpet, 0),
            'minecraft:red_concrete': (ids.block_minecraft__red_concrete, 0),
            'minecraft:red_concrete_powder': (ids.block_minecraft__red_concrete_powder, 0),
            'minecraft:red_glazed_terracotta': (ids.block_minecraft__red_glazed_terracotta, 0),
            'minecraft:red_mushroom': (ids.block_minecraft__red_mushroom, 0),
            'minecraft:red_mushroom_block': (ids.block_minecraft__red_mushroom_block, 0),
            'minecraft:red_nether_brick_slab': (ids.block_minecraft__red_nether_brick_slab, 0),
            'minecraft:red_nether_brick_stairs': (ids.block_minecraft__red_nether_brick_stairs, 0),
            'minecraft:red_nether_brick_wall': (ids.block_minecraft__red_nether_brick_wall, 0),
            'minecraft:red_nether_bricks': (ids.block_minecraft__red_nether_bricks, 0),
            'minecraft:red_sand': (ids.block_minecraft__red_sand, 0),
            'minecraft:red_sandstone': (ids.block_minecraft__red_sandstone, 0),
            'minecraft:red_sandstone_slab': (ids.block_minecraft__red_sandstone_slab, 0),
            'minecraft:red_sandstone_stairs': (ids.block_minecraft__red_sandstone_stairs, 0),
            'minecraft:red_sandstone_wall': (ids.block_minecraft__red_sandstone_wall, 0),
            'minecraft:red_shulker_box': (ids.block_minecraft__red_shulker_box, 0),
            'minecraft:red_stained_glass': (ids.block_minecraft__red_stained_glass, 0),
            'minecraft:red_stained_glass_pane': (ids.block_minecraft__red_stained_glass_pane, 0),
            'minecraft:red_terracotta': (ids.block_minecraft__red_terracotta, 0),
            'minecraft:red_tulip': (ids.block_minecraft__red_tulip, 0),
            'minecraft:red_wall_banner': (ids.block_minecraft__red_wall_banner, 0),
            'minecraft:red_wool': (ids.block_minecraft__red_wool, 0),
            'minecraft:redstone_block': (ids.block_minecraft__redstone_block, 0),
            'minecraft:redstone_lamp': (ids.block_minecraft__redstone_lamp, 0),
            'minecraft:redstone_ore': (ids.block_minecraft__redstone_ore, 0),
            'minecraft:redstone_torch': (ids.block_minecraft__redstone_torch, 0),
            'minecraft:redstone_wall_torch': (ids.block_minecraft__redstone_wall_torch, 0),
            'minecraft:redstone_wire': (ids.block_minecraft__redstone_wire, 0),
            'minecraft:repeater': (ids.block_minecraft__repeater, 0),
            'minecraft:repeating_command_block': (ids.block_minecraft__repeating_command_block, 0),
            'minecraft:respawn_anchor': (ids.block_minecraft__respawn_anchor, 0),
            'minecraft:rose_bush': (ids.block_minecraft__rose_bush, 0),
            'minecraft:sand': (ids.block_minecraft__sand, 0),
            'minecraft:sandstone': (ids.block_minecraft__sandstone, 0),
            'minecraft:sandstone_slab': (ids.block_minecraft__sandstone_slab, 0),
            'minecraft:sandstone_stairs': (ids.block_minecraft__sandstone_stairs, 0),
            'minecraft:sandstone_wall': (ids.block_minecraft__sandstone_wall, 0),
            'minecraft:scaffolding': (ids.block_minecraft__scaffolding, 0),
            'minecraft:sea_lantern': (ids.block_minecraft__sea_lantern, 0),
            'minecraft:sea_pickle': (ids.block_minecraft__sea_pickle, 0),
            'minecraft:seagrass': (ids.block_minecraft__seagrass, 0),
            'minecraft:shroomlight': (ids.block_minecraft__shroomlight, 0),
            'minecraft:shulker_box': (ids.block_minecraft__shulker_box, 0),
            'minecraft:skeleton_skull': (ids.block_minecraft__skeleton_skull, 0),
            'minecraft:skeleton_wall_skull': (ids.block_minecraft__skeleton_wall_skull, 0),
            'minecraft:slime_block': (ids.block_minecraft__slime_block, 0),
            'minecraft:smithing_table': (ids.block_minecraft__smithing_table, 0),
            'minecraft:smoker': (ids.block_minecraft__smoker, 0),
            'minecraft:smooth_quartz': (ids.block_minecraft__smooth_quartz, 0),
            'minecraft:smooth_quartz_slab': (ids.block_minecraft__smooth_quartz_slab, 0),
            'minecraft:smooth_quartz_stairs': (ids.block_minecraft__smooth_quartz_stairs, 0),
            'minecraft:smooth_red_sandstone': (ids.block_minecraft__smooth_red_sandstone, 0),
            'minecraft:smooth_red_sandstone_slab': (ids.block_minecraft__smooth_red_sandstone_slab, 0),
            'minecraft:smooth_red_sandstone_stairs': (ids.block_minecraft__smooth_red_sandstone_stairs, 0),
            'minecraft:smooth_sandstone': (ids.block_minecraft__smooth_sandstone, 0),
            'minecraft:smooth_sandstone_slab': (ids.block_minecraft__smooth_sandstone_slab, 0),
            'minecraft:smooth_sandstone_stairs': (ids.block_minecraft__smooth_sandstone_stairs, 0),
            'minecraft:smooth_stone': (ids.block_minecraft__smooth_stone, 0),
            'minecraft:smooth_stone_slab': (ids.block_minecraft__smooth_stone_slab, 0),
            'minecraft:snow': (ids.block_minecraft__snow, 0),
            'minecraft:snow_block': (ids.block_minecraft__snow_block, 0),
            'minecraft:soul_campfire': (ids.block_minecraft__soul_campfire, 0),
            'minecraft:soul_fire': (ids.block_minecraft__soul_fire, 0),
            'minecraft:soul_lantern': (ids.block_minecraft__soul_lantern, 0),
            'minecraft:soul_sand': (ids.block_minecraft__soul_sand, 0),
            'minecraft:soul_soil': (ids.block_minecraft__soul_soil, 0),
            'minecraft:soul_torch': (ids.block_minecraft__soul_torch, 0),
            'minecraft:soul_wall_torch': (ids.block_minecraft__soul_wall_torch, 0),
            'minecraft:spawner': (ids.block_minecraft__spawner, 0),
            'minecraft:sponge': (ids.block_minecraft__sponge, 0),
            'minecraft:spruce_button': (ids.block_minecraft__spruce_button, 0),
            'minecraft:spruce_door': (ids.block_minecraft__spruce_door, 0),
            'minecraft:spruce_fence': (ids.block_minecraft__spruce_fence, 0),
            'minecraft:spruce_fence_gate': (ids.block_minecraft__spruce_fence_gate, 0),
            'minecraft:spruce_leaves': (ids.block_minecraft__spruce_leaves, 0),
            'minecraft:spruce_log': (ids.block_minecraft__spruce_log, 0),
            'minecraft:spruce_planks': (ids.block_minecraft__spruce_planks, 0),
            'minecraft:spruce_pressure_plate': (ids.block_minecraft__spruce_pressure_plate, 0),
            'minecraft:spruce_sapling': (ids.block_minecraft__spruce_sapling, 0),
            'minecraft:spruce_sign': (ids.block_minecraft__spruce_sign, 0),
            'minecraft:spruce_slab': (ids.block_minecraft__spruce_slab, 0),
            'minecraft:spruce_stairs': (ids.block_minecraft__spruce_stairs, 0),
            'minecraft:spruce_trapdoor': (ids.block_minecraft__spruce_trapdoor, 0),
            'minecraft:spruce_wall_sign': (ids.block_minecraft__spruce_wall_sign, 0),
            'minecraft:spruce_wood': (ids.block_minecraft__spruce_wood, 0),
            'minecraft:sticky_piston': (ids.block_minecraft__sticky_piston, 0),
            'minecraft:stone': (ids.block_minecraft__stone, 0),
            'minecraft:stone_brick_slab': (ids.block_minecraft__stone_brick_slab, 0),
            'minecraft:stone_brick_stairs': (ids.block_minecraft__stone_brick_stairs, 0),
            'minecraft:stone_brick_wall': (ids.block_minecraft__stone_brick_wall, 0),
            'minecraft:stone_bricks': (ids.block_minecraft__stone_bricks, 0),
            'minecraft:stone_button': (ids.block_minecraft__stone_button, 0),
            'minecraft:stone_pressure_plate': (ids.block_minecraft__stone_pressure_plate, 0),
            'minecraft:stone_slab': (ids.block_minecraft__stone_slab, 0),
            'minecraft:stone_stairs': (ids.block_minecraft__stone_stairs, 0),
            'minecraft:stonecutter': (ids.block_minecraft__stonecutter, 0),
            'minecraft:stripped_acacia_log': (ids.block_minecraft__stripped_acacia_log, 0),
            'minecraft:stripped_acacia_wood': (ids.block_minecraft__stripped_acacia_wood, 0),
            'minecraft:stripped_birch_log': (ids.block_minecraft__stripped_birch_log, 0),
            'minecraft:stripped_birch_wood': (ids.block_minecraft__stripped_birch_wood, 0),
            'minecraft:stripped_crimson_hyphae': (ids.block_minecraft__stripped_crimson_hyphae, 0),
            'minecraft:stripped_crimson_stem': (ids.block_minecraft__stripped_crimson_stem, 0),
            'minecraft:stripped_dark_oak_log': (ids.block_minecraft__stripped_dark_oak_log, 0),
            'minecraft:stripped_dark_oak_wood': (ids.block_minecraft__stripped_dark_oak_wood, 0),
            'minecraft:stripped_jungle_log': (ids.block_minecraft__stripped_jungle_log, 0),
            'minecraft:stripped_jungle_wood': (ids.block_minecraft__stripped_jungle_wood, 0),
            'minecraft:stripped_oak_log': (ids.block_minecraft__stripped_oak_log, 0),
            'minecraft:stripped_oak_wood': (ids.block_minecraft__stripped_oak_wood, 0),
            'minecraft:stripped_spruce_log': (ids.block_minecraft__stripped_spruce_log, 0),
            'minecraft:stripped_spruce_wood': (ids.block_minecraft__stripped_spruce_wood, 0),
            'minecraft:stripped_warped_hyphae': (ids.block_minecraft__stripped_warped_hyphae, 0),
            'minecraft:stripped_warped_stem': (ids.block_minecraft__stripped_warped_stem, 0),
            'minecraft:structure_block': (ids.block_minecraft__structure_block, 0),
            'minecraft:structure_void': (ids.block_minecraft__structure_void, 0),
            'minecraft:sugar_cane': (ids.block_minecraft__sugar_cane, 0),
            'minecraft:sunflower': (ids.block_minecraft__sunflower, 0),
            'minecraft:sweet_berry_bush': (ids.block_minecraft__sweet_berry_bush, 0),
            'minecraft:tall_grass': (ids.block_minecraft__tall_grass, 0),
            'minecraft:tall_seagrass': (ids.block_minecraft__tall_seagrass, 0),
            'minecraft:target': (ids.block_minecraft__target, 0),
            'minecraft:terracotta': (ids.block_minecraft__terracotta, 0),
            'minecraft:tnt': (ids.block_minecraft__tnt, 0),
            'minecraft:torch': (ids.block_minecraft__torch, 0),
            'minecraft:trapped_chest': (ids.block_minecraft__trapped_chest, 0),
            'minecraft:tripwire': (ids.block_minecraft__tripwire, 0),
            'minecraft:tripwire_hook': (ids.block_minecraft__tripwire_hook, 0),
            'minecraft:tube_coral': (ids.block_minecraft__water, 0),
            'minecraft:tube_coral_block': (ids.block_minecraft__tube_coral_block, 0),
            'minecraft:tube_coral_fan': (ids.block_minecraft__water, 0),
            'minecraft:tube_coral_wall_fan': (ids.block_minecraft__water, 0),
            'minecraft:turtle_egg': (ids.block_minecraft__turtle_egg, 0),
            'minecraft:twisting_vines': (ids.block_minecraft__twisting_vines, 0),
            'minecraft:twisting_vines_plant': (ids.block_minecraft__twisting_vines_plant, 0),
            'minecraft:vine': (ids.block_minecraft__vine, 0),
            'minecraft:void_air': (ids.block_minecraft__cave_air, 0),
            'minecraft:wall_torch': (ids.block_minecraft__wall_torch, 0),
            'minecraft:warped_button': (ids.block_minecraft__warped_button, 0),
            'minecraft:warped_door': (ids.block_minecraft__warped_door, 0),
            'minecraft:warped_fence': (ids.block_minecraft__warped_fence, 0),
            'minecraft:warped_fence_gate': (ids.block_minecraft__warped_fence_gate, 0),
            'minecraft:warped_fungus': (ids.block_minecraft__warped_fungus, 0),
            'minecraft:warped_hyphae': (ids.block_minecraft__warped_hyphae, 0),
            'minecraft:warped_nylium': (ids.block_minecraft__warped_nylium, 0),
            'minecraft:warped_planks': (ids.block_minecraft__warped_planks, 0),
            'minecraft:warped_pressure_plate': (ids.block_minecraft__warped_pressure_plate, 0),
            'minecraft:warped_roots': (ids.block_minecraft__warped_roots, 0),
            'minecraft:warped_sign': (ids.block_minecraft__warped_sign, 0),
            'minecraft:warped_slab': (ids.block_minecraft__warped_slab, 0),
            'minecraft:warped_stairs': (ids.block_minecraft__warped_stairs, 0),
            'minecraft:warped_stem': (ids.block_minecraft__warped_stem, 0),
            'minecraft:warped_trapdoor': (ids.block_minecraft__warped_trapdoor, 0),
            'minecraft:warped_wall_sign': (ids.block_minecraft__warped_wall_sign, 0),
            'minecraft:warped_wart_block': (ids.block_minecraft__warped_wart_block, 0),
            'minecraft:water': (ids.block_minecraft__water, 0),
            'minecraft:weeping_vines': (ids.block_minecraft__weeping_vines, 0),
            'minecraft:weeping_vines_plant': (ids.block_minecraft__weeping_vines_plant, 0),
            'minecraft:wet_sponge': (ids.block_minecraft__wet_sponge, 0),
            'minecraft:wheat': (ids.block_minecraft__wheat, 0),
            'minecraft:white_banner': (ids.block_minecraft__white_banner, 0),
            'minecraft:white_bed': (ids.block_minecraft__white_bed, 0),
            'minecraft:white_carpet': (ids.block_minecraft__white_carpet, 0),
            'minecraft:white_concrete': (ids.block_minecraft__white_concrete, 0),
            'minecraft:white_concrete_powder': (ids.block_minecraft__white_concrete_powder, 0),
            'minecraft:white_glazed_terracotta': (ids.block_minecraft__white_glazed_terracotta, 0),
            'minecraft:white_shulker_box': (ids.block_minecraft__white_shulker_box, 0),
            'minecraft:white_stained_glass': (ids.block_minecraft__white_stained_glass, 0),
            'minecraft:white_stained_glass_pane': (ids.block_minecraft__white_stained_glass_pane, 0),
            'minecraft:white_terracotta': (ids.block_minecraft__white_terracotta, 0),
            'minecraft:white_tulip': (ids.block_minecraft__white_tulip, 0),
            'minecraft:white_wall_banner': (ids.block_minecraft__white_wall_banner, 0),
            'minecraft:white_wool': (ids.block_minecraft__white_wool, 0),
            'minecraft:wither_rose': (ids.block_minecraft__wither_rose, 0),
            'minecraft:wither_skeleton_skull': (ids.block_minecraft__wither_skeleton_skull, 0),
            'minecraft:wither_skeleton_wall_skull': (ids.block_minecraft__wither_skeleton_wall_skull, 0),
            'minecraft:yellow_banner': (ids.block_minecraft__yellow_banner, 0),
            'minecraft:yellow_bed': (ids.block_minecraft__yellow_bed, 0),
            'minecraft:yellow_carpet': (ids.block_minecraft__yellow_carpet, 0),
            'minecraft:yellow_concrete': (ids.block_minecraft__yellow_concrete, 0),
            'minecraft:yellow_concrete_powder': (ids.block_minecraft__yellow_concrete_powder, 0),
            'minecraft:yellow_glazed_terracotta': (ids.block_minecraft__yellow_glazed_terracotta, 0),
            'minecraft:yellow_shulker_box': (ids.block_minecraft__yellow_shulker_box, 0),
            'minecraft:yellow_stained_glass': (ids.block_minecraft__yellow_stained_glass, 0),
            'minecraft:yellow_stained_glass_pane': (ids.block_minecraft__yellow_stained_glass_pane, 0),
            'minecraft:yellow_terracotta': (ids.block_minecraft__yellow_terracotta, 0),
            'minecraft:yellow_wall_banner': (ids.block_minecraft__yellow_wall_banner, 0),
            'minecraft:yellow_wool': (ids.block_minecraft__yellow_wool, 0),
            'minecraft:zombie_head': (ids.block_minecraft__zombie_head, 0),
            'minecraft:zombie_wall_head': (ids.block_minecraft__zombie_wall_head, 0),
            ### END FLAG FULLNAME IDS ###

            # 'minecraft:potted_oak_sapling': (ids.block_minecraft__potted_oak_sapling, 0),  # not rendering
            # 'minecraft:potted_spruce_sapling': (ids.block_minecraft__potted_spruce_sapling, 0),  # not rendering
            # 'minecraft:potted_birch_sapling': (ids.block_minecraft__potted_birch_sapling, 0),  # not rendering
            # 'minecraft:potted_jungle_sapling': (ids.block_minecraft__potted_jungle_sapling, 0),  # not rendering
            # 'minecraft:potted_acacia_sapling': (ids.block_minecraft__potted_acacia_sapling, 0),  # not rendering
            # 'minecraft:potted_dandelion': (ids.block_minecraft__potted_dandelion, 0),  # not rendering
            # 'minecraft:potted_fern': (ids.block_minecraft__potted_fern, 0),  # not rendering
            # 'minecraft:potted_poppy': (ids.block_minecraft__potted_poppy, 0),  # not rendering
            # 'minecraft:potted_blue_orchid': (ids.block_minecraft__potted_blue_orchid, 0),  # not rendering
            # 'minecraft:potted_allium': (ids.block_minecraft__potted_allium, 0),  # not rendering
            # 'minecraft:potted_azure_bluet': (ids.block_minecraft__potted_azure_bluet, 0),  # not rendering
            # 'minecraft:potted_red_tulip': (ids.block_minecraft__potted_red_tulip, 0),  # not rendering
            # 'minecraft:potted_orange_tulip': (ids.block_minecraft__potted_orange_tulip, 0),  # not rendering
            # 'minecraft:potted_white_tulip': (ids.block_minecraft__potted_white_tulip, 0),  # not rendering
            # 'minecraft:potted_pink_tulip': (ids.block_minecraft__potted_pink_tulip, 0),  # not rendering
            # 'minecraft:potted_oxeye_daisy': (ids.block_minecraft__potted_oxeye_daisy, 0),  # not rendering
            # 'minecraft:potted_cornflower': (ids.block_minecraft__potted_cornflower, 0),  # not rendering
            # 'minecraft:potted_wither_rose': (ids.block_minecraft__potted_wither_rose, 0),  # not rendering
            # 'minecraft:potted_red_mushroom': (ids.block_minecraft__potted_red_mushroom, 0),  # not rendering
            # 'minecraft:potted_brown_mushroom': (ids.block_minecraft__potted_brown_mushroom, 0),  # not rendering
            # 'minecraft:potted_dead_bush': (ids.block_minecraft__potted_dead_bush, 0),  # not rendering
            # 'minecraft:potted_cactus': (ids.block_minecraft__potted_cactus, 0),  # not rendering
            # 'minecraft:potted_bamboo': (ids.block_minecraft__potted_bamboo, 0),  # not rendering
            # 'minecraft:flower_pot': (ids.block_minecraft__flower_pot, 0),  # not rendering
            # 'minecraft:potted_crimson_fungus': (ids.block_minecraft__potted_crimson_fungus, 0),  # not rendering
            # 'minecraft:potted_warped_fungus': (ids.block_minecraft__potted_warped_fungus, 0),  # not rendering
            # 'minecraft:potted_crimson_roots': (ids.block_minecraft__potted_crimson_roots, 0),  # not rendering
            # 'minecraft:potted_warped_roots': (ids.block_minecraft__potted_warped_roots, 0),  # not rendering
            
            # 'minecraft:white_banner': (ids.block_minecraft__white_banner, 0),  # not Rendering
            # 'minecraft:orange_banner': (ids.block_minecraft__orange_banner, 0),  # not Rendering
            # 'minecraft:magenta_banner': (ids.block_minecraft__magenta_banner, 0),  # not Rendering
            # 'minecraft:light_blue_banner': (ids.block_minecraft__light_blue_banner, 0),  # not Rendering
            # 'minecraft:yellow_banner': (ids.block_minecraft__yellow_banner, 0),  # not Rendering
            # 'minecraft:lime_banner': (ids.block_minecraft__lime_banner, 0),  # not Rendering
            # 'minecraft:pink_banner': (ids.block_minecraft__pink_banner, 0),  # not Rendering
            # 'minecraft:gray_banner': (ids.block_minecraft__gray_banner, 0),  # not Rendering
            # 'minecraft:light_gray_banner': (ids.block_minecraft__light_gray_banner, 0),  # not Rendering
            # 'minecraft:cyan_banner': (ids.block_minecraft__cyan_banner, 0),  # not Rendering
            # 'minecraft:purple_banner': (ids.block_minecraft__purple_banner, 0),  # not Rendering
            # 'minecraft:blue_banner': (ids.block_minecraft__blue_banner, 0),  # not Rendering
            # 'minecraft:brown_banner': (ids.block_minecraft__brown_banner, 0),  # not Rendering
            # 'minecraft:green_banner': (ids.block_minecraft__green_banner, 0),  # not Rendering
            # 'minecraft:red_banner': (ids.block_minecraft__red_banner, 0),  # not Rendering
            # 'minecraft:black_banner': (ids.block_minecraft__black_banner, 0),  # not Rendering
            # 'minecraft:white_wall_banner': (ids.block_minecraft__white_wall_banner, 0),  # not Rendering
            # 'minecraft:orange_wall_banner': (ids.block_minecraft__orange_wall_banner, 0),  # not Rendering
            # 'minecraft:magenta_wall_banner': (ids.block_minecraft__magenta_wall_banner, 0),  # not Rendering
            # 'minecraft:yellow_wall_banner': (ids.block_minecraft__yellow_wall_banner, 0),  # not Rendering
            # 'minecraft:lime_wall_banner': (ids.block_minecraft__lime_wall_banner, 0),  # not Rendering
            # 'minecraft:pink_wall_banner': (ids.block_minecraft__pink_wall_banner, 0),  # not Rendering
            # 'minecraft:gray_wall_banner': (ids.block_minecraft__gray_wall_banner, 0),  # not Rendering
            # 'minecraft:cyan_wall_banner': (ids.block_minecraft__cyan_wall_banner, 0),  # not Rendering
            # 'minecraft:purple_wall_banner': (ids.block_minecraft__purple_wall_banner, 0),  # not Rendering
            # 'minecraft:blue_wall_banner': (ids.block_minecraft__blue_wall_banner, 0),  # not Rendering
            # 'minecraft:brown_wall_banner': (ids.block_minecraft__brown_wall_banner, 0),  # not Rendering
            # 'minecraft:green_wall_banner': (ids.block_minecraft__green_wall_banner, 0),  # not Rendering
            # 'minecraft:red_wall_banner': (ids.block_minecraft__red_wall_banner, 0),  # not Rendering
            # 'minecraft:black_wall_banner': (ids.block_minecraft__black_wall_banner, 0),  # not Rendering
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

        if 'Properties' in palette_entry.keys():
            if 'waterlogged' in palette_entry['Properties'].keys():
                if palette_entry['Properties']['waterlogged'] == 'true':
                    block = ids.block_minecraft__water
                    data = 0

        if block in [ids.block_minecraft__redstone_ore, ids.block_minecraft__redstone_lamp]:
            if palette_entry['Properties']['lit'] == 'true':
                data = 1

        elif block in [ids.block_minecraft__tall_seagrass, ids.block_minecraft__seagrass]:
            block = ids.block_minecraft__water
            data = 0

        # elif block == ids.block_minecraft__water:
        #     data = int(palette_entry['Properties'].get('level', 0)) << 6

        elif block in ids.group_cube_full:
            facing = palette_entry.get('Properties', {'facing': 'south'}).get('facing', 'south')
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]

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

        elif block == ids.block_minecraft__daylight_detector:
            if palette_entry['Properties']['inverted'] == 'true':
                block = 178

        elif block == ids.block_minecraft__redstone_wire:
            data = int(palette_entry['Properties']['power'] != '0')
            index = 1
            for direction in ['east', 'north', 'west', 'south']:
                if palette_entry['Properties'][direction] == "side":
                    data += (0b1 << index)
                elif palette_entry['Properties'][direction] == "up":
                    data += (0b10 << index)
                index += 2

        elif block == ids.block_minecraft__grass_block:
            if palette_entry['Properties']['snowy'] == 'true':
                data |= 0x10

        elif block in ids.group_tall_sprite:
            if palette_entry['Properties']['half'] == 'lower':
                data = 1
            else:
                data = 0

        elif block in ids.group_slabs:
            if palette_entry['Properties']['type'] == 'top':
                data = 1
            elif palette_entry['Properties']['type'] == 'double':
                block = ids.double_slabs[block]
                data = 0

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
            if ((block in ids.group_piston and p.get('extended', 'false') == 'true') or (block == ids.block_minecraft__piston_head and p.get('type', 'normal') == 'sticky') or (block == ids.block_minecraft__observer and p.get('powered', 'false') == 'true')):
                data |= 0x08

        elif block in ids.group_cube_column:
            axis = palette_entry['Properties']['axis']
            if axis == 'x':
                data |= 4
            elif axis == 'z':
                data |= 8

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

            # inner_right north --> inner_left east
            # outer_right north --> outer_left east
            # inner_right east --> inner_left south
            # outer_right east --> outer_left south
            # inner_right south --> inner_left west
            # outer_right south --> outer_left west
            # inner_right west --> inner_left north
            # outer_right west --> outer_left north

            half = palette_entry['Properties']['half']
            if half == 'bottom':
                data = 0
            else:
                data = 1

            shape = palette_entry['Properties']['shape']
            if shape == 'straight':
                data += 2
            elif shape in ['outer_right', 'outer_left']:
                data += 4
            elif shape in ['inner_right', 'inner_left']:
                data += 6

            facing = palette_entry['Properties']['facing']
            if facing == 'north':
                if shape in ['outer_left', 'inner_left']:
                    data += 64
                else:
                    data += 8
            elif facing == 'east':
                if shape in ['outer_left', 'inner_left']:
                    data += 8
                else:
                    data += 16
            elif facing == 'south':
                if shape in ['outer_left', 'inner_left']:
                    data += 16
                else:
                    data += 32
            elif facing == 'west':
                if shape in ['outer_left', 'inner_left']:
                    data += 32
                else:
                    data += 64

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

        elif block == ids.block_minecraft__lantern:
            if palette_entry['Properties']['hanging'] == 'true':
                data = 1
            else:
                data = 0

        elif block == ids.block_minecraft__composter:
            data = palette_entry['Properties']['level']

        elif block == ids.block_minecraft__barrel:
            facing_data = {'up': 0, 'down': 1, 'south': 2, 'east': 3, 'north': 4, 'west': 5}
            data = ((facing_data[palette_entry['Properties']['facing']] << 1) + (1 if palette_entry['Properties']['open'] == 'true' else 0))

        elif block in ids.group_bed:
            facing = palette_entry['Properties']['facing']
            data |= {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if palette_entry['Properties'].get('part', 'foot') == 'head':
                data |= 8

        elif block == ids.block_minecraft__end_portal_frame:
            facing = palette_entry['Properties']['facing']
            data |= {'south': 0, 'west': 1, 'north': 2, 'east': 3}[facing]
            if palette_entry['Properties'].get('eye', 'false') == 'true':
                data |= 4

        elif block == ids.block_minecraft__cauldron:
            data = int(palette_entry['Properties'].get('level', '0'))

        elif block == ids.block_minecraft__structure_block:
            block_mode = palette_entry['Properties'].get('mode', 'save')
            data = {'save': 0, 'load': 1, 'corner': 2, 'data': 3}.get(block_mode, 0)

        elif block == ids.block_minecraft__cake:
            data = int(palette_entry['Properties'].get('bites', '0'))

        elif block == ids.block_minecraft__farmland:
            # A moisture level of 7 has a different texture from other farmland
            data = 1 if palette_entry['Properties'].get('moisture', '0') == '7' else 0

        elif block in ids.group_glcbs:
            p = palette_entry['Properties']
            data = {'south': 0, 'west': 1, 'north': 2, 'east': 3}[p['facing']]
            if block == ids.block_minecraft__grindstone:
                data |= {'floor': 0, 'wall': 4, 'ceiling': 8}[p['face']]
            elif block == ids.block_minecraft__lectern:
                if p['has_book'] == 'true':
                    data |= 4
            elif block == ids.block_minecraft__campfire or block == ids.block_minecraft__soul_campfire:
                if p['lit'] == 'true':
                    data |= 4
            elif block == ids.block_minecraft__bell:
                data |= {'floor': 0, 'ceiling': 4, 'single_wall': 8,
                         'double_wall': 12}[p['attachment']]

        elif block == ids.block_minecraft__respawn_anchor:
            data = palette_entry['Properties']['charges']

        elif block == ids.block_minecraft__sea_pickle:
            if palette_entry['Properties'].get('waterlogged', False):
                block = ids.block_minecraft__air
            else:
                block = ids.block_minecraft__water

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

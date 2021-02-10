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

from collections import OrderedDict
import sys
import imp
import os
import os.path
import zipfile
from io import BytesIO
import math
from random import randint
import numpy
from PIL import Image, ImageEnhance, ImageOps, ImageDraw
import logging
import functools

from . import util
from . import ids

BLOCKTEX = "assets/minecraft/textures/block/"

# global variables to collate information in @material decorators
blockmap_generators = {}

known_blocks = set()
used_datas = set()
max_blockid = 0
max_data = 0

transparent_blocks = set()
solid_blocks = set()
fluid_blocks = set()
nospawn_blocks = set()
nodata_blocks = set()


# This is here for circular import reasons.
# Please don't ask, I choose to repress these memories.
# ... okay fine I'll tell you.
# Initialising the C extension requires access to the globals above.
# Due to the circular import, this wouldn't work, unless we reload the
# module in the C extension or just move the import below its dependencies.
from .c_overviewer import alpha_over


class TextureException(Exception):
    "To be thrown when a texture is not found."
    pass


color_map = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
             "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black"]


##
## Textures object
##
class Textures(object):
    """An object that generates a set of block sprites to use while
    rendering. It accepts a background color, north direction, and
    local textures path.
    """
    def __init__(self, texturepath=None, bgcolor=(26, 26, 26, 0), northdirection=0):
        self.bgcolor = bgcolor
        self.rotation = northdirection
        self.find_file_local_path = texturepath
        
        # not yet configurable
        self.texture_size = 24
        self.texture_dimensions = (self.texture_size, self.texture_size)
        
        # this is set in in generate()
        self.generated = False

        # see load_image_texture()
        self.texture_cache = {}

        # once we find a jarfile that contains a texture, we cache the ZipFile object here
        self.jars = OrderedDict()
    
    ##
    ## pickle support
    ##
    
    def __getstate__(self):
        # we must get rid of the huge image lists, and other images
        attributes = self.__dict__.copy()
        for attr in ['blockmap', 'biome_grass_texture', 'watertexture', 'lavatexture', 'firetexture', 'portaltexture', 'lightcolor', 'grasscolor', 'foliagecolor', 'watercolor', 'texture_cache']:
            try:
                del attributes[attr]
            except KeyError:
                pass
        attributes['jars'] = OrderedDict()
        return attributes
    def __setstate__(self, attrs):
        # regenerate textures, if needed
        for attr, val in list(attrs.items()):
            setattr(self, attr, val)
        self.texture_cache = {}
        if self.generated:
            self.generate()
    
    ##
    ## The big one: generate()
    ##
    
    def generate(self):
        # Make sure we have the foliage/grasscolor images available
        try:
            self.load_foliage_color()
            self.load_grass_color()
        except TextureException as e:
            logging.error(
                "Your system is missing either assets/minecraft/textures/colormap/foliage.png "
                "or assets/minecraft/textures/colormap/grass.png. Either complement your "
                "resource pack with these texture files, or install the vanilla Minecraft "
                "client to use as a fallback.")
            raise e
        
        # generate biome grass mask
        self.biome_grass_texture = self.build_block(self.load_image_texture("assets/minecraft/textures/block/grass_block_top.png"), self.load_image_texture("assets/minecraft/textures/block/grass_block_side_overlay.png"))
        
        # generate the blocks
        global blockmap_generators
        global known_blocks, used_datas
        self.blockmap = [None] * max_blockid * max_data
        
        for (blockid, data), texgen in list(blockmap_generators.items()):
            tex = texgen(self, blockid, data)
            self.blockmap[blockid * max_data + data] = self.generate_texture_tuple(tex)
        
        if self.texture_size != 24:
            # rescale biome grass
            self.biome_grass_texture = self.biome_grass_texture.resize(self.texture_dimensions, Image.ANTIALIAS)
            
            # rescale the rest
            for i, tex in enumerate(blockmap):
                if tex is None:
                    continue
                block = tex[0]
                scaled_block = block.resize(self.texture_dimensions, Image.ANTIALIAS)
                blockmap[i] = self.generate_texture_tuple(scaled_block)
        
        self.generated = True
    
    ##
    ## Helpers for opening textures
    ##
    
    def find_file(self, filename, mode="rb", verbose=False):
        """Searches for the given file and returns an open handle to it.
        This searches the following locations in this order:
        
        * In the directory textures_path given in the initializer if not already open
        * In an already open resource pack or client jar file
        * In the resource pack given by textures_path
        * The program dir (same dir as overviewer.py) for extracted textures
        * On Darwin, in /Applications/Minecraft for extracted textures
        * Inside a minecraft client jar. Client jars are searched for in the
          following location depending on platform:
        
            * On Windows, at %APPDATA%/.minecraft/versions/
            * On Darwin, at
                $HOME/Library/Application Support/minecraft/versions
            * at $HOME/.minecraft/versions/

          Only the latest non-snapshot version >1.6 is used

        * The overviewer_core/data/textures dir
        
        """
        if verbose: logging.info("Starting search for {0}".format(filename))

        # Look for the file is stored in with the overviewer
        # installation. We include a few files that aren't included with Minecraft
        # textures. This used to be for things such as water and lava, since
        # they were generated by the game and not stored as images. Nowdays I
        # believe that's not true, but we still have a few files distributed
        # with overviewer.
        # Do this first so we don't try all .jar files for stuff like "water.png"
        programdir = util.get_program_path()
        if verbose: logging.info("Looking for texture in overviewer_core/data/textures")
        path = os.path.join(programdir, "overviewer_core", "data", "textures", filename)
        if os.path.isfile(path):
            if verbose: logging.info("Found %s in '%s'", filename, path)
            return open(path, mode)
        elif hasattr(sys, "frozen") or imp.is_frozen("__main__"):
            # windows special case, when the package dir doesn't exist
            path = os.path.join(programdir, "textures", filename)
            if os.path.isfile(path):
                if verbose: logging.info("Found %s in '%s'", filename, path)
                return open(path, mode)

        # A texture path was given on the command line. Search this location
        # for the file first.
        if self.find_file_local_path:
            if (self.find_file_local_path not in self.jars
                and os.path.isfile(self.find_file_local_path)):
                # Must be a resource pack. Look for the requested file within
                # it.
                try:
                    pack = zipfile.ZipFile(self.find_file_local_path)
                    # pack.getinfo() will raise KeyError if the file is
                    # not found.
                    pack.getinfo(filename)
                    if verbose: logging.info("Found %s in '%s'", filename,
                                             self.find_file_local_path)
                    self.jars[self.find_file_local_path] = pack
                    # ok cool now move this to the start so we pick it first
                    self.jars.move_to_end(self.find_file_local_path, last=False)
                    return pack.open(filename)
                except (zipfile.BadZipfile, KeyError, IOError):
                    pass
            elif os.path.isdir(self.find_file_local_path):
                full_path = os.path.join(self.find_file_local_path, filename)
                if os.path.isfile(full_path):
                        if verbose: logging.info("Found %s in '%s'", filename, full_path)
                        return open(full_path, mode)

        # We already have some jars open, better use them.
        if len(self.jars) > 0:
            for jarpath in self.jars:
                try:
                    jar = self.jars[jarpath]
                    jar.getinfo(filename)
                    if verbose: logging.info("Found (cached) %s in '%s'", filename,
                                             jarpath)
                    return jar.open(filename)
                except (KeyError, IOError) as e:
                    pass

        # If we haven't returned at this point, then the requested file was NOT
        # found in the user-specified texture path or resource pack.
        if verbose: logging.info("Did not find the file in specified texture path")


        # Look in the location of the overviewer executable for the given path
        path = os.path.join(programdir, filename)
        if os.path.isfile(path):
            if verbose: logging.info("Found %s in '%s'", filename, path)
            return open(path, mode)

        if sys.platform.startswith("darwin"):
            path = os.path.join("/Applications/Minecraft", filename)
            if os.path.isfile(path):
                if verbose: logging.info("Found %s in '%s'", filename, path)
                return open(path, mode)

        if verbose: logging.info("Did not find the file in overviewer executable directory")
        if verbose: logging.info("Looking for installed minecraft jar files...")

        # Find an installed minecraft client jar and look in it for the texture
        # file we need.
        versiondir = ""
        if "APPDATA" in os.environ and sys.platform.startswith("win"):
            versiondir = os.path.join(os.environ['APPDATA'], ".minecraft", "versions")
        elif "HOME" in os.environ:
            # For linux:
            versiondir = os.path.join(os.environ['HOME'], ".minecraft", "versions")
            if not os.path.exists(versiondir) and sys.platform.startswith("darwin"):
                # For Mac:
                versiondir = os.path.join(os.environ['HOME'], "Library",
                    "Application Support", "minecraft", "versions")

        try:
            if verbose: logging.info("Looking in the following directory: \"%s\"" % versiondir)
            versions = os.listdir(versiondir)
            if verbose: logging.info("Found these versions: {0}".format(versions))
        except OSError:
            # Directory doesn't exist? Ignore it. It will find no versions and
            # fall through the checks below to the error at the bottom of the
            # method.
            versions = []

        available_versions = []
        for version in versions:
            # Look for the latest non-snapshot that is at least 1.8. This
            # version is only compatible with >=1.8, and we cannot in general
            # tell if a snapshot is more or less recent than a release.

            # Allow two component names such as "1.8" and three component names
            # such as "1.8.1"
            if version.count(".") not in (1,2):
                continue
            try:
                versionparts = [int(x) for x in version.split(".")]
            except ValueError:
                continue

            if versionparts < [1,8]:
                continue

            available_versions.append(versionparts)

        available_versions.sort(reverse=True)
        if not available_versions:
            if verbose: logging.info("Did not find any non-snapshot minecraft jars >=1.8.0")
        while(available_versions):
            most_recent_version = available_versions.pop(0)
            if verbose: logging.info("Trying {0}. Searching it for the file...".format(".".join(str(x) for x in most_recent_version)))

            jarname = ".".join(str(x) for x in most_recent_version)
            jarpath = os.path.join(versiondir, jarname, jarname + ".jar")

            if os.path.isfile(jarpath):
                try:
                    jar = zipfile.ZipFile(jarpath)
                    jar.getinfo(filename)
                    if verbose: logging.info("Found %s in '%s'", filename, jarpath)
                    self.jars[jarpath] = jar
                    return jar.open(filename)
                except (KeyError, IOError) as e:
                    pass
                except (zipfile.BadZipFile) as e:
                    logging.warning("Your jar {0} is corrupted, I'll be skipping it, but you "
                                    "should probably look into that.".format(jarpath))

            if verbose: logging.info("Did not find file {0} in jar {1}".format(filename, jarpath))
            

        raise TextureException("Could not find the textures while searching for '{0}'. Try specifying the 'texturepath' option in your config file.\nSet it to the path to a Minecraft Resource pack.\nAlternately, install the Minecraft client (which includes textures)\nAlso see <http://docs.overviewer.org/en/latest/running/#installing-the-textures>\n(Remember, this version of Overviewer requires a 1.16-compatible resource pack)\n(Also note that I won't automatically use snapshots; you'll have to use the texturepath option to use a snapshot jar)".format(filename))

    def load_image_texture(self, filename):
        # Textures may be animated or in a different resolution than 16x16.  
        # This method will always return a 16x16 image

        img = self.load_image(filename)

        w,h = img.size
        if w != h:
            img = img.crop((0,0,w,w))
        if w != 16:
            img = img.resize((16, 16), Image.ANTIALIAS)

        self.texture_cache[filename] = img
        return img

    def load_image(self, filename):
        """Returns an image object"""

        try:
            img = self.texture_cache[filename]
            if isinstance(img, Exception):  # Did we cache an exception?
                raise img                   # Okay then, raise it.
            return img
        except KeyError:
            pass
        
        try:
            fileobj = self.find_file(filename, verbose=logging.getLogger().isEnabledFor(logging.DEBUG))
        except (TextureException, IOError) as e:
            # We cache when our good friend find_file can't find
            # a texture, so that we do not repeatedly search for it.
            self.texture_cache[filename] = e
            raise e
        buffer = BytesIO(fileobj.read())
        try:
            img = Image.open(buffer).convert("RGBA")
        except IOError:
            raise TextureException("The texture {} appears to be corrupted. Please fix it. Run "
                                   "Overviewer in verbose mode (-v) to find out where I loaded "
                                   "that file from.".format(filename))
        self.texture_cache[filename] = img
        return img



    def load_water(self):
        """Special-case function for loading water."""
        watertexture = getattr(self, "watertexture", None)
        if watertexture:
            return watertexture
        watertexture = self.load_image_texture("assets/minecraft/textures/block/water_still.png")
        self.watertexture = watertexture
        return watertexture

    def load_lava(self):
        """Special-case function for loading lava."""
        lavatexture = getattr(self, "lavatexture", None)
        if lavatexture:
            return lavatexture
        lavatexture = self.load_image_texture("assets/minecraft/textures/block/lava_still.png")
        self.lavatexture = lavatexture
        return lavatexture
    
    def load_fire(self):
        """Special-case function for loading fire."""
        firetexture = getattr(self, "firetexture", None)
        if firetexture:
            return firetexture
        fireNS = self.load_image_texture("assets/minecraft/textures/block/fire_0.png")
        fireEW = self.load_image_texture("assets/minecraft/textures/block/fire_1.png")
        firetexture = (fireNS, fireEW)
        self.firetexture = firetexture
        return firetexture
    
    def load_portal(self):
        """Special-case function for loading portal."""
        portaltexture = getattr(self, "portaltexture", None)
        if portaltexture:
            return portaltexture
        portaltexture = self.load_image_texture("assets/minecraft/textures/block/nether_portal.png")
        self.portaltexture = portaltexture
        return portaltexture
    
    def load_light_color(self):
        """Helper function to load the light color texture."""
        if hasattr(self, "lightcolor"):
            return self.lightcolor
        try:
            lightcolor = list(self.load_image("light_normal.png").getdata())
        except Exception:
            logging.warning("Light color image could not be found.")
            lightcolor = None
        self.lightcolor = lightcolor
        return lightcolor
    
    def load_grass_color(self):
        """Helper function to load the grass color texture."""
        if not hasattr(self, "grasscolor"):
            self.grasscolor = list(self.load_image("assets/minecraft/textures/colormap/grass.png").getdata())
        return self.grasscolor

    def load_foliage_color(self):
        """Helper function to load the foliage color texture."""
        if not hasattr(self, "foliagecolor"):
            self.foliagecolor = list(self.load_image("assets/minecraft/textures/colormap/foliage.png").getdata())
        return self.foliagecolor

    #I guess "watercolor" is wrong. But I can't correct as my texture pack don't define water color.
    # def load_water_color(self):
    #     """Helper function to load the water color texture."""
    #     if not hasattr(self, "watercolor"):
    #         self.watercolor = list(self.load_image("watercolor.png").getdata())
    #     return self.watercolor

    def _split_terrain(self, terrain):
        """Builds and returns a length 256 array of each 16x16 chunk
        of texture.
        """
        textures = []
        (terrain_width, terrain_height) = terrain.size
        texture_resolution = terrain_width / 16
        for y in range(16):
            for x in range(16):
                left = x*texture_resolution
                upper = y*texture_resolution
                right = left+texture_resolution
                lower = upper+texture_resolution
                region = terrain.transform(
                          (16, 16),
                          Image.EXTENT,
                          (left,upper,right,lower),
                          Image.BICUBIC)
                textures.append(region)

        return textures

    ##
    ## Image Transformation Functions
    ##

    @staticmethod
    def transform_image_top(img):
        """Takes a PIL image and rotates it left 45 degrees and shrinks the y axis
        by a factor of 2. Returns the resulting image, which will be 24x12 pixels

        """

        # Resize to 17x17, since the diagonal is approximately 24 pixels, a nice
        # even number that can be split in half twice
        img = img.resize((17, 17), Image.ANTIALIAS)

        # Build the Affine transformation matrix for this perspective
        transform = numpy.matrix(numpy.identity(3))
        # Translate up and left, since rotations are about the origin
        transform *= numpy.matrix([[1,0,8.5],[0,1,8.5],[0,0,1]])
        # Rotate 45 degrees
        ratio = math.cos(math.pi/4)
        #transform *= numpy.matrix("[0.707,-0.707,0;0.707,0.707,0;0,0,1]")
        transform *= numpy.matrix([[ratio,-ratio,0],[ratio,ratio,0],[0,0,1]])
        # Translate back down and right
        transform *= numpy.matrix([[1,0,-12],[0,1,-12],[0,0,1]])
        # scale the image down by a factor of 2
        transform *= numpy.matrix("[1,0,0;0,2,0;0,0,1]")

        transform = numpy.array(transform)[:2,:].ravel().tolist()

        newimg = img.transform((24,12), Image.AFFINE, transform)
        return newimg

    @staticmethod
    def transform_image_side(img):
        """Takes an image and shears it for the left side of the cube (reflect for
        the right side)"""

        # Size of the cube side before shear
        img = img.resize((12,12), Image.ANTIALIAS)

        # Apply shear
        transform = numpy.matrix(numpy.identity(3))
        transform *= numpy.matrix("[1,0,0;-0.5,1,0;0,0,1]")

        transform = numpy.array(transform)[:2,:].ravel().tolist()

        newimg = img.transform((12,18), Image.AFFINE, transform)
        return newimg

    @staticmethod
    def transform_image_slope(img):
        """Takes an image and shears it in the shape of a slope going up
        in the -y direction (reflect for +x direction). Used for minetracks"""

        # Take the same size as trasform_image_side
        img = img.resize((12,12), Image.ANTIALIAS)

        # Apply shear
        transform = numpy.matrix(numpy.identity(3))
        transform *= numpy.matrix("[0.75,-0.5,3;0.25,0.5,-3;0,0,1]")
        transform = numpy.array(transform)[:2,:].ravel().tolist()

        newimg = img.transform((24,24), Image.AFFINE, transform)

        return newimg


    @staticmethod
    def transform_image_angle(img, angle):
        """Takes an image an shears it in arbitrary angle with the axis of
        rotation being vertical.

        WARNING! Don't use angle = pi/2 (or multiplies), it will return
        a blank image (or maybe garbage).

        NOTE: angle is in the image not in game, so for the left side of a
        block angle = 30 degree.
        """

        # Take the same size as trasform_image_side
        img = img.resize((12,12), Image.ANTIALIAS)

        # some values
        cos_angle = math.cos(angle)
        sin_angle = math.sin(angle)

        # function_x and function_y are used to keep the result image in the 
        # same position, and constant_x and constant_y are the coordinates
        # for the center for angle = 0.
        constant_x = 6.
        constant_y = 6.
        function_x = 6.*(1-cos_angle)
        function_y = -6*sin_angle
        big_term = ( (sin_angle * (function_x + constant_x)) - cos_angle* (function_y + constant_y))/cos_angle

        # The numpy array is not really used, but is helpful to 
        # see the matrix used for the transformation.
        transform = numpy.array([[1./cos_angle, 0, -(function_x + constant_x)/cos_angle],
                                 [-sin_angle/(cos_angle), 1., big_term ],
                                 [0, 0, 1.]])

        transform = tuple(transform[0]) + tuple(transform[1])

        newimg = img.transform((24,24), Image.AFFINE, transform)

        return newimg


    def build_block(self, top, side):
        """From a top texture and a side texture, build a block image.
        top and side should be 16x16 image objects. Returns a 24x24 image

        """
        img = Image.new("RGBA", (24,24), self.bgcolor)

        original_texture = top.copy()
        top = self.transform_image_top(top)

        if not side:
            alpha_over(img, top, (0,0), top)
            return img

        side = self.transform_image_side(side)
        otherside = side.transpose(Image.FLIP_LEFT_RIGHT)

        # Darken the sides slightly. These methods also affect the alpha layer,
        # so save them first (we don't want to "darken" the alpha layer making
        # the block transparent)
        sidealpha = side.split()[3]
        side = ImageEnhance.Brightness(side).enhance(0.9)
        side.putalpha(sidealpha)
        othersidealpha = otherside.split()[3]
        otherside = ImageEnhance.Brightness(otherside).enhance(0.8)
        otherside.putalpha(othersidealpha)

        alpha_over(img, top, (0,0), top)
        alpha_over(img, side, (0,6), side)
        alpha_over(img, otherside, (12,6), otherside)

        # Manually touch up 6 pixels that leave a gap because of how the
        # shearing works out. This makes the blocks perfectly tessellate-able
        for x,y in [(13,23), (17,21), (21,19)]:
            # Copy a pixel to x,y from x-1,y
            img.putpixel((x,y), img.getpixel((x-1,y)))
        for x,y in [(3,4), (7,2), (11,0)]:
            # Copy a pixel to x,y from x+1,y
            img.putpixel((x,y), img.getpixel((x+1,y)))

        return img

    def build_slab_block(self, top, side, upper):
        """From a top texture and a side texture, build a slab block image.
        top and side should be 16x16 image objects. Returns a 24x24 image

        """
        # cut the side texture in half
        mask = side.crop((0,8,16,16))
        side = Image.new(side.mode, side.size, self.bgcolor)
        alpha_over(side, mask,(0,0,16,8), mask)

        # plain slab
        top = self.transform_image_top(top)
        side = self.transform_image_side(side)
        otherside = side.transpose(Image.FLIP_LEFT_RIGHT)

        sidealpha = side.split()[3]
        side = ImageEnhance.Brightness(side).enhance(0.9)
        side.putalpha(sidealpha)
        othersidealpha = otherside.split()[3]
        otherside = ImageEnhance.Brightness(otherside).enhance(0.8)
        otherside.putalpha(othersidealpha)

        # upside down slab
        delta = 0
        if upper:
            delta = 6

        img = Image.new("RGBA", (24,24), self.bgcolor)
        alpha_over(img, side, (0,12 - delta), side)
        alpha_over(img, otherside, (12,12 - delta), otherside)
        alpha_over(img, top, (0,6 - delta), top)

        # Manually touch up 6 pixels that leave a gap because of how the
        # shearing works out. This makes the blocks perfectly tessellate-able
        if upper:
            for x,y in [(3,4), (7,2), (11,0)]:
                # Copy a pixel to x,y from x+1,y
                img.putpixel((x,y), img.getpixel((x+1,y)))
            for x,y in [(13,17), (17,15), (21,13)]:
                # Copy a pixel to x,y from x-1,y
                img.putpixel((x,y), img.getpixel((x-1,y)))
        else:
            for x,y in [(3,10), (7,8), (11,6)]:
                # Copy a pixel to x,y from x+1,y
                img.putpixel((x,y), img.getpixel((x+1,y)))
            for x,y in [(13,23), (17,21), (21,19)]:
                # Copy a pixel to x,y from x-1,y
                img.putpixel((x,y), img.getpixel((x-1,y)))

        return img

    def build_full_block(self, top, side1, side2, side3, side4, bottom=None):
        """From a top texture, a bottom texture and 4 different side textures,
        build a full block with four differnts faces. All images should be 16x16 
        image objects. Returns a 24x24 image. Can be used to render any block.

        side1 is in the -y face of the cube     (top left, east)
        side2 is in the +x                      (top right, south)
        side3 is in the -x                      (bottom left, north)
        side4 is in the +y                      (bottom right, west)

        A non transparent block uses top, side 3 and side 4.

        If top is a tuple then first item is the top image and the second
        item is an increment (integer) from 0 to 16 (pixels in the
        original minecraft texture). This increment will be used to crop the
        side images and to paste the top image increment pixels lower, so if
        you use an increment of 8, it will draw a half-block.

        NOTE: this method uses the bottom of the texture image (as done in 
        minecraft with beds and cakes)

        """

        increment = 0
        if isinstance(top, tuple):
            increment = int(round((top[1] / 16.)*12.)) # range increment in the block height in pixels (half texture size)
            crop_height = increment
            top = top[0]
            if side1 is not None:
                side1 = side1.copy()
                ImageDraw.Draw(side1).rectangle((0, 0,16,crop_height),outline=(0,0,0,0),fill=(0,0,0,0))
            if side2 is not None:
                side2 = side2.copy()
                ImageDraw.Draw(side2).rectangle((0, 0,16,crop_height),outline=(0,0,0,0),fill=(0,0,0,0))
            if side3 is not None:
                side3 = side3.copy()
                ImageDraw.Draw(side3).rectangle((0, 0,16,crop_height),outline=(0,0,0,0),fill=(0,0,0,0))
            if side4 is not None:
                side4 = side4.copy()
                ImageDraw.Draw(side4).rectangle((0, 0,16,crop_height),outline=(0,0,0,0),fill=(0,0,0,0))

        img = Image.new("RGBA", (24,24), self.bgcolor)

        # first back sides
        if side1 is not None :
            side1 = self.transform_image_side(side1)
            side1 = side1.transpose(Image.FLIP_LEFT_RIGHT)

            # Darken this side.
            sidealpha = side1.split()[3]
            side1 = ImageEnhance.Brightness(side1).enhance(0.9)
            side1.putalpha(sidealpha)        

            alpha_over(img, side1, (0,0), side1)


        if side2 is not None :
            side2 = self.transform_image_side(side2)

            # Darken this side.
            sidealpha2 = side2.split()[3]
            side2 = ImageEnhance.Brightness(side2).enhance(0.8)
            side2.putalpha(sidealpha2)

            alpha_over(img, side2, (12,0), side2)

        if bottom is not None :
            bottom = self.transform_image_top(bottom)
            alpha_over(img, bottom, (0,12), bottom)

        # front sides
        if side3 is not None :
            side3 = self.transform_image_side(side3)

            # Darken this side
            sidealpha = side3.split()[3]
            side3 = ImageEnhance.Brightness(side3).enhance(0.9)
            side3.putalpha(sidealpha)

            alpha_over(img, side3, (0,6), side3)

        if side4 is not None :
            side4 = self.transform_image_side(side4)
            side4 = side4.transpose(Image.FLIP_LEFT_RIGHT)

            # Darken this side
            sidealpha = side4.split()[3]
            side4 = ImageEnhance.Brightness(side4).enhance(0.8)
            side4.putalpha(sidealpha)

            alpha_over(img, side4, (12,6), side4)

        if top is not None :
            top = self.transform_image_top(top)
            alpha_over(img, top, (0, increment), top)

        # Manually touch up 6 pixels that leave a gap because of how the
        # shearing works out. This makes the blocks perfectly tessellate-able
        for x,y in [(13,23), (17,21), (21,19)]:
            # Copy a pixel to x,y from x-1,y
            img.putpixel((x,y), img.getpixel((x-1,y)))
        for x,y in [(3,4), (7,2), (11,0)]:
            # Copy a pixel to x,y from x+1,y
            img.putpixel((x,y), img.getpixel((x+1,y)))

        return img

    def build_sprite(self, side):
        """From a side texture, create a sprite-like texture such as those used
        for spiderwebs or flowers."""
        img = Image.new("RGBA", (24,24), self.bgcolor)

        side = self.transform_image_side(side)
        otherside = side.transpose(Image.FLIP_LEFT_RIGHT)

        alpha_over(img, side, (6,3), side)
        alpha_over(img, otherside, (6,3), otherside)
        return img

    def build_billboard(self, tex):
        """From a texture, create a billboard-like texture such as
        those used for tall grass or melon stems.
        """
        img = Image.new("RGBA", (24,24), self.bgcolor)

        front = tex.resize((14, 12), Image.ANTIALIAS)
        alpha_over(img, front, (5,9))
        return img

    def generate_opaque_mask(self, img):
        """ Takes the alpha channel of the image and generates a mask
        (used for lighting the block) that deprecates values of alpha
        smallers than 50, and sets every other value to 255. """

        alpha = img.split()[3]
        return alpha.point(lambda a: int(min(a, 25.5) * 10))

    def tint_texture(self, im, c):
        # apparently converting to grayscale drops the alpha channel?
        i = ImageOps.colorize(ImageOps.grayscale(im), (0,0,0), c)
        i.putalpha(im.split()[3]); # copy the alpha band back in. assuming RGBA
        return i

    def generate_texture_tuple(self, img):
        """ This takes an image and returns the needed tuple for the
        blockmap array."""
        if img is None:
            return None
        return (img, self.generate_opaque_mask(img))

##
## The other big one: @material and associated framework
##


# the material registration decorator
def material(blockid=[], data=[0], **kwargs):
    # mapping from property name to the set to store them in
    properties = {"transparent" : transparent_blocks, "solid" : solid_blocks, "fluid" : fluid_blocks, "nospawn" : nospawn_blocks, "nodata" : nodata_blocks}
    
    # make sure blockid and data are iterable
    try:
        iter(blockid)
    except Exception:
        blockid = [blockid,]
    try:
        iter(data)
    except Exception:
        data = [data,]
        
    def inner_material(func):
        global blockmap_generators
        global max_data, max_blockid

        # create a wrapper function with a known signature
        @functools.wraps(func)
        def func_wrapper(texobj, blockid, data):
            return func(texobj, blockid, data)
        
        used_datas.update(data)
        if max(data) >= max_data:
            max_data = max(data) + 1
        
        for block in blockid:
            # set the property sets appropriately
            known_blocks.update([block])
            if block >= max_blockid:
                max_blockid = block + 1
            for prop in properties:
                try:
                    if block in kwargs.get(prop, []):
                        properties[prop].update([block])
                except TypeError:
                    if kwargs.get(prop, False):
                        properties[prop].update([block])
            
            # populate blockmap_generators with our function
            for d in data:
                blockmap_generators[(block, d)] = func_wrapper
        
        return func_wrapper
    return inner_material

# shortcut function for pure blocks, default to solid, nodata
def block(blockid=[], top_image=None, side_image=None, **kwargs):
    new_kwargs = {'solid': True, 'nodata': True}
    new_kwargs.update(kwargs)

    if top_image is None:
        raise ValueError("top_image was not provided")

    if side_image is None:
        side_image = top_image

    @material(blockid=blockid, **new_kwargs)
    def inner_block(self, unused_id, unused_data):
        return self.build_block(self.load_image_texture(top_image), self.load_image_texture(side_image))
    return inner_block

# shortcut function for sprite blocks, defaults to transparent, nodata
def sprite(blockid=[], imagename=None, **kwargs):
    new_kwargs = {'transparent' : True, 'nodata' : True}
    new_kwargs.update(kwargs)
    
    if imagename is None:
        raise ValueError("imagename was not provided")
    
    @material(blockid=blockid, **new_kwargs)
    def inner_sprite(self, unused_id, unused_data):
        return self.build_sprite(self.load_image_texture(imagename))
    return inner_sprite

# shortcut function for billboard blocks, defaults to transparent, nodata
def billboard(blockid=[], imagename=None, **kwargs):
    new_kwargs = {'transparent' : True, 'nodata' : True}
    new_kwargs.update(kwargs)
    
    if imagename is None:
        raise ValueError("imagename was not provided")
    
    @material(blockid=blockid, **new_kwargs)
    def inner_billboard(self, unused_id, unused_data):
        return self.build_billboard(self.load_image_texture(imagename))
    return inner_billboard
##
## and finally: actual texture definitions
##


# simple bloc only 1 or 2 textures
block(blockid=ids.block_minecraft__stone, top_image="assets/minecraft/textures/block/stone.png")
block(blockid=ids.block_minecraft__granite, top_image="assets/minecraft/textures/block/granite.png")
block(blockid=ids.block_minecraft__polished_granite, top_image="assets/minecraft/textures/block/polished_granite.png")
block(blockid=ids.block_minecraft__diorite, top_image="assets/minecraft/textures/block/diorite.png")
block(blockid=ids.block_minecraft__polished_diorite, top_image="assets/minecraft/textures/block/polished_diorite.png")
block(blockid=ids.block_minecraft__andesite, top_image="assets/minecraft/textures/block/andesite.png")
block(blockid=ids.block_minecraft__polished_andesite, top_image="assets/minecraft/textures/block/polished_andesite.png")
block(blockid=ids.block_minecraft__dirt, top_image="assets/minecraft/textures/block/dirt.png")
block(blockid=ids.block_minecraft__coarse_dirt, top_image="assets/minecraft/textures/block/coarse_dirt.png")
block(blockid=ids.block_minecraft__podzol, top_image="assets/minecraft/textures/block/podzol_top.png", side_image="assets/minecraft/textures/block/podzol_side.png")
block(blockid=ids.block_minecraft__cobblestone, top_image="assets/minecraft/textures/block/cobblestone.png")
block(blockid=ids.block_minecraft__oak_planks, top_image="assets/minecraft/textures/block/oak_planks.png")
block(blockid=ids.block_minecraft__spruce_planks, top_image="assets/minecraft/textures/block/spruce_planks.png")
block(blockid=ids.block_minecraft__birch_planks, top_image="assets/minecraft/textures/block/birch_planks.png")
block(blockid=ids.block_minecraft__jungle_planks, top_image="assets/minecraft/textures/block/jungle_planks.png")
block(blockid=ids.block_minecraft__acacia_planks, top_image="assets/minecraft/textures/block/acacia_planks.png")
block(blockid=ids.block_minecraft__dark_oak_planks, top_image="assets/minecraft/textures/block/dark_oak_planks.png")
block(blockid=ids.block_minecraft__crimson_planks, top_image="assets/minecraft/textures/block/crimson_planks.png")
block(blockid=ids.block_minecraft__warped_planks, top_image="assets/minecraft/textures/block/warped_planks.png")
block(blockid=ids.block_minecraft__oak_leaves, top_image="assets/minecraft/textures/block/oak_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__spruce_leaves, top_image="assets/minecraft/textures/block/spruce_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__birch_leaves, top_image="assets/minecraft/textures/block/birch_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__jungle_leaves, top_image="assets/minecraft/textures/block/jungle_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__acacia_leaves, top_image="assets/minecraft/textures/block/acacia_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__dark_oak_leaves, top_image="assets/minecraft/textures/block/dark_oak_leaves.png", transparent=True, solid=True)
block(blockid=ids.block_minecraft__bedrock, top_image="assets/minecraft/textures/block/bedrock.png")
block(blockid=ids.block_minecraft__sand, top_image="assets/minecraft/textures/block/sand.png")
block(blockid=ids.block_minecraft__red_sand, top_image="assets/minecraft/textures/block/red_sand.png")
block(blockid=ids.block_minecraft__gravel, top_image="assets/minecraft/textures/block/gravel.png")
block(blockid=ids.block_minecraft__gold_ore, top_image="assets/minecraft/textures/block/gold_ore.png")
block(blockid=ids.block_minecraft__iron_ore, top_image="assets/minecraft/textures/block/iron_ore.png")
block(blockid=ids.block_minecraft__coal_ore, top_image="assets/minecraft/textures/block/coal_ore.png")
block(blockid=ids.block_minecraft__sponge, top_image="assets/minecraft/textures/block/sponge.png")
block(blockid=ids.block_minecraft__wet_sponge, top_image="assets/minecraft/textures/block/sponge.png")
block(blockid=ids.block_minecraft__lapis_ore, top_image="assets/minecraft/textures/block/lapis_ore.png")
block(blockid=ids.block_minecraft__lapis_block, top_image="assets/minecraft/textures/block/lapis_block.png")
block(blockid=ids.block_minecraft__sandstone, top_image="assets/minecraft/textures/block/sandstone_top.png", side_image="assets/minecraft/textures/block/sandstone.png")
block(blockid=ids.block_minecraft__chiseled_sandstone, top_image="assets/minecraft/textures/block/sandstone_top.png", side_image="assets/minecraft/textures/block/chiseled_sandstone.png")
block(blockid=ids.block_minecraft__cut_sandstone, top_image="assets/minecraft/textures/block/sandstone_top.png", side_image="assets/minecraft/textures/block/cut_sandstone.png")
block(blockid=ids.block_minecraft__note_block, top_image="assets/minecraft/textures/block/note_block.png")
block(blockid=ids.block_minecraft__chiseled_red_sandstone, top_image="assets/minecraft/textures/block/red_sandstone_top.png", side_image="assets/minecraft/textures/block/chiseled_red_sandstone.png")
block(blockid=ids.block_minecraft__cut_red_sandstone, top_image="assets/minecraft/textures/block/red_sandstone_top.png", side_image="assets/minecraft/textures/block/cut_red_sandstone.png")
sprite(blockid=ids.block_minecraft__cobweb, imagename="assets/minecraft/textures/block/cobweb.png", nospawn=True)
block(blockid=ids.block_minecraft__white_wool, top_image="assets/minecraft/textures/block/white_wool.png")
block(blockid=ids.block_minecraft__orange_wool, top_image="assets/minecraft/textures/block/orange_wool.png")
block(blockid=ids.block_minecraft__magenta_wool, top_image="assets/minecraft/textures/block/magenta_wool.png")
block(blockid=ids.block_minecraft__light_blue_wool, top_image="assets/minecraft/textures/block/light_blue_wool.png")
block(blockid=ids.block_minecraft__yellow_wool, top_image="assets/minecraft/textures/block/yellow_wool.png")
block(blockid=ids.block_minecraft__lime_wool, top_image="assets/minecraft/textures/block/lime_wool.png")
block(blockid=ids.block_minecraft__pink_wool, top_image="assets/minecraft/textures/block/pink_wool.png")
block(blockid=ids.block_minecraft__gray_wool, top_image="assets/minecraft/textures/block/gray_wool.png")
block(blockid=ids.block_minecraft__light_gray_wool, top_image="assets/minecraft/textures/block/light_gray_wool.png")
block(blockid=ids.block_minecraft__cyan_wool, top_image="assets/minecraft/textures/block/cyan_wool.png")
block(blockid=ids.block_minecraft__purple_wool, top_image="assets/minecraft/textures/block/purple_wool.png")
block(blockid=ids.block_minecraft__blue_wool, top_image="assets/minecraft/textures/block/blue_wool.png")
block(blockid=ids.block_minecraft__brown_wool, top_image="assets/minecraft/textures/block/brown_wool.png")
block(blockid=ids.block_minecraft__green_wool, top_image="assets/minecraft/textures/block/green_wool.png")
block(blockid=ids.block_minecraft__red_wool, top_image="assets/minecraft/textures/block/red_wool.png")
block(blockid=ids.block_minecraft__black_wool, top_image="assets/minecraft/textures/block/black_wool.png")
block(blockid=ids.block_minecraft__gold_block, top_image="assets/minecraft/textures/block/gold_block.png")
block(blockid=ids.block_minecraft__iron_block, top_image="assets/minecraft/textures/block/iron_block.png")
block(blockid=ids.block_minecraft__bricks, top_image="assets/minecraft/textures/block/bricks.png")
block(blockid=ids.block_minecraft__tnt, top_image="assets/minecraft/textures/block/tnt_top.png", side_image="assets/minecraft/textures/block/tnt_side.png", nospawn=True)
block(blockid=ids.block_minecraft__bookshelf, top_image="assets/minecraft/textures/block/oak_planks.png", side_image="assets/minecraft/textures/block/bookshelf.png")
block(blockid=ids.block_minecraft__mossy_cobblestone, top_image="assets/minecraft/textures/block/mossy_cobblestone.png")
block(blockid=ids.block_minecraft__obsidian, top_image="assets/minecraft/textures/block/obsidian.png")
block(blockid=ids.block_minecraft__spawner, top_image="assets/minecraft/textures/block/spawner.png", transparent=True)
block(blockid=ids.block_minecraft__diamond_ore, top_image="assets/minecraft/textures/block/diamond_ore.png")
block(blockid=ids.block_minecraft__diamond_block, top_image="assets/minecraft/textures/block/diamond_block.png")
block(blockid=ids.block_minecraft__redstone_ore, top_image="assets/minecraft/textures/block/redstone_ore.png")
block(blockid=ids.block_minecraft__snow_block, top_image="assets/minecraft/textures/block/snow.png")
block(blockid=ids.block_minecraft__clay, top_image="assets/minecraft/textures/block/clay.png")
block(blockid=ids.block_minecraft__jukebox, top_image="assets/minecraft/textures/block/jukebox_top.png", side_image="assets/minecraft/textures/block/note_block.png")
block(blockid=ids.block_minecraft__netherrack, top_image="assets/minecraft/textures/block/netherrack.png")
block(blockid=ids.block_minecraft__soul_sand, top_image="assets/minecraft/textures/block/soul_sand.png")
block(blockid=ids.block_minecraft__glowstone, top_image="assets/minecraft/textures/block/glowstone.png")
block(blockid=ids.block_minecraft__shroomlight, top_image="assets/minecraft/textures/block/shroomlight.png")
block(blockid=ids.block_minecraft__infested_cobblestone, top_image="assets/minecraft/textures/block/cobblestone.png")
block(blockid=ids.block_minecraft__infested_stone, top_image="assets/minecraft/textures/block/stone.png")
block(blockid=ids.block_minecraft__infested_stone_bricks, top_image="assets/minecraft/textures/block/stone_bricks.png")
block(blockid=ids.block_minecraft__infested_mossy_stone_bricks, top_image="assets/minecraft/textures/block/mossy_stone_bricks.png")
block(blockid=ids.block_minecraft__infested_cracked_stone_bricks, top_image="assets/minecraft/textures/block/cracked_stone_bricks.png")
block(blockid=ids.block_minecraft__infested_chiseled_stone_bricks, top_image="assets/minecraft/textures/block/chiseled_stone_bricks.png")
block(blockid=ids.block_minecraft__stone_bricks, top_image="assets/minecraft/textures/block/stone_bricks.png")
block(blockid=ids.block_minecraft__mossy_stone_bricks, top_image="assets/minecraft/textures/block/mossy_stone_bricks.png")
block(blockid=ids.block_minecraft__cracked_stone_bricks, top_image="assets/minecraft/textures/block/cracked_stone_bricks.png")
block(blockid=ids.block_minecraft__chiseled_stone_bricks, top_image="assets/minecraft/textures/block/chiseled_stone_bricks.png")
block(blockid=ids.block_minecraft__melon, top_image="assets/minecraft/textures/block/melon_top.png", side_image="assets/minecraft/textures/block/melon_side.png", solid=True)
block(blockid=ids.block_minecraft__mycelium, top_image="assets/minecraft/textures/block/mycelium_top.png", side_image="assets/minecraft/textures/block/mycelium_side.png")
block(blockid=ids.block_minecraft__warped_nylium, top_image="assets/minecraft/textures/block/warped_nylium.png", side_image="assets/minecraft/textures/block/warped_nylium_side.png")
block(blockid=ids.block_minecraft__crimson_nylium, top_image="assets/minecraft/textures/block/crimson_nylium.png", side_image="assets/minecraft/textures/block/crimson_nylium_side.png")
block(blockid=ids.block_minecraft__soul_soil, top_image="assets/minecraft/textures/block/soul_soil.png")
block(blockid=ids.block_minecraft__nether_gold_ore, top_image="assets/minecraft/textures/block/nether_gold_ore.png")
block(blockid=ids.block_minecraft__nether_bricks, top_image="assets/minecraft/textures/block/nether_bricks.png")
block(blockid=ids.block_minecraft__end_stone, top_image="assets/minecraft/textures/block/end_stone.png")
# NOTE: dragon egg this isn't a block, but I think it's better than nothing
block(blockid=ids.block_minecraft__dragon_egg, top_image="assets/minecraft/textures/block/dragon_egg.png")
block(blockid=ids.block_minecraft__emerald_ore, top_image="assets/minecraft/textures/block/emerald_ore.png")
block(blockid=ids.block_minecraft__emerald_block, top_image="assets/minecraft/textures/block/emerald_block.png")
block(blockid=ids.block_minecraft__redstone_block, top_image="assets/minecraft/textures/block/redstone_block.png")
block(blockid=ids.block_minecraft__nether_quartz_ore, top_image="assets/minecraft/textures/block/nether_quartz_ore.png")
block(blockid=[ids.block_minecraft__quartz_block, ids.block_minecraft__smooth_quartz], top_image="assets/minecraft/textures/block/quartz_block_top.png", side_image="assets/minecraft/textures/block/quartz_block_side.png")
block(blockid=ids.block_minecraft__chiseled_quartz_block, top_image="assets/minecraft/textures/block/chiseled_quartz_block_top.png", side_image="assets/minecraft/textures/block/chiseled_quartz_block.png")
block(blockid=ids.block_minecraft__slime_block, top_image="assets/minecraft/textures/block/slime_block.png")
block(blockid=ids.block_minecraft__prismarine, top_image="assets/minecraft/textures/block/prismarine.png")
block(blockid=ids.block_minecraft__prismarine_bricks, top_image="assets/minecraft/textures/block/prismarine_bricks.png")
block(blockid=ids.block_minecraft__dark_prismarine, top_image="assets/minecraft/textures/block/dark_prismarine.png")
block(blockid=ids.block_minecraft__sea_lantern, top_image="assets/minecraft/textures/block/sea_lantern.png")
block(blockid=ids.block_minecraft__terracotta, top_image="assets/minecraft/textures/block/terracotta.png")
block(blockid=ids.block_minecraft__white_terracotta, top_image="assets/minecraft/textures/block/white_terracotta.png")
block(blockid=ids.block_minecraft__orange_terracotta, top_image="assets/minecraft/textures/block/orange_terracotta.png")
block(blockid=ids.block_minecraft__magenta_terracotta, top_image="assets/minecraft/textures/block/magenta_terracotta.png")
block(blockid=ids.block_minecraft__light_blue_terracotta, top_image="assets/minecraft/textures/block/light_blue_terracotta.png")
block(blockid=ids.block_minecraft__yellow_terracotta, top_image="assets/minecraft/textures/block/yellow_terracotta.png")
block(blockid=ids.block_minecraft__lime_terracotta, top_image="assets/minecraft/textures/block/lime_terracotta.png")
block(blockid=ids.block_minecraft__pink_terracotta, top_image="assets/minecraft/textures/block/pink_terracotta.png")
block(blockid=ids.block_minecraft__gray_terracotta, top_image="assets/minecraft/textures/block/gray_terracotta.png")
block(blockid=ids.block_minecraft__light_gray_terracotta, top_image="assets/minecraft/textures/block/light_gray_terracotta.png")
block(blockid=ids.block_minecraft__cyan_terracotta, top_image="assets/minecraft/textures/block/cyan_terracotta.png")
block(blockid=ids.block_minecraft__purple_terracotta, top_image="assets/minecraft/textures/block/purple_terracotta.png")
block(blockid=ids.block_minecraft__blue_terracotta, top_image="assets/minecraft/textures/block/blue_terracotta.png")
block(blockid=ids.block_minecraft__brown_terracotta, top_image="assets/minecraft/textures/block/brown_terracotta.png")
block(blockid=ids.block_minecraft__green_terracotta, top_image="assets/minecraft/textures/block/green_terracotta.png")
block(blockid=ids.block_minecraft__red_terracotta, top_image="assets/minecraft/textures/block/red_terracotta.png")
block(blockid=ids.block_minecraft__black_terracotta, top_image="assets/minecraft/textures/block/black_terracotta.png")
block(blockid=ids.block_minecraft__coal_block, top_image="assets/minecraft/textures/block/coal_block.png")
block(blockid=ids.block_minecraft__packed_ice, top_image="assets/minecraft/textures/block/packed_ice.png")
block(blockid=ids.block_minecraft__blue_ice, top_image="assets/minecraft/textures/block/blue_ice.png")
block(blockid=ids.block_minecraft__smooth_stone, top_image="assets/minecraft/textures/block/smooth_stone.png")
block(blockid=ids.block_minecraft__smooth_sandstone, top_image="assets/minecraft/textures/block/sandstone_top.png")
block(blockid=ids.block_minecraft__smooth_red_sandstone, top_image="assets/minecraft/textures/block/red_sandstone_top.png")
block(blockid=ids.block_minecraft__brain_coral_block, top_image="assets/minecraft/textures/block/brain_coral_block.png")
block(blockid=ids.block_minecraft__bubble_coral_block, top_image="assets/minecraft/textures/block/bubble_coral_block.png")
block(blockid=ids.block_minecraft__fire_coral_block, top_image="assets/minecraft/textures/block/fire_coral_block.png")
block(blockid=ids.block_minecraft__horn_coral_block, top_image="assets/minecraft/textures/block/horn_coral_block.png")
block(blockid=ids.block_minecraft__tube_coral_block, top_image="assets/minecraft/textures/block/tube_coral_block.png")
block(blockid=ids.block_minecraft__dead_brain_coral_block, top_image="assets/minecraft/textures/block/dead_brain_coral_block.png")
block(blockid=ids.block_minecraft__dead_bubble_coral_block, top_image="assets/minecraft/textures/block/dead_bubble_coral_block.png")
block(blockid=ids.block_minecraft__dead_fire_coral_block, top_image="assets/minecraft/textures/block/dead_fire_coral_block.png")
block(blockid=ids.block_minecraft__dead_horn_coral_block, top_image="assets/minecraft/textures/block/dead_horn_coral_block.png")
block(blockid=ids.block_minecraft__dead_tube_coral_block, top_image="assets/minecraft/textures/block/dead_tube_coral_block.png")
block(blockid=ids.block_minecraft__purpur_block, top_image="assets/minecraft/textures/block/purpur_block.png")
block(blockid=ids.block_minecraft__end_stone_bricks, top_image="assets/minecraft/textures/block/end_stone_bricks.png")
block(blockid=ids.block_minecraft__magma_block, top_image="assets/minecraft/textures/block/magma.png")
block(blockid=ids.block_minecraft__nether_wart_block, top_image="assets/minecraft/textures/block/nether_wart_block.png")
block(blockid=ids.block_minecraft__warped_wart_block, top_image="assets/minecraft/textures/block/warped_wart_block.png")
block(blockid=ids.block_minecraft__red_nether_bricks, top_image="assets/minecraft/textures/block/red_nether_bricks.png")
block(blockid=ids.block_minecraft__white_concrete, top_image="assets/minecraft/textures/block/white_concrete.png")
block(blockid=ids.block_minecraft__orange_concrete, top_image="assets/minecraft/textures/block/orange_concrete.png")
block(blockid=ids.block_minecraft__magenta_concrete, top_image="assets/minecraft/textures/block/magenta_concrete.png")
block(blockid=ids.block_minecraft__light_blue_concrete, top_image="assets/minecraft/textures/block/light_blue_concrete.png")
block(blockid=ids.block_minecraft__yellow_concrete, top_image="assets/minecraft/textures/block/yellow_concrete.png")
block(blockid=ids.block_minecraft__lime_concrete, top_image="assets/minecraft/textures/block/lime_concrete.png")
block(blockid=ids.block_minecraft__pink_concrete, top_image="assets/minecraft/textures/block/pink_concrete.png")
block(blockid=ids.block_minecraft__gray_concrete, top_image="assets/minecraft/textures/block/gray_concrete.png")
block(blockid=ids.block_minecraft__light_gray_concrete, top_image="assets/minecraft/textures/block/light_gray_concrete.png")
block(blockid=ids.block_minecraft__cyan_concrete, top_image="assets/minecraft/textures/block/cyan_concrete.png")
block(blockid=ids.block_minecraft__purple_concrete, top_image="assets/minecraft/textures/block/purple_concrete.png")
block(blockid=ids.block_minecraft__blue_concrete, top_image="assets/minecraft/textures/block/blue_concrete.png")
block(blockid=ids.block_minecraft__brown_concrete, top_image="assets/minecraft/textures/block/brown_concrete.png")
block(blockid=ids.block_minecraft__green_concrete, top_image="assets/minecraft/textures/block/green_concrete.png")
block(blockid=ids.block_minecraft__red_concrete, top_image="assets/minecraft/textures/block/red_concrete.png")
block(blockid=ids.block_minecraft__black_concrete, top_image="assets/minecraft/textures/block/black_concrete.png")
block(blockid=ids.block_minecraft__white_concrete_powder, top_image="assets/minecraft/textures/block/white_concrete_powder.png")
block(blockid=ids.block_minecraft__orange_concrete_powder, top_image="assets/minecraft/textures/block/orange_concrete_powder.png")
block(blockid=ids.block_minecraft__magenta_concrete_powder, top_image="assets/minecraft/textures/block/magenta_concrete_powder.png")
block(blockid=ids.block_minecraft__light_blue_concrete_powder, top_image="assets/minecraft/textures/block/light_blue_concrete_powder.png")
block(blockid=ids.block_minecraft__yellow_concrete_powder, top_image="assets/minecraft/textures/block/yellow_concrete_powder.png")
block(blockid=ids.block_minecraft__lime_concrete_powder, top_image="assets/minecraft/textures/block/lime_concrete_powder.png")
block(blockid=ids.block_minecraft__pink_concrete_powder, top_image="assets/minecraft/textures/block/pink_concrete_powder.png")
block(blockid=ids.block_minecraft__gray_concrete_powder, top_image="assets/minecraft/textures/block/gray_concrete_powder.png")
block(blockid=ids.block_minecraft__light_gray_concrete_powder, top_image="assets/minecraft/textures/block/light_gray_concrete_powder.png")
block(blockid=ids.block_minecraft__cyan_concrete_powder, top_image="assets/minecraft/textures/block/cyan_concrete_powder.png")
block(blockid=ids.block_minecraft__purple_concrete_powder, top_image="assets/minecraft/textures/block/purple_concrete_powder.png")
block(blockid=ids.block_minecraft__blue_concrete_powder, top_image="assets/minecraft/textures/block/blue_concrete_powder.png")
block(blockid=ids.block_minecraft__brown_concrete_powder, top_image="assets/minecraft/textures/block/brown_concrete_powder.png")
block(blockid=ids.block_minecraft__green_concrete_powder, top_image="assets/minecraft/textures/block/green_concrete_powder.png")
block(blockid=ids.block_minecraft__red_concrete_powder, top_image="assets/minecraft/textures/block/red_concrete_powder.png")
block(blockid=ids.block_minecraft__black_concrete_powder, top_image="assets/minecraft/textures/block/black_concrete_powder.png")
block(blockid=ids.block_minecraft__dried_kelp_block, top_image="assets/minecraft/textures/block/dried_kelp_top.png", side_image="assets/minecraft/textures/block/dried_kelp_side.png")
block(blockid=ids.block_minecraft__scaffolding, top_image="assets/minecraft/textures/block/scaffolding_top.png", side_image="assets/minecraft/textures/block/scaffolding_side.png", solid=False, transparent=True)
block(blockid=ids.block_minecraft__honeycomb_block, top_image="assets/minecraft/textures/block/honeycomb_block.png")
block(blockid=ids.block_minecraft__honey_block, top_image="assets/minecraft/textures/block/honey_block_top.png", side_image="assets/minecraft/textures/block/honey_block_side.png")
block(blockid=ids.block_minecraft__ancient_debris, top_image="assets/minecraft/textures/block/ancient_debris_top.png", side_image="assets/minecraft/textures/block/ancient_debris_side.png")
block(blockid=ids.block_minecraft__blackstone, top_image="assets/minecraft/textures/block/blackstone_top.png", side_image="assets/minecraft/textures/block/blackstone.png")
block(blockid=ids.block_minecraft__netherite_block, top_image="assets/minecraft/textures/block/netherite_block.png")
block(blockid=ids.block_minecraft__polished_blackstone_bricks, top_image="assets/minecraft/textures/block/polished_blackstone_bricks.png")
block(blockid=ids.block_minecraft__polished_blackstone, top_image="assets/minecraft/textures/block/polished_blackstone.png")
block(blockid=ids.block_minecraft__gilded_blackstone, top_image="assets/minecraft/textures/block/gilded_blackstone.png")
block(blockid=ids.block_minecraft__lodestone, top_image="assets/minecraft/textures/block/lodestone_top.png", side_image="assets/minecraft/textures/block/lodestone_side.png")
block(blockid=ids.block_minecraft__crying_obsidian, top_image="assets/minecraft/textures/block/crying_obsidian.png")
block(blockid=ids.block_minecraft__target, top_image="assets/minecraft/textures/block/target_top.png", side_image="assets/minecraft/textures/block/target_side.png"),
block(blockid=ids.block_minecraft__cracked_polished_blackstone_bricks, top_image="assets/minecraft/textures/block/cracked_polished_blackstone_bricks.png"),
block(blockid=ids.block_minecraft__chiseled_polished_blackstone, top_image="assets/minecraft/textures/block/chiseled_polished_blackstone.png")
block(blockid=ids.block_minecraft__chiseled_nether_bricks, top_image="assets/minecraft/textures/block/chiseled_nether_bricks.png")
block(blockid=ids.block_minecraft__cracked_nether_bricks, top_image="assets/minecraft/textures/block/cracked_nether_bricks.png")
block(blockid=ids.block_minecraft__quartz_bricks, top_image="assets/minecraft/textures/block/quartz_bricks.png")

# simple sprite
sprite(blockid=ids.block_minecraft__oak_sapling, imagename="assets/minecraft/textures/block/oak_sapling.png")
sprite(blockid=ids.block_minecraft__spruce_sapling, imagename="assets/minecraft/textures/block/spruce_sapling.png")
sprite(blockid=ids.block_minecraft__birch_sapling, imagename="assets/minecraft/textures/block/birch_sapling.png")
sprite(blockid=ids.block_minecraft__jungle_sapling, imagename="assets/minecraft/textures/block/jungle_sapling.png")
sprite(blockid=ids.block_minecraft__acacia_sapling, imagename="assets/minecraft/textures/block/acacia_sapling.png")
sprite(blockid=ids.block_minecraft__dark_oak_sapling, imagename="assets/minecraft/textures/block/dark_oak_sapling.png")
sprite(blockid=ids.block_minecraft__bamboo_sapling, imagename="assets/minecraft/textures/block/bamboo_stage0.png")
sprite(blockid=ids.block_minecraft__fern, imagename="assets/minecraft/textures/block/fern.png")
sprite(blockid=ids.block_minecraft__dead_bush, imagename="assets/minecraft/textures/block/dead_bush.png")
sprite(blockid=ids.block_minecraft__grass, imagename="assets/minecraft/textures/block/grass.png")
sprite(blockid=ids.block_minecraft__poppy, imagename="assets/minecraft/textures/block/poppy.png")
sprite(blockid=ids.block_minecraft__blue_orchid, imagename="assets/minecraft/textures/block/blue_orchid.png")
sprite(blockid=ids.block_minecraft__allium, imagename="assets/minecraft/textures/block/allium.png")
sprite(blockid=ids.block_minecraft__azure_bluet, imagename="assets/minecraft/textures/block/azure_bluet.png")
sprite(blockid=ids.block_minecraft__red_tulip, imagename="assets/minecraft/textures/block/red_tulip.png")
sprite(blockid=ids.block_minecraft__orange_tulip, imagename="assets/minecraft/textures/block/orange_tulip.png")
sprite(blockid=ids.block_minecraft__white_tulip, imagename="assets/minecraft/textures/block/white_tulip.png")
sprite(blockid=ids.block_minecraft__pink_tulip, imagename="assets/minecraft/textures/block/pink_tulip.png")
sprite(blockid=ids.block_minecraft__oxeye_daisy, imagename="assets/minecraft/textures/block/oxeye_daisy.png")
sprite(blockid=ids.block_minecraft__dandelion, imagename="assets/minecraft/textures/block/dandelion.png")
sprite(blockid=ids.block_minecraft__wither_rose, imagename="assets/minecraft/textures/block/wither_rose.png")
sprite(blockid=ids.block_minecraft__cornflower, imagename="assets/minecraft/textures/block/cornflower.png")
sprite(blockid=ids.block_minecraft__lily_of_the_valley, imagename="assets/minecraft/textures/block/lily_of_the_valley.png")
sprite(blockid=ids.block_minecraft__brown_mushroom, imagename="assets/minecraft/textures/block/brown_mushroom.png")
sprite(blockid=ids.block_minecraft__red_mushroom, imagename="assets/minecraft/textures/block/red_mushroom.png")
sprite(blockid=ids.block_minecraft__warped_fungus, imagename="assets/minecraft/textures/block/warped_fungus.png")
sprite(blockid=ids.block_minecraft__crimson_fungus, imagename="assets/minecraft/textures/block/crimson_fungus.png")
sprite(blockid=ids.block_minecraft__warped_roots, imagename="assets/minecraft/textures/block/warped_roots.png")
sprite(blockid=ids.block_minecraft__crimson_roots, imagename="assets/minecraft/textures/block/crimson_roots.png")
sprite(blockid=ids.block_minecraft__sugar_cane, imagename="assets/minecraft/textures/block/sugar_cane.png")
sprite(blockid=ids.block_minecraft__nether_sprouts, imagename="assets/minecraft/textures/block/nether_sprouts.png")
sprite(blockid=ids.block_minecraft__dead_brain_coral, imagename="assets/minecraft/textures/block/dead_brain_coral.png")
sprite(blockid=ids.block_minecraft__dead_bubble_coral, imagename="assets/minecraft/textures/block/dead_bubble_coral.png")
sprite(blockid=ids.block_minecraft__dead_fire_coral, imagename="assets/minecraft/textures/block/dead_fire_coral.png")
sprite(blockid=ids.block_minecraft__dead_horn_coral, imagename="assets/minecraft/textures/block/dead_horn_coral.png")
sprite(blockid=ids.block_minecraft__dead_tube_coral, imagename="assets/minecraft/textures/block/dead_tube_coral.png")
sprite(blockid=ids.block_minecraft__dead_tube_coral_fan, imagename="assets/minecraft/textures/block/dead_tube_coral_fan.png")
sprite(blockid=ids.block_minecraft__dead_brain_coral_fan, imagename="assets/minecraft/textures/block/dead_brain_coral_fan.png")
sprite(blockid=ids.block_minecraft__dead_bubble_coral_fan, imagename="assets/minecraft/textures/block/dead_bubble_coral_fan.png")
sprite(blockid=ids.block_minecraft__dead_fire_coral_fan, imagename="assets/minecraft/textures/block/dead_fire_coral_fan.png")
sprite(blockid=ids.block_minecraft__dead_horn_coral_fan, imagename="assets/minecraft/textures/block/dead_horn_coral_fan.png")
sprite(blockid=ids.block_minecraft__tube_coral, imagename="assets/minecraft/textures/block/tube_coral.png")
sprite(blockid=ids.block_minecraft__brain_coral, imagename="assets/minecraft/textures/block/brain_coral.png")
sprite(blockid=ids.block_minecraft__bubble_coral, imagename="assets/minecraft/textures/block/bubble_coral.png")
sprite(blockid=ids.block_minecraft__fire_coral, imagename="assets/minecraft/textures/block/fire_coral.png")
sprite(blockid=ids.block_minecraft__horn_coral, imagename="assets/minecraft/textures/block/horn_coral.png")
sprite(blockid=ids.block_minecraft__tube_coral_fan, imagename="assets/minecraft/textures/block/tube_coral_fan.png")
sprite(blockid=ids.block_minecraft__brain_coral_fan, imagename="assets/minecraft/textures/block/brain_coral_fan.png")
sprite(blockid=ids.block_minecraft__bubble_coral_fan, imagename="assets/minecraft/textures/block/bubble_coral_fan.png")
sprite(blockid=ids.block_minecraft__fire_coral_fan, imagename="assets/minecraft/textures/block/fire_coral_fan.png")
sprite(blockid=ids.block_minecraft__horn_coral_fan, imagename="assets/minecraft/textures/block/horn_coral_fan.png")

# simple billboard
billboard(blockid=ids.block_minecraft__weeping_vines, imagename="assets/minecraft/textures/block/twisting_vines.png")
billboard(blockid=ids.block_minecraft__weeping_vines_plant, imagename="assets/minecraft/textures/block/twisting_vines_plant.png")
billboard(blockid=ids.block_minecraft__twisting_vines, imagename="assets/minecraft/textures/block/weeping_vines.png")
billboard(blockid=ids.block_minecraft__twisting_vines_plant, imagename="assets/minecraft/textures/block/weeping_vines_plant.png")


@material(blockid=ids.block_minecraft__grass_block, data=list(range(11)) + [0x10], solid=True)
def grass(self, blockid, data):
    # 0x10 bit means SNOW
    side_img = self.load_image_texture("assets/minecraft/textures/block/grass_block_side.png")
    if data & 0x10:
        side_img = self.load_image_texture("assets/minecraft/textures/block/grass_block_snow.png")
    img = self.build_block(self.load_image_texture("assets/minecraft/textures/block/grass_block_top.png"), side_img)
    if not data & 0x10:
        alpha_over(img, self.biome_grass_texture, (0, 0), self.biome_grass_texture)
    return img

@material(blockid=ids.group_fluid, data=list(range(512)), fluid=ids.group_fluid, transparent=True, nospawn=True)
def water(self, blockid, data):
    texture = self.load_water()

    def rect(tex, coords, outline=(0, 0, 0, 0), fill=(0, 0, 0, 0)):
        t = tex.copy()
        ImageDraw.Draw(t).rectangle(coords, outline=outline, fill=fill)
        return t

    level = data >> 6

    if (data & 0b10000) == 16:
        top = texture
    else:
        top = None

    if (data & 0b0001) == 1:
        side1 = texture    # top left
    else:
        side1 = None

    if (data & 0b1000) == 8:
        side2 = texture    # top right
    else:
        side2 = None

    if (data & 0b0010) == 2:
        side3 = texture    # bottom left
    else:
        side3 = None

    if (data & 0b0100) == 4:
        side4 = texture    # bottom right
    else:
        side4 = None

    # if nothing shown do not draw at all
    if top is None and side3 is None and side4 is None:
        return None

    img = self.build_full_block(None, None, None, side3, side4)

    if top:
        top = self.transform_image_top(top)
        if (data & 0b100000):
            alpha_over(img, top)
        else:
            if side3 and side4:
                alpha_over(img, top)
            else:
                alpha_over(img, top, (0, 2))

    return img

# glass, and ice (no inner surfaces)
# uses pseudo-ancildata found in iterate.c
@material(blockid=ids.group_no_inner_surfaces, data=list(range(512)), transparent=True, nospawn=True, solid=ids.group_no_inner_surfaces)
def no_inner_surfaces(self, blockid, data):
    if blockid == ids.block_minecraft__glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/glass.png")
    elif blockid == ids.block_minecraft__white_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/white_stained_glass.png")
    elif blockid == ids.block_minecraft__orange_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/orange_stained_glass.png")
    elif blockid == ids.block_minecraft__magenta_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/magenta_stained_glass.png")
    elif blockid == ids.block_minecraft__light_blue_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/light_blue_stained_glass.png")
    elif blockid == ids.block_minecraft__yellow_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/yellow_stained_glass.png")
    elif blockid == ids.block_minecraft__lime_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/lime_stained_glass.png")
    elif blockid == ids.block_minecraft__pink_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/pink_stained_glass.png")
    elif blockid == ids.block_minecraft__gray_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/gray_stained_glass.png")
    elif blockid == ids.block_minecraft__light_gray_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/light_gray_stained_glass.png")
    elif blockid == ids.block_minecraft__cyan_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/cyan_stained_glass.png")
    elif blockid == ids.block_minecraft__purple_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/purple_stained_glass.png")
    elif blockid == ids.block_minecraft__blue_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/blue_stained_glass.png")
    elif blockid == ids.block_minecraft__brown_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/brown_stained_glass.png")
    elif blockid == ids.block_minecraft__green_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/green_stained_glass.png")
    elif blockid == ids.block_minecraft__red_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/red_stained_glass.png")
    elif blockid == ids.block_minecraft__black_stained_glass:
        texture = self.load_image_texture("assets/minecraft/textures/block/black_stained_glass.png")
    elif blockid == ids.block_minecraft__ice:
        texture = self.load_image_texture("assets/minecraft/textures/block/ice.png")

    # now that we've used the lower 4 bits to get color, shift down to get the 5 bits that encode face hiding
    data = data >> 4

    if (data & 0b10000) == 16:
        top = texture
    else:
        top = None

    if (data & 0b0001) == 1:
        side1 = texture    # top left
    else:
        side1 = None

    if (data & 0b1000) == 8:
        side2 = texture    # top right
    else:
        side2 = None

    if (data & 0b0010) == 2:
        side3 = texture    # bottom left
    else:
        side3 = None

    if (data & 0b0100) == 4:
        side4 = texture    # bottom right
    else:
        side4 = None

    # if nothing shown do not draw at all
    if top is None and side3 is None and side4 is None:
        return None

    img = self.build_full_block(top, None, None, side3, side4)
    return img


@material(blockid=ids.block_minecraft__lava, data=list(range(16)), fluid=True, transparent=False, nospawn=True)
def lava(self, blockid, data):
    lavatex = self.load_lava()
    return self.build_block(lavatex, lavatex)


@material(blockid=ids.group_cube_column, data=[0, 4, 8], solid=True)
def cube_column(self, blockid, data):
    # extract orientation and wood type frorm data bits
    if self.rotation == 1:
        if data == 4:
            data = 8
        elif data == 8:
            data = 4
    elif self.rotation == 3:
        if data == 4:
            data = 8
        elif data == 8:
            data = 4

    cube_column_tex = {
        ids.block_minecraft__oak_log: ("assets/minecraft/textures/block/oak_log_top.png", "assets/minecraft/textures/block/oak_log.png"),
        ids.block_minecraft__spruce_log: ("assets/minecraft/textures/block/spruce_log_top.png", "assets/minecraft/textures/block/spruce_log.png"),
        ids.block_minecraft__birch_log: ("assets/minecraft/textures/block/birch_log_top.png", "assets/minecraft/textures/block/birch_log.png"),
        ids.block_minecraft__jungle_log: ("assets/minecraft/textures/block/jungle_log_top.png", "assets/minecraft/textures/block/jungle_log.png"),
        ids.block_minecraft__acacia_log: ("assets/minecraft/textures/block/acacia_log_top.png", "assets/minecraft/textures/block/acacia_log.png"),
        ids.block_minecraft__dark_oak_log: ("assets/minecraft/textures/block/dark_oak_log_top.png", "assets/minecraft/textures/block/dark_oak_log.png"),
        ids.block_minecraft__stripped_oak_log: ("assets/minecraft/textures/block/stripped_oak_log_top.png", "assets/minecraft/textures/block/stripped_oak_log.png"),
        ids.block_minecraft__stripped_spruce_log: ("assets/minecraft/textures/block/stripped_spruce_log_top.png", "assets/minecraft/textures/block/stripped_spruce_log.png"),
        ids.block_minecraft__stripped_birch_log: ("assets/minecraft/textures/block/stripped_birch_log_top.png", "assets/minecraft/textures/block/stripped_birch_log.png"),
        ids.block_minecraft__stripped_jungle_log: ("assets/minecraft/textures/block/stripped_jungle_log_top.png", "assets/minecraft/textures/block/stripped_jungle_log.png"),
        ids.block_minecraft__stripped_acacia_log: ("assets/minecraft/textures/block/stripped_acacia_log_top.png", "assets/minecraft/textures/block/stripped_acacia_log.png"),
        ids.block_minecraft__stripped_dark_oak_log: ("assets/minecraft/textures/block/stripped_dark_oak_log_top.png", "assets/minecraft/textures/block/stripped_dark_oak_log.png"),
        ids.block_minecraft__oak_wood: ("assets/minecraft/textures/block/oak_log.png", "assets/minecraft/textures/block/oak_log.png"),
        ids.block_minecraft__spruce_wood: ("assets/minecraft/textures/block/spruce_log.png", "assets/minecraft/textures/block/spruce_log.png"),
        ids.block_minecraft__birch_wood: ("assets/minecraft/textures/block/birch_log.png", "assets/minecraft/textures/block/birch_log.png"),
        ids.block_minecraft__jungle_wood: ("assets/minecraft/textures/block/jungle_log.png", "assets/minecraft/textures/block/jungle_log.png"),
        ids.block_minecraft__acacia_wood: ("assets/minecraft/textures/block/acacia_log.png", "assets/minecraft/textures/block/acacia_log.png"),
        ids.block_minecraft__dark_oak_wood: ("assets/minecraft/textures/block/dark_oak_log.png", "assets/minecraft/textures/block/dark_oak_log.png"),
        ids.block_minecraft__stripped_oak_wood: ("assets/minecraft/textures/block/stripped_oak_log.png", "assets/minecraft/textures/block/stripped_oak_log.png"),
        ids.block_minecraft__stripped_spruce_wood: ("assets/minecraft/textures/block/stripped_spruce_log.png", "assets/minecraft/textures/block/stripped_spruce_log.png"),
        ids.block_minecraft__stripped_birch_wood: ("assets/minecraft/textures/block/stripped_birch_log.png", "assets/minecraft/textures/block/stripped_birch_log.png"),
        ids.block_minecraft__stripped_jungle_wood: ("assets/minecraft/textures/block/stripped_jungle_log.png", "assets/minecraft/textures/block/stripped_jungle_log.png"),
        ids.block_minecraft__stripped_acacia_wood: ("assets/minecraft/textures/block/stripped_acacia_log.png", "assets/minecraft/textures/block/stripped_acacia_log.png"),
        ids.block_minecraft__stripped_dark_oak_wood: ("assets/minecraft/textures/block/stripped_dark_oak_log.png", "assets/minecraft/textures/block/stripped_dark_oak_log.png"),
        ids.block_minecraft__warped_stem: ("assets/minecraft/textures/block/warped_stem_top.png", "assets/minecraft/textures/block/warped_stem.png"),
        ids.block_minecraft__stripped_warped_stem: ("assets/minecraft/textures/block/warped_stem_top.png", "assets/minecraft/textures/block/stripped_warped_stem.png"),
        ids.block_minecraft__crimson_stem: ("assets/minecraft/textures/block/crimson_stem_top.png", "assets/minecraft/textures/block/crimson_stem.png"),
        ids.block_minecraft__stripped_crimson_stem: ("assets/minecraft/textures/block/crimson_stem_top.png", "assets/minecraft/textures/block/stripped_crimson_stem.png"),
        ids.block_minecraft__warped_hyphae: ("assets/minecraft/textures/block/warped_stem.png", "assets/minecraft/textures/block/warped_stem.png"),
        ids.block_minecraft__stripped_warped_hyphae: ("assets/minecraft/textures/block/stripped_warped_stem.png", "assets/minecraft/textures/block/stripped_warped_stem.png"),
        ids.block_minecraft__crimson_hyphae: ("assets/minecraft/textures/block/crimson_stem.png", "assets/minecraft/textures/block/crimson_stem.png"),
        ids.block_minecraft__stripped_crimson_hyphae: ("assets/minecraft/textures/block/stripped_crimson_stem.png", "assets/minecraft/textures/block/stripped_crimson_stem.png"),
        ids.block_minecraft__bone_block: ("assets/minecraft/textures/block/bone_block_top.png", "assets/minecraft/textures/block/bone_block_side.png"),
        ids.block_minecraft__quartz_pillar: ("assets/minecraft/textures/block/quartz_pillar_top.png", "assets/minecraft/textures/block/quartz_pillar.png"),
        ids.block_minecraft__hay_block: ("assets/minecraft/textures/block/hay_block_top.png", "assets/minecraft/textures/block/hay_block_side.png"),
        ids.block_minecraft__basalt: ("assets/minecraft/textures/block/basalt_top.png", "assets/minecraft/textures/block/basalt_side.png"),
        ids.block_minecraft__polished_basalt: ("assets/minecraft/textures/block/polished_basalt_top.png", "assets/minecraft/textures/block/polished_basalt_side.png"),
        ids.block_minecraft__purpur_pillar: ("assets/minecraft/textures/block/purpur_pillar_top.png", "assets/minecraft/textures/block/purpur_pillar.png"),
        # FLAG CUBE_COLUMN TEX_DICT #
        # END FLAG CUBE_COLUMN TEX_DICT #
    }

    top_f, side_f = cube_column_tex[blockid]
    if not side_f:
        side_f = top_f
    top = self.load_image_texture(top_f)
    side = self.load_image_texture(side_f)

    # choose orientation and paste textures
    if data == 0:
        return self.build_block(top, side)
    elif data == 4:  # east-west orientation
        return self.build_full_block(side.rotate(90), None, None, top, side.rotate(90))
    elif data == 8:  # north-south orientation
        return self.build_full_block(side, None, None, side.rotate(270), top)


# dispenser, dropper, furnace, blast furnace, and smoker
@material(blockid=[ids.block_minecraft__dispenser, ids.block_minecraft__furnace, ids.block_minecraft__dropper, ids.block_minecraft__blast_furnace, ids.block_minecraft__smoker], data=list(range(14)), solid=True)
def furnaces(self, blockid, data):
    # first, do the rotation if needed
    # Masked as bit 4 indicates whether the block is lit/triggered or not
    if self.rotation in [1, 2, 3] and data & 0b111 in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = data & 0b1000 | rotation_map[self.rotation][data & 0b111]

    # Rotation angles for top texture using data & 0b111 as an index
    top_rotation_map = [0, 0, 180, 0, 270, 90, 0, 0]

                   # Dispenser
    texture_map = {ids.block_minecraft__dispenser: {'top': 'furnace_top', 'side': 'furnace_side',
                           'front': 'dispenser_front', 'top_vert': 'dispenser_front_vertical'},
                   # Furnace
                   ids.block_minecraft__furnace:    {'top': 'furnace_top', 'side': 'furnace_side',
                           'front': 'furnace_front', 'front_on': 'furnace_front_on'},
                   # Dropper
                   ids.block_minecraft__dropper:   {'top': 'furnace_top', 'side': 'furnace_side',
                           'front': 'dropper_front', 'top_vert': 'dropper_front_vertical'},
                   # Blast furance
                   ids.block_minecraft__blast_furnace: {'top': 'blast_furnace_top', 'side': 'blast_furnace_side',
                           'front': 'blast_furnace_front', 'front_on': 'blast_furnace_front_on'},
                   # Smoker
                   ids.block_minecraft__smoker: {'top': 'smoker_top', 'side': 'smoker_side',
                           'front': 'smoker_front', 'front_on': 'smoker_front_on'}}

    if data & 0b111 in [0, 1] and 'top_vert' in texture_map[blockid]:
        # Block has a special top texture when it faces up/down
        # This also affects which texture is used for the sides/front
        top_name = 'top_vert' if data & 0b111 == 1 else 'top'
        side_name = 'top'
        front_name = 'top'
    else:
        top_name = 'top'
        side_name = 'side'
        # Use block's lit/on front texture if it is defined & bit 4 is set
        # Note: Some front_on texture images have multiple frames,
        #       but load_image_texture() crops this appropriately
        #       as long as the image width is 16px
        if data & 0b1000 == 8 and 'front_on' in texture_map[blockid]:
            front_name = 'front_on'
        else:
            front_name = 'front'

    top = self.load_image_texture("assets/minecraft/textures/block/%s.png" %
                                  texture_map[blockid][top_name]).copy()
    top = top.rotate(top_rotation_map[data & 0b111])
    side = self.load_image_texture("assets/minecraft/textures/block/%s.png" %
                                   texture_map[blockid][side_name])
    front = self.load_image_texture("assets/minecraft/textures/block/%s.png" %
                                    texture_map[blockid][front_name])

    if data & 0b111 == 3:  # pointing west
        return self.build_full_block(top, None, None, side, front)
    elif data & 0b111 == 4:  # pointing north
        return self.build_full_block(top, None, None, front, side)
    else:  # in any other direction the front can't be seen
        return self.build_full_block(top, None, None, side, side)

# red sandstone
@material(blockid=ids.block_minecraft__red_sandstone, data=0, solid=True)
def sandstone(self, blockid, data):
    top = self.load_image_texture("assets/minecraft/textures/block/sandstone_top.png")
    side = self.load_image_texture("assets/minecraft/textures/block/red_sandstone.png")
    return self.build_full_block(top, None, None, side, side, self.load_image_texture("assets/minecraft/textures/block/red_sandstone_bottom.png"))

# Bed
@material(blockid=ids.group_bed, data=list(range(256)), transparent=True, nospawn=True)
def bed(self, blockid, data):
    # Bits 1-2   Rotation
    # Bit 3      Occupancy, no impact on appearance
    # Bit 4      Foot/Head of bed (0 = foot, 1 = head)
    # Bits 5-8   Color NOP NOP

    # first get rotation done
    # Masked to not clobber block head/foot & color info
    data = data & 0b11111100 | ((self.rotation + (data & 0b11)) % 4)

    if blockid == ids.block_minecraft__white_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/white.png"
    elif blockid == ids.block_minecraft__orange_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/orange.png"
    elif blockid == ids.block_minecraft__magenta_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/magenta.png"
    elif blockid == ids.block_minecraft__light_blue_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/light_blue.png"
    elif blockid == ids.block_minecraft__yellow_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/yellow.png"
    elif blockid == ids.block_minecraft__lime_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/lime.png"
    elif blockid == ids.block_minecraft__pink_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/pink.png"
    elif blockid == ids.block_minecraft__gray_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/gray.png"
    elif blockid == ids.block_minecraft__light_gray_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/light_gray.png"
    elif blockid == ids.block_minecraft__cyan_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/cyan.png"
    elif blockid == ids.block_minecraft__purple_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/purple.png"
    elif blockid == ids.block_minecraft__blue_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/blue.png"
    elif blockid == ids.block_minecraft__brown_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/brown.png"
    elif blockid == ids.block_minecraft__green_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/green.png"
    elif blockid == ids.block_minecraft__red_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/red.png"
    elif blockid == ids.block_minecraft__black_bed:
        bed_tex = "assets/minecraft/textures/entity/bed/black.png"
    else:
        bed_tex = "assets/minecraft/textures/entity/bed/red.png"

    bed_texture = self.load_image(bed_tex)

    increment = 8
    left_face = None
    right_face = None
    top_face = None
    if data & 0x8 == 0x8:  # head of the bed
        top = bed_texture.copy().crop((6, 6, 22, 22))

        # Composing the side
        side = Image.new("RGBA", (16, 16), self.bgcolor)
        side_part1 = bed_texture.copy().crop((0, 6, 6, 22)).rotate(90, expand=True)
        # foot of the bed
        side_part2 = bed_texture.copy().crop((53, 3, 56, 6))
        side_part2_f = side_part2.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(side, side_part1, (0, 7), side_part1)
        alpha_over(side, side_part2, (0, 13), side_part2)

        end = Image.new("RGBA", (16, 16), self.bgcolor)
        end_part = bed_texture.copy().crop((6, 0, 22, 6)).rotate(180)
        alpha_over(end, end_part, (0, 7), end_part)
        alpha_over(end, side_part2, (0, 13), side_part2)
        alpha_over(end, side_part2_f, (13, 13), side_part2_f)
        if data & 0x03 == 0x00:    # South
            top_face = top.rotate(180)
            left_face = side.transpose(Image.FLIP_LEFT_RIGHT)
            right_face = end
        elif data & 0x03 == 0x01:  # West
            top_face = top.rotate(90)
            left_face = end
            right_face = side.transpose(Image.FLIP_LEFT_RIGHT)
        elif data & 0x03 == 0x02:  # North
            top_face = top
            left_face = side
        elif data & 0x03 == 0x03:  # East
            top_face = top.rotate(270)
            right_face = side

    else:  # foot of the bed
        top = bed_texture.copy().crop((6, 28, 22, 44))
        side = Image.new("RGBA", (16, 16), self.bgcolor)
        side_part1 = bed_texture.copy().crop((0, 28, 6, 44)).rotate(90, expand=True)
        side_part2 = bed_texture.copy().crop((53, 3, 56, 6))
        side_part2_f = side_part2.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(side, side_part1, (0, 7), side_part1)
        alpha_over(side, side_part2, (13, 13), side_part2)

        end = Image.new("RGBA", (16, 16), self.bgcolor)
        end_part = bed_texture.copy().crop((22, 22, 38, 28)).rotate(180)
        alpha_over(end, end_part, (0, 7), end_part)
        alpha_over(end, side_part2, (0, 13), side_part2)
        alpha_over(end, side_part2_f, (13, 13), side_part2_f)
        if data & 0x03 == 0x00:    # South
            top_face = top.rotate(180)
            left_face = side.transpose(Image.FLIP_LEFT_RIGHT)
        elif data & 0x03 == 0x01:  # West
            top_face = top.rotate(90)
            right_face = side.transpose(Image.FLIP_LEFT_RIGHT)
        elif data & 0x03 == 0x02:  # North
            top_face = top
            left_face = side
            right_face = end
        elif data & 0x03 == 0x03:  # East
            top_face = top.rotate(270)
            left_face = end
            right_face = side

    top_face = (top_face, increment)
    return self.build_full_block(top_face, None, None, left_face, right_face)


# powered, detector, activator and normal rails
@material(blockid=ids.group_rail, data=list(range(14)), transparent=True)
def rails(self, blockid, data):
    # first, do rotation
    # Masked to not clobber powered rail on/off info
    # Ascending and flat straight
    if self.rotation == 1:
        if (data & 0b0111) == 0:
            data = data & 0b1000 | 1
        elif (data & 0b0111) == 1:
            data = data & 0b1000 | 0
        elif (data & 0b0111) == 2:
            data = data & 0b1000 | 5
        elif (data & 0b0111) == 3:
            data = data & 0b1000 | 4
        elif (data & 0b0111) == 4:
            data = data & 0b1000 | 2
        elif (data & 0b0111) == 5:
            data = data & 0b1000 | 3
    elif self.rotation == 2:
        if (data & 0b0111) == 2:
            data = data & 0b1000 | 3
        elif (data & 0b0111) == 3:
            data = data & 0b1000 | 2
        elif (data & 0b0111) == 4:
            data = data & 0b1000 | 5
        elif (data & 0b0111) == 5:
            data = data & 0b1000 | 4
    elif self.rotation == 3:
        if (data & 0b0111) == 0:
            data = data & 0b1000 | 1
        elif (data & 0b0111) == 1:
            data = data & 0b1000 | 0
        elif (data & 0b0111) == 2:
            data = data & 0b1000 | 4
        elif (data & 0b0111) == 3:
            data = data & 0b1000 | 5
        elif (data & 0b0111) == 4:
            data = data & 0b1000 | 3
        elif (data & 0b0111) == 5:
            data = data & 0b1000 | 2
    # if blockid == 66: # normal minetrack only
    #     #Corners
    #     if self.rotation == 1:
    #         if data == 6: data = 7
    #         elif data == 7: data = 8
    #         elif data == 8: data = 6
    #         elif data == 9: data = 9
    #     elif self.rotation == 2:
    #         if data == 6: data = 8
    #         elif data == 7: data = 9
    #         elif data == 8: data = 6
    #         elif data == 9: data = 7
    #     elif self.rotation == 3:
    #         if data == 6: data = 9
    #         elif data == 7: data = 6
    #         elif data == 8: data = 8
    #         elif data == 9: data = 7
    img = Image.new("RGBA", (24, 24), self.bgcolor)

    if blockid == ids.block_minecraft__powered_rail:  # powered rail
        if data & 0x8 == 0:  # unpowered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/powered_rail.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")    # they don't exist but make the code
        # much simplier
        elif data & 0x8 == 0x8:  # powered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/powered_rail_on.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")    # leave corners for code simplicity
        # filter the 'powered' bit
        data = data & 0x7

    elif blockid == ids.block_minecraft__detector_rail:  # detector rail
        if data & 0x8 == 0:  # unpowered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/detector_rail.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")    # leave corners for code simplicity
        elif data & 0x8 == 0x8:  # powered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/detector_rail_on.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")
        # filter the 'powered' bit
        data = data & 0x7

    elif blockid == ids.block_minecraft__rail:  # normal rail
        raw_straight = self.load_image_texture("assets/minecraft/textures/block/rail.png")
        raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")

    elif blockid == ids.block_minecraft__activator_rail:  # activator rail
        if data & 0x8 == 0:  # unpowered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/activator_rail.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")    # they don't exist but make the code
        # much simplier
        elif data & 0x8 == 0x8:  # powered
            raw_straight = self.load_image_texture("assets/minecraft/textures/block/activator_rail_on.png")
            raw_corner = self.load_image_texture("assets/minecraft/textures/block/rail_corner.png")    # leave corners for code simplicity
        # filter the 'powered' bit
        data = data & 0x7

    # use transform_image to scale and shear
    if data == 0:
        track = self.transform_image_top(raw_straight)
        alpha_over(img, track, (0, 12), track)
    elif data == 6:
        track = self.transform_image_top(raw_corner)
        alpha_over(img, track, (0, 12), track)
    elif data == 7:
        track = self.transform_image_top(raw_corner.rotate(270))
        alpha_over(img, track, (0, 12), track)
    elif data == 8:
        # flip
        track = self.transform_image_top(raw_corner.transpose(Image.FLIP_TOP_BOTTOM).rotate(90))
        alpha_over(img, track, (0, 12), track)
    elif data == 9:
        track = self.transform_image_top(raw_corner.transpose(Image.FLIP_TOP_BOTTOM))
        alpha_over(img, track, (0, 12), track)
    elif data == 1:
        track = self.transform_image_top(raw_straight.rotate(90))
        alpha_over(img, track, (0, 12), track)

    # slopes
    elif data == 2:  # slope going up in +x direction
        track = self.transform_image_slope(raw_straight)
        track = track.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, track, (2, 0), track)
        # the 2 pixels move is needed to fit with the adjacent tracks

    elif data == 3:  # slope going up in -x direction
        # tracks are sprites, in this case we are seeing the "side" of
        # the sprite, so draw a line to make it beautiful.
        ImageDraw.Draw(img).line([(11, 11), (23, 17)], fill=(164, 164, 164))
        # grey from track texture (exterior grey).
        # the track doesn't start from image corners, be carefull drawing the line!
    elif data == 4:  # slope going up in -y direction
        track = self.transform_image_slope(raw_straight)
        alpha_over(img, track, (0, 0), track)

    elif data == 5:  # slope going up in +y direction
        # same as "data == 3"
        ImageDraw.Draw(img).line([(1, 17), (12, 11)], fill=(164, 164, 164))

    return img


# sticky and normal piston body
@material(blockid=[ids.block_minecraft__sticky_piston, ids.block_minecraft__piston], data=[0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13],
          transparent=True, solid=True, nospawn=True)
def piston(self, blockid, data):
    # first, rotation
    # Masked to not clobber block head/foot info
    if self.rotation in [1, 2, 3] and (data & 0b111) in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = (data & 0b1000) | rotation_map[self.rotation][data & 0b111]

    if blockid == ids.block_minecraft__sticky_piston:  # sticky
        piston_t = self.load_image_texture("assets/minecraft/textures/block/piston_top_sticky.png").copy()
    else:  # normal
        piston_t = self.load_image_texture("assets/minecraft/textures/block/piston_top.png").copy()

    # other textures
    side_t = self.load_image_texture("assets/minecraft/textures/block/piston_side.png").copy()
    back_t = self.load_image_texture("assets/minecraft/textures/block/piston_bottom.png").copy()
    interior_t = self.load_image_texture("assets/minecraft/textures/block/piston_inner.png").copy()

    if data & 0x08 == 0x08:  # pushed out, non full blocks, tricky stuff
        # remove piston texture from piston body
        ImageDraw.Draw(side_t).rectangle((0, 0, 16, 3), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))

        if data & 0x07 == 0x0:    # down
            side_t = side_t.rotate(180)
            img = self.build_full_block(back_t, None, None, side_t, side_t)
        elif data & 0x07 == 0x1:  # up
            img = self.build_full_block((interior_t, 4), None, None, side_t, side_t)
        elif data & 0x07 == 0x2:  # north
            img = self.build_full_block(side_t, None, None, side_t.rotate(90), back_t)
        elif data & 0x07 == 0x3:  # south
            img = self.build_full_block(side_t.rotate(180), None, None, side_t.rotate(270), None)
            temp = self.transform_image_side(interior_t)
            temp = temp.transpose(Image.FLIP_LEFT_RIGHT)
            alpha_over(img, temp, (9, 4), temp)
        elif data & 0x07 == 0x4:  # west
            img = self.build_full_block(side_t.rotate(90), None, None, None, side_t.rotate(270))
            temp = self.transform_image_side(interior_t)
            alpha_over(img, temp, (3, 4), temp)
        elif data & 0x07 == 0x5:  # east
            img = self.build_full_block(side_t.rotate(270), None, None, back_t, side_t.rotate(90))

    else:  # pushed in, normal full blocks, easy stuff
        if data & 0x07 == 0x0:    # down
            side_t = side_t.rotate(180)
            img = self.build_full_block(back_t, None, None, side_t, side_t)
        elif data & 0x07 == 0x1:  # up
            img = self.build_full_block(piston_t, None, None, side_t, side_t)
        elif data & 0x07 == 0x2:  # north
            img = self.build_full_block(side_t, None, None, side_t.rotate(90), back_t)
        elif data & 0x07 == 0x3:  # south
            img = self.build_full_block(side_t.rotate(180), None, None, side_t.rotate(270), piston_t)
        elif data & 0x07 == 0x4:  # west
            img = self.build_full_block(side_t.rotate(90), None, None, piston_t, side_t.rotate(270))
        elif data & 0x07 == 0x5:  # east
            img = self.build_full_block(side_t.rotate(270), None, None, back_t, side_t.rotate(90))

    return img


# sticky and normal piston shaft
@material(blockid=ids.block_minecraft__piston_head, data=[0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13], transparent=True, nospawn=True)
def piston_extension(self, blockid, data):
    # first, rotation
    # Masked to not clobber block head/foot info
    if self.rotation in [1, 2, 3] and (data & 0b111) in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = (data & 0b1000) | rotation_map[self.rotation][data & 0b111]

    if data & 0x8 == 0x8:  # sticky
        piston_t = self.load_image_texture("assets/minecraft/textures/block/piston_top_sticky.png").copy()
    else:  # normal
        piston_t = self.load_image_texture("assets/minecraft/textures/block/piston_top.png").copy()

    # other textures
    side_t = self.load_image_texture("assets/minecraft/textures/block/piston_side.png").copy()
    back_t = self.load_image_texture("assets/minecraft/textures/block/piston_top.png").copy()
    # crop piston body
    ImageDraw.Draw(side_t).rectangle((0, 4, 16, 16), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))

    # generate the horizontal piston extension stick
    h_stick = Image.new("RGBA", (24, 24), self.bgcolor)
    temp = self.transform_image_side(side_t)
    alpha_over(h_stick, temp, (1, 7), temp)
    temp = self.transform_image_top(side_t.rotate(90))
    alpha_over(h_stick, temp, (1, 1), temp)
    # Darken it
    sidealpha = h_stick.split()[3]
    h_stick = ImageEnhance.Brightness(h_stick).enhance(0.85)
    h_stick.putalpha(sidealpha)

    # generate the vertical piston extension stick
    v_stick = Image.new("RGBA", (24, 24), self.bgcolor)
    temp = self.transform_image_side(side_t.rotate(90))
    alpha_over(v_stick, temp, (12, 6), temp)
    temp = temp.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(v_stick, temp, (1, 6), temp)
    # Darken it
    sidealpha = v_stick.split()[3]
    v_stick = ImageEnhance.Brightness(v_stick).enhance(0.85)
    v_stick.putalpha(sidealpha)

    # Piston orientation is stored in the 3 first bits
    if data & 0x07 == 0x0:    # down
        side_t = side_t.rotate(180)
        img = self.build_full_block((back_t, 12), None, None, side_t, side_t)
        alpha_over(img, v_stick, (0, -3), v_stick)
    elif data & 0x07 == 0x1:  # up
        img = Image.new("RGBA", (24, 24), self.bgcolor)
        img2 = self.build_full_block(piston_t, None, None, side_t, side_t)
        alpha_over(img, v_stick, (0, 4), v_stick)
        alpha_over(img, img2, (0, 0), img2)
    elif data & 0x07 == 0x2:  # north
        img = self.build_full_block(side_t, None, None, side_t.rotate(90), None)
        temp = self.transform_image_side(back_t).transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, temp, (2, 2), temp)
        alpha_over(img, h_stick, (6, 3), h_stick)
    elif data & 0x07 == 0x3:  # south
        img = Image.new("RGBA", (24, 24), self.bgcolor)
        img2 = self.build_full_block(side_t.rotate(180), None, None, side_t.rotate(270), piston_t)
        alpha_over(img, h_stick, (0, 0), h_stick)
        alpha_over(img, img2, (0, 0), img2)
    elif data & 0x07 == 0x4:  # west
        img = self.build_full_block(side_t.rotate(90), None, None, piston_t, side_t.rotate(270))
        h_stick = h_stick.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, h_stick, (0, 0), h_stick)
    elif data & 0x07 == 0x5:  # east
        img = Image.new("RGBA", (24, 24), self.bgcolor)
        img2 = self.build_full_block(side_t.rotate(270), None, None, None, side_t.rotate(90))
        h_stick = h_stick.transpose(Image.FLIP_LEFT_RIGHT)
        temp = self.transform_image_side(back_t)
        alpha_over(img2, temp, (10, 2), temp)
        alpha_over(img, img2, (0, 0), img2)
        alpha_over(img, h_stick, (-3, 2), h_stick)

    return img


# double slabs and slabs
# these wooden slabs are unobtainable without cheating, they are still
# here because lots of pre-1.3 worlds use this blocks, add prismarine slabs
@material(blockid=ids.group_slabs, data=[0, 1], transparent=ids.group_slabs, solid=True)
def slabs(self, blockid, data):
    # if blockid == 44 or blockid == 182:
    #     texture = data & 7
    # else: # data > 8 are special double slabs
    #     texture = data
    if blockid == ids.block_minecraft__oak_slab or blockid == ids.block_minecraft__petrified_oak_slab:  # oak
        top = side = self.load_image_texture("assets/minecraft/textures/block/oak_planks.png")
    elif blockid == ids.block_minecraft__spruce_slab:  # spruce
        top = side = self.load_image_texture("assets/minecraft/textures/block/spruce_planks.png")
    elif blockid == ids.block_minecraft__birch_slab:  # birch
        top = side = self.load_image_texture("assets/minecraft/textures/block/birch_planks.png")
    elif blockid == ids.block_minecraft__jungle_slab:  # jungle
        top = side = self.load_image_texture("assets/minecraft/textures/block/jungle_planks.png")
    elif blockid == ids.block_minecraft__acacia_slab:  # acacia
        top = side = self.load_image_texture("assets/minecraft/textures/block/acacia_planks.png")
    elif blockid == ids.block_minecraft__dark_oak_slab:  # dark wood
        top = side = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png")
    elif blockid == ids.block_minecraft__crimson_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/crimson_planks.png")
    elif blockid == ids.block_minecraft__warped_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/warped_planks.png")
    elif blockid == ids.block_minecraft__stone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/stone.png")
    elif blockid == ids.block_minecraft__sandstone_slab:
        top = self.load_image_texture("assets/minecraft/textures/block/sandstone_top.png")
        side = self.load_image_texture("assets/minecraft/textures/block/sandstone.png")
    elif blockid == ids.block_minecraft__cobblestone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/cobblestone.png")
    elif blockid == ids.block_minecraft__brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/bricks.png")
    elif blockid == ids.block_minecraft__stone_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/stone_bricks.png")
    elif blockid == ids.block_minecraft__nether_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/nether_bricks.png")
    elif blockid == ids.block_minecraft__quartz_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/quartz_block_side.png")
    elif blockid == ids.block_minecraft__red_sandstone_slab:
        top = self.load_image_texture("assets/minecraft/textures/block/red_sandstone_top.png")
        side = self.load_image_texture("assets/minecraft/textures/block/red_sandstone.png")
    elif blockid == ids.block_minecraft__purpur_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/purpur_block.png")
    elif blockid == ids.block_minecraft__prismarine_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/prismarine.png")
    elif blockid == ids.block_minecraft__dark_prismarine_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/dark_prismarine.png")
    elif blockid == ids.block_minecraft__prismarine_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/prismarine_bricks.png")
    elif blockid == ids.block_minecraft__andesite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/andesite.png")
    elif blockid == ids.block_minecraft__diorite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/diorite.png")
    elif blockid == ids.block_minecraft__granite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/granite.png")
    elif blockid == ids.block_minecraft__polished_andesite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/polished_andesite.png")
    elif blockid == ids.block_minecraft__polished_diorite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/polished_diorite.png")
    elif blockid == ids.block_minecraft__polished_granite_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/polished_granite.png")
    elif blockid == ids.block_minecraft__red_nether_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/red_nether_bricks.png")
    elif blockid == ids.block_minecraft__smooth_sandstone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/sandstone_top.png")
    elif blockid == ids.block_minecraft__cut_sandstone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/cut_sandstone.png")
    elif blockid == ids.block_minecraft__smooth_red_sandstone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/red_sandstone_top.png")
    elif blockid == ids.block_minecraft__cut_red_sandstone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/cut_red_sandstone.png")
    elif blockid == ids.block_minecraft__end_stone_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/end_stone_bricks.png")
    elif blockid == ids.block_minecraft__mossy_cobblestone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/mossy_cobblestone.png")
    elif blockid == ids.block_minecraft__mossy_stone_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/mossy_stone_bricks.png")
    elif blockid == ids.block_minecraft__smooth_quartz_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/quartz_block_bottom.png")
    elif blockid == ids.block_minecraft__smooth_stone_slab:
        top = self.load_image_texture("assets/minecraft/textures/block/smooth_stone.png")
        side = self.load_image_texture("assets/minecraft/textures/block/smooth_stone_slab_side.png")
    elif blockid == ids.block_minecraft__polished_blackstone_brick_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/polished_blackstone_bricks.png")
    elif blockid == ids.block_minecraft__blackstone_slab:
        top = self.load_image_texture("assets/minecraft/textures/block/blackstone_top.png")
        side = self.load_image_texture("assets/minecraft/textures/block/blackstone.png")
    elif blockid == ids.block_minecraft__polished_blackstone_slab:
        top = side = self.load_image_texture("assets/minecraft/textures/block/polished_blackstone.png")

    # if blockid == 43 or blockid == 181 or blockid == 204: # double slab
    #     return self.build_block(top, side)

    return self.build_slab_block(top, side, data == 1)


# torch, redstone torch (off), redstone torch(on)
@material(blockid=ids.group_torch, data=[0, 1, 2, 3, 4, 8, 9, 10, 11, 12], transparent=True)
def torches(self, blockid, data):
    img = None
    # first, rotations
    on = (data & 0b1000 == 8)
    data = data & 0b111
    if self.rotation == 1:
        if data == 1:
            data = 3
        elif data == 2:
            data = 4
        elif data == 3:
            data = 2
        elif data == 4:
            data = 1
    elif self.rotation == 2:
        if data == 1:
            data = 2
        elif data == 2:
            data = 1
        elif data == 3:
            data = 4
        elif data == 4:
            data = 3
    elif self.rotation == 3:
        if data == 1:
            data = 4
        elif data == 2:
            data = 3
        elif data == 3:
            data = 1
        elif data == 4:
            data = 2

    # # choose the proper texture
    # if blockid == ids.block_minecraft__torch or blockid == ids.block_minecraft__wall_torch:  # torch
    #     small = self.load_image_texture("assets/minecraft/textures/block/torch.png")
    # elif blockid == ids.block_minecraft__redstone_wall_torch or blockid == ids.block_minecraft__redstone_torch:  # off redstone torch
    #     small = self.load_image_texture("assets/minecraft/textures/block/redstone_torch_off.png")
    # else:  # on redstone torch
    #     small = self.load_image_texture("assets/minecraft/textures/block/redstone_torch.png")

    if blockid == ids.block_minecraft__redstone_torch or blockid == ids.block_minecraft__redstone_wall_torch:
        if on:
            small = self.load_image_texture("assets/minecraft/textures/block/redstone_torch.png")
        else:
            small = self.load_image_texture("assets/minecraft/textures/block/redstone_torch_off.png")

    elif blockid == ids.block_minecraft__torch or blockid == ids.block_minecraft__wall_torch:
        small = self.load_image_texture("assets/minecraft/textures/block/torch.png")

    elif blockid == ids.block_minecraft__soul_torch or blockid == ids.block_minecraft__soul_wall_torch:   
        small = self.load_image_texture("assets/minecraft/textures/block/soul_torch.png")

    # compose a torch bigger than the normal
    # (better for doing transformations)
    torch = Image.new("RGBA", (16, 16), self.bgcolor)
    alpha_over(torch, small, (-4, -3))
    alpha_over(torch, small, (-5, -2))
    alpha_over(torch, small, (-3, -2))

    # angle of inclination of the texture
    rotation = 15

    if data == 1:  # pointing south
        torch = torch.rotate(-rotation, Image.NEAREST)  # nearest filter is more nitid.
        img = self.build_full_block(None, None, None, torch, None, None)

    elif data == 2:  # pointing north
        torch = torch.rotate(rotation, Image.NEAREST)
        img = self.build_full_block(None, None, torch, None, None, None)

    elif data == 3:  # pointing west
        torch = torch.rotate(rotation, Image.NEAREST)
        img = self.build_full_block(None, torch, None, None, None, None)

    elif data == 4:  # pointing east
        torch = torch.rotate(-rotation, Image.NEAREST)
        img = self.build_full_block(None, None, None, None, torch, None)

    if blockid == ids.block_minecraft__torch or blockid == ids.block_minecraft__redstone_torch or img is None:  # standing on the floor
        # compose a "3d torch".
        img = Image.new("RGBA", (24, 24), self.bgcolor)

        small_crop = small.crop((2, 2, 14, 14))
        slice = small_crop.copy()
        ImageDraw.Draw(slice).rectangle((6, 0, 12, 12), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
        ImageDraw.Draw(slice).rectangle((0, 0, 4, 12), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))

        alpha_over(img, slice, (7, 5))
        alpha_over(img, small_crop, (6, 6))
        alpha_over(img, small_crop, (7, 6))
        alpha_over(img, slice, (7, 7))

    return img


# lantern
@material(blockid=ids.block_minecraft__lantern, data=[0, 1], transparent=True)
def lantern(self, blockid, data):
    # get the  multipart texture of the lantern
    inputtexture = self.load_image_texture("assets/minecraft/textures/block/lantern.png")

    # # now create a textures, using the parts defined in lantern.json

    # JSON data for sides
    # from": [ 5,  1,  5 ],
    #  "to": [11,  8, 11 ],
    # { "uv": [ 0, 2, 6,  9 ], "texture": "#all" }

    side_crop = inputtexture.crop((0, 2, 6, 9))
    side_slice = side_crop.copy()
    side_texture = Image.new("RGBA", (16, 16), self.bgcolor)
    side_texture.paste(side_slice, (5, 8))

    # JSON data for top
    # { "uv": [  0, 9,  6, 15 ], "texture": "#all" }
    top_crop = inputtexture.crop((0, 9, 6, 15))
    top_slice = top_crop.copy()
    top_texture = Image.new("RGBA", (16, 16), self.bgcolor)
    top_texture.paste(top_slice,(5, 5))

    # mimic parts of build_full_block, to get an object smaller than a block 
    # build_full_block(self, top, side1, side2, side3, side4, bottom=None):
    # a non transparent block uses top, side 3 and side 4.
    img = Image.new("RGBA", (24, 24), self.bgcolor)
    # prepare the side textures
    # side3
    side3 = self.transform_image_side(side_texture)
    # Darken this side
    sidealpha = side3.split()[3]
    side3 = ImageEnhance.Brightness(side3).enhance(0.9)
    side3.putalpha(sidealpha)
    # place the transformed texture
    hangoff = 0
    if data == 1:
        hangoff = 8
    xoff = 4
    yoff =- hangoff
    alpha_over(img, side3, (xoff+0, yoff+6), side3)
    # side4
    side4 = self.transform_image_side(side_texture)
    side4 = side4.transpose(Image.FLIP_LEFT_RIGHT)
    # Darken this side
    sidealpha = side4.split()[3]
    side4 = ImageEnhance.Brightness(side4).enhance(0.8)
    side4.putalpha(sidealpha)
    alpha_over(img, side4, (12-xoff, yoff+6), side4)
    # top
    top = self.transform_image_top(top_texture)
    alpha_over(img, top, (0, 8-hangoff), top)
    return img

# bamboo
@material(blockid=ids.block_minecraft__bamboo, transparent=True)
def bamboo(self, blockid, data):
    # get the  multipart texture of the lantern
    inputtexture = self.load_image_texture("assets/minecraft/textures/block/bamboo_stalk.png")

    # # now create a textures, using the parts defined in bamboo1_age0.json
        # {   "from": [ 7, 0, 7 ],
        #     "to": [ 9, 16, 9 ],
        #     "faces": {
        #         "down":  { "uv": [ 13, 4, 15, 6 ], "texture": "#all", "cullface": "down" },
        #         "up":    { "uv": [ 13, 0, 15, 2], "texture": "#all", "cullface": "up" },
        #         "north": { "uv": [ 0, 0, 2, 16 ], "texture": "#all" },
        #         "south": { "uv": [ 0, 0, 2, 16 ], "texture": "#all" },
        #         "west":  { "uv": [  0, 0, 2, 16 ], "texture": "#all" },
        #         "east":  { "uv": [  0, 0, 2, 16 ], "texture": "#all" }
        #     }
        # }

    side_crop = inputtexture.crop((0, 0, 3, 16))
    side_slice = side_crop.copy()
    side_texture = Image.new("RGBA", (16, 16), self.bgcolor)
    side_texture.paste(side_slice,(0, 0))

    # JSON data for top
    # "up":    { "uv": [ 13, 0, 15, 2], "texture": "#all", "cullface": "up" },
    top_crop = inputtexture.crop((13, 0, 16, 3))
    top_slice = top_crop.copy()
    top_texture = Image.new("RGBA", (16, 16), self.bgcolor)
    top_texture.paste(top_slice,(5, 5))

    # mimic parts of build_full_block, to get an object smaller than a block 
    # build_full_block(self, top, side1, side2, side3, side4, bottom=None):
    # a non transparent block uses top, side 3 and side 4.
    img = Image.new("RGBA", (24, 24), self.bgcolor)
    # prepare the side textures
    # side3
    side3 = self.transform_image_side(side_texture)
    # Darken this side
    sidealpha = side3.split()[3]
    side3 = ImageEnhance.Brightness(side3).enhance(0.9)
    side3.putalpha(sidealpha)
    # place the transformed texture
    xoff = 3
    yoff = 0
    alpha_over(img, side3, (4+xoff, yoff), side3)
    # side4
    side4 = self.transform_image_side(side_texture)
    side4 = side4.transpose(Image.FLIP_LEFT_RIGHT)
    # Darken this side
    sidealpha = side4.split()[3]
    side4 = ImageEnhance.Brightness(side4).enhance(0.8)
    side4.putalpha(sidealpha)
    alpha_over(img, side4, (-4+xoff, yoff), side4)
    # top
    top = self.transform_image_top(top_texture)
    alpha_over(img, top, (-4+xoff, -5), top)
    return img

# composter
@material(blockid=ids.block_minecraft__composter, data=list(range(9)), transparent=True)
def composter(self, blockid, data):
    side = self.load_image_texture("assets/minecraft/textures/block/composter_side.png")
    top = self.load_image_texture("assets/minecraft/textures/block/composter_top.png")
    # bottom = self.load_image_texture("assets/minecraft/textures/block/composter_bottom.png")

    if data == 0:  # empty
        return self.build_full_block(top, side, side, side, side)

    if data == 8:
        compost = self.transform_image_top(
            self.load_image_texture("assets/minecraft/textures/block/composter_ready.png"))
    else:
        compost = self.transform_image_top(
            self.load_image_texture("assets/minecraft/textures/block/composter_compost.png"))

    nudge = {1: (0, 9), 2: (0, 8), 3: (0, 7), 4: (0, 6), 5: (0, 4), 6: (0, 2), 7: (0, 0), 8: (0, 0)}

    img = self.build_full_block(None, side, side, None, None)
    alpha_over(img, compost, nudge[data], compost)
    img2 = self.build_full_block(top, None, None, side, side)
    alpha_over(img, img2, (0, 0), img2)
    return img

# fire
@material(blockid=ids.block_minecraft__fire, data=list(range(16)), transparent=True)
def fire(self, blockid, data):
    firetextures = self.load_fire()
    side1 = self.transform_image_side(firetextures[0])
    side2 = self.transform_image_side(firetextures[1]).transpose(Image.FLIP_LEFT_RIGHT)
    
    img = Image.new("RGBA", (24,24), self.bgcolor)

    alpha_over(img, side1, (12,0), side1)
    alpha_over(img, side2, (0,0), side2)

    alpha_over(img, side1, (0,6), side1)
    alpha_over(img, side2, (12,6), side2)
    
    return img

@material(blockid=ids.group_stairs, data=[10, 11, 18, 19, 34, 35, 66, 67, 14, 15, 12, 13, 22, 23, 20, 21, 38, 39, 36, 37, 70, 71, 68, 69], transparent=True, solid=True, nospawn=True)
def stairs(self, blockid, data):

    # data & 1 --> top
    # not(data & 1) --> bottom
    # (data & 7) >> 1 == 1 --> straight shape
    # (data & 7) >> 1 == 2 --> outer_right shape
    # (data & 7) >> 1 == 3 --> inner_right shape
    # data & 8 --> north facing
    # data & 16 --> east facing
    # data & 32 --> south facing
    # data & 64 --> west facing

    def rect(tex, coords, outline=(0, 0, 0, 0), fill=(0, 0, 0, 0)):
        t = tex.copy()
        ImageDraw.Draw(t).rectangle(coords, outline=outline, fill=fill)
        return t

    def removeAlphaSup100(img):
        t = numpy.array(img)
        tAlpha = t[:, :, 3]
        t[numpy.all([tAlpha > 100, tAlpha < 255], axis=0), 3] = 255
        return Image.fromarray(t)

    # slide only the facing bits self.rotation times
    for i in range(0, self.rotation):
        # take only facing bits 4 bits
        dirData = data >> 3
        # dirData >> 1 move bits on the left
        # ((dirData & 0b1) << 3)) the first bit goto last bit
        # << 3 add 3 0 bits
        # (data & 7) add all other bits at the same place
        data = (((dirData >> 1) + ((dirData & 0b1) << 3)) << 3) + (data & 7)

    stair_id_to_tex = {
        ids.block_minecraft__oak_stairs: "assets/minecraft/textures/block/oak_planks.png",
        ids.block_minecraft__cobblestone_stairs: "assets/minecraft/textures/block/cobblestone.png",
        ids.block_minecraft__brick_stairs: "assets/minecraft/textures/block/bricks.png",
        ids.block_minecraft__stone_brick_stairs: "assets/minecraft/textures/block/stone_bricks.png",
        ids.block_minecraft__nether_brick_stairs: "assets/minecraft/textures/block/nether_bricks.png",
        ids.block_minecraft__sandstone_stairs: "assets/minecraft/textures/block/sandstone.png",
        ids.block_minecraft__spruce_stairs: "assets/minecraft/textures/block/spruce_planks.png",
        ids.block_minecraft__birch_stairs: "assets/minecraft/textures/block/birch_planks.png",
        ids.block_minecraft__jungle_stairs: "assets/minecraft/textures/block/jungle_planks.png",
        ids.block_minecraft__quartz_stairs: "assets/minecraft/textures/block/quartz_block_side.png",
        ids.block_minecraft__acacia_stairs: "assets/minecraft/textures/block/acacia_planks.png",
        ids.block_minecraft__dark_oak_stairs: "assets/minecraft/textures/block/dark_oak_planks.png",
        ids.block_minecraft__red_sandstone_stairs: "assets/minecraft/textures/block/red_sandstone.png",
        ids.block_minecraft__purpur_stairs: "assets/minecraft/textures/block/purpur_block.png",
        ids.block_minecraft__prismarine_stairs: "assets/minecraft/textures/block/prismarine.png",
        ids.block_minecraft__dark_prismarine_stairs: "assets/minecraft/textures/block/dark_prismarine.png",
        ids.block_minecraft__prismarine_brick_stairs: "assets/minecraft/textures/block/prismarine_bricks.png",
        ids.block_minecraft__mossy_stone_brick_stairs: "assets/minecraft/textures/block/mossy_stone_bricks.png",
        ids.block_minecraft__mossy_cobblestone_stairs: "assets/minecraft/textures/block/mossy_cobblestone.png",
        ids.block_minecraft__smooth_sandstone_stairs: "assets/minecraft/textures/block/sandstone_top.png",
        ids.block_minecraft__smooth_quartz_stairs: "assets/minecraft/textures/block/quartz_block_side.png",
        ids.block_minecraft__polished_granite_stairs: "assets/minecraft/textures/block/polished_granite.png",
        ids.block_minecraft__polished_diorite_stairs: "assets/minecraft/textures/block/polished_diorite.png",
        ids.block_minecraft__polished_andesite_stairs: "assets/minecraft/textures/block/polished_andesite.png",
        ids.block_minecraft__stone_stairs: "assets/minecraft/textures/block/stone.png",
        ids.block_minecraft__granite_stairs: "assets/minecraft/textures/block/granite.png",
        ids.block_minecraft__diorite_stairs: "assets/minecraft/textures/block/diorite.png",
        ids.block_minecraft__andesite_stairs: "assets/minecraft/textures/block/andesite.png",
        ids.block_minecraft__end_stone_brick_stairs: "assets/minecraft/textures/block/end_stone_bricks.png",
        ids.block_minecraft__red_nether_brick_stairs: "assets/minecraft/textures/block/red_nether_bricks.png",
        ids.block_minecraft__smooth_red_sandstone_stairs: "assets/minecraft/textures/block/red_sandstone_top.png",
        ids.block_minecraft__crimson_stairs: "assets/minecraft/textures/block/crimson_planks.png",
        ids.block_minecraft__warped_stairs: "assets/minecraft/textures/block/warped_planks.png",
        ids.block_minecraft__blackstone_stairs: "assets/minecraft/textures/block/blackstone.png",
        ids.block_minecraft__polished_blackstone_brick_stairs: "assets/minecraft/textures/block/polished_blackstone_bricks.png",
        ids.block_minecraft__polished_blackstone_stairs: "assets/minecraft/textures/block/polished_blackstone.png",
    }

    # sandstone, red sandstone, and quartz stairs have special top texture
    special_tops = {
        ids.block_minecraft__sandstone_stairs: "assets/minecraft/textures/block/sandstone_top.png",
        ids.block_minecraft__quartz_stairs: "assets/minecraft/textures/block/quartz_block_top.png",
        ids.block_minecraft__red_sandstone_stairs: "assets/minecraft/textures/block/red_sandstone_top.png",
        ids.block_minecraft__smooth_quartz_stairs: "assets/minecraft/textures/block/quartz_block_top.png",
    }

    # Texture: 12
    #          34

    dict_textures = {}
    dict_textures[0] = self.load_image_texture(stair_id_to_tex[blockid]).copy()

    dict_textures_top = {}
    dict_textures_top[0] = self.load_image_texture(special_tops[blockid]).copy() if blockid in special_tops.keys() else self.load_image_texture(stair_id_to_tex[blockid]).copy()

    for dict_texture in [dict_textures, dict_textures_top]:
        # Straight
        dict_texture[12] = rect(dict_texture[0], coords=(0, 8, 15, 15))  # 12
        dict_texture[34] = rect(dict_texture[0], coords=(0, 0, 15, 7))  # 34
        dict_texture[13] = rect(dict_texture[0], coords=(8, 0, 15, 15))  # 13
        dict_texture[24] = rect(dict_texture[0], coords=(0, 0, 7, 15))  # 24
        # Inner
        dict_texture[123] = rect(dict_texture[0], coords=(8, 8, 15, 15))  # 123
        dict_texture[124] = rect(dict_texture[0], coords=(0, 8, 7, 15))  # 124
        dict_texture[234] = rect(dict_texture[0], coords=(0, 0, 7, 7))  # 234
        dict_texture[134] = rect(dict_texture[0], coords=(8, 0, 15, 7))  # 134
        # Outer
        dict_texture[1] = rect(rect(dict_texture[0], coords=(8, 0, 15, 7)), coords=(0, 8, 15, 15))  # 1
        dict_texture[2] = rect(rect(dict_texture[0], coords=(0, 0, 7, 7)), coords=(0, 8, 15, 15))  # 2
        dict_texture[3] = rect(rect(dict_texture[0], coords=(0, 0, 15, 7)), coords=(8, 8, 15, 15))  # 3
        dict_texture[4] = rect(rect(dict_texture[0], coords=(0, 0, 15, 7)), coords=(0, 8, 7, 15))  # 4

    top_out = None
    top_in = None
    front_out = None
    front_in = None
    side_left_out = None
    side_left_in = None
    back_out = None
    back_in = None
    side_right_out = None
    side_right_in = None
    inBlock = None

    sides = {"top": None, "front": None, "side_left": None, "side_right": None, "back": None, "in": None}
    orderKeySide = ["in", "front", "side_left", "top"]

    if (data & 7) >> 1 == 1:  # --> Straight shape
        if data & 1:  # --> top
            top_out = dict_textures[0]

            if data & 8:  # --> north facing
                front_out = dict_textures[12]
                front_in = dict_textures[34]
                side_left_out = dict_textures[123]

            elif data & 16:  # --> east facing
                front_out = dict_textures[123]
                side_left_out = dict_textures[12]
                side_left_in = dict_textures[34]
                orderKeySide = ["top", "side_left", "front"]

            elif data & 32:  # --> south facing
                front_out = dict_textures[0]
                side_left_out = dict_textures[124]

            elif data & 64:  # --> west facing
                front_out = dict_textures[124]
                side_left_out = dict_textures[0]

        else:  # --> bottom
            if data & 8:  # --> north facing
                top_out = dict_textures[12]
                top_in = dict_textures[34]
                front_out = dict_textures[34]
                front_in = dict_textures[12]
                side_left_out = dict_textures[134]

            elif data & 16:  # --> east facing
                top_out = dict_textures[24]
                top_in = dict_textures[13]
                front_out = dict_textures[134]
                side_left_out = dict_textures[34]
                side_left_in = dict_textures[12]

            elif data & 32:  # --> south facing
                top_out = dict_textures[34]
                top_in = dict_textures[12]
                front_out = dict_textures[0]
                side_left_out = dict_textures[234]
                orderKeySide = ["top", "front", "side_left"]

            elif data & 64:  # --> west facing
                top_out = dict_textures[13]
                top_in = dict_textures[24]
                front_out = dict_textures[234]
                side_left_out = dict_textures[0]
                orderKeySide = ["top", "front", "side_left"]

    elif (data & 7) >> 1 == 2:  # --> outer_right shape
        if data & 1:  # --> top
            top_out = dict_textures[0]
            if data & 8:  # --> north facing
                front_out = dict_textures[12]
                side_left_out = dict_textures[12]
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                front_in = ImageOps.mirror(self.transform_image_side(dict_textures[3]))
                side_left_in = self.transform_image_side(dict_textures[3])
                front_in = ImageEnhance.Brightness(front_in).enhance(0.85)
                side_left_in = ImageEnhance.Brightness(side_left_in).enhance(0.85)
                alpha_over(inBlock, side_left_in, (6, 3))
                alpha_over(inBlock, front_in, (6, 3))
                front_in = None
                side_left_in = None

                orderKeySide = ["in", "top", "side_left", "front"]

            elif data & 16:  # --> east facing
                front_out = dict_textures[123]
                side_left_out = dict_textures[12]
                side_left_in = dict_textures[4]
                orderKeySide = ["top", "side_left", "front"]

            elif data & 32:  # --> south facing
                front_out = dict_textures[124]
                side_left_out = dict_textures[124]

            elif data & 64:  # --> west facing
                front_out = dict_textures[12]
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                front_in = ImageOps.mirror(self.transform_image_side(dict_textures[4]))
                front_in = ImageEnhance.Brightness(front_in).enhance(0.85)
                alpha_over(inBlock, front_in, (6, 3))
                front_in = None
                side_left_out = dict_textures[123]

        else:
            if data & 8:  # --> north facing
                top_out = dict_textures[2]
                top_in = dict_textures[134]
                front_out = dict_textures[34]
                front_in = dict_textures[1]
                side_left_out = dict_textures[34]
                side_left_in = dict_textures[1]

            elif data & 16:  # --> east facing
                top_out = dict_textures[4]
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                top_in = self.transform_image_top(dict_textures[123])
                alpha_over(inBlock, top_in, (0, 6))
                front_out = dict_textures[134]
                side_left_out = dict_textures[34]
                side_left_in = dict_textures[2]
                top_in = None

            elif data & 32:  # --> south facing
                top_out = dict_textures[3]
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                top_in = self.transform_image_top(dict_textures[124])
                alpha_over(inBlock, top_in, (0, 6))
                front_out = dict_textures[234]
                side_left_out = dict_textures[234]
                top_in = None

            elif data & 64:  # --> west facing
                top_out = dict_textures[1]
                top_in = dict_textures[234]
                front_out = dict_textures[34]
                front_in = dict_textures[2]
                side_left_out = dict_textures[134]
                side_left_in = None

                orderKeySide = ["top", "front", "side_left"]

    elif (data & 7) >> 1 == 3:  # --> inner_right shape
        if data & 1:  # --> top
            top_out = dict_textures[0]
            if data & 8:  # --> north facing
                front_out = dict_textures[123]
                side_left_out = dict_textures[123]
                front_in = ImageOps.mirror(self.transform_image_side(dict_textures[4]))
                side_left_in = self.transform_image_side(dict_textures[4])
                if data & 1:  # --> top
                    front_in = ImageEnhance.Brightness(front_in).enhance(0.85)
                    side_left_in = ImageEnhance.Brightness(side_left_in).enhance(0.85)
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                alpha_over(inBlock, front_in, (6, 3))
                alpha_over(inBlock, side_left_in, (6, 3))
                front_in = None
                side_left_in = None

                orderKeySide = ["in", "top", "side_left", "front"]

            elif data & 16:  # --> east facing
                front_out = dict_textures[0]
                side_left_out = dict_textures[124]

            elif data & 32:  # --> south facing
                front_out = dict_textures[0]
                side_left_out = dict_textures[0]

            elif data & 64:  # --> west facing
                front_out = dict_textures[124]
                side_left_out = dict_textures[0]

        else:
            if data & 8:  # --> north facing
                front_out = dict_textures[134]
                front_in = dict_textures[2]
                side_left_out = dict_textures[134]
                side_left_in = dict_textures[2]
                top_out = dict_textures[124]
                top_in = dict_textures[3]
                orderKeySide = ["top", "side_left", "front"]

            elif data & 16:  # --> east facing
                front_out = dict_textures[0]
                side_left_out = dict_textures[234]
                top_out = dict_textures[234]
                top_in = self.transform_image_top(dict_textures[1])
                side_left_in = self.transform_image_side(dict_textures[1])
                if data & 1:  # --> top
                    side_left_in = ImageEnhance.Brightness(side_left_in).enhance(0.85)
                inBlock = Image.new("RGBA", (24, 24), self.bgcolor)
                alpha_over(inBlock, top_in, (0, 6))
                alpha_over(inBlock, side_left_in, (6, 3))
                top_in = None
                side_left_in = None

                orderKeySide = ["in", "side_left", "top", "front"]

            elif data & 32:  # --> south facing
                top_out = dict_textures[134]
                front_out = dict_textures[0]
                side_left_out = dict_textures[0]

            elif data & 64:  # --> west facing
                top_out = dict_textures[123]
                top_in = dict_textures[4]
                front_out = dict_textures[234]
                side_left_out = dict_textures[0]

                orderKeySide = ["top", "side_left", "front"]

    if inBlock:
        sides["in"] = inBlock

    sides["top"] = Image.new("RGBA", (24, 24), self.bgcolor)
    if top_out:
        top_out = self.transform_image_top(top_out)
        # Make Top face: merge top_out and top_in
        alpha_over(sides["top"], top_out, (0, 0))
    if top_in:
        top_in = self.transform_image_top(top_in)
        alpha_over(sides["top"], top_in, (0, 6))

    sides["front"] = Image.new("RGBA", (24, 24), self.bgcolor)
    if front_out:
        front_out = ImageOps.mirror(self.transform_image_side(front_out))
        # Make front face: merge front_out and front_in
        alpha_over(sides["front"], front_out, (12, 6))
    if front_in:
        front_in = ImageOps.mirror(self.transform_image_side(front_in))
        alpha_over(sides["front"], front_in, (6, 3))
    sides["front"] = ImageEnhance.Brightness(sides["front"]).enhance(0.85)

    sides["side_left"] = Image.new("RGBA", (24, 24), self.bgcolor)
    if side_left_out:
        side_left_out = self.transform_image_side(side_left_out)
        # Make side left face: merge side_left_out and side_left_in
        alpha_over(sides["side_left"], side_left_out, (0, 6))
    if side_left_in:
        side_left_in = self.transform_image_side(side_left_in)
        if data & 1:  # --> top
            side_left_in = ImageEnhance.Brightness(side_left_in).enhance(0.9)
        alpha_over(sides["side_left"], side_left_in, (6, 3))
    sides["side_left"] = ImageEnhance.Brightness(sides["side_left"]).enhance(0.85)

    sides["side_right"] = Image.new("RGBA", (24, 24), self.bgcolor)
    if side_right_out:
        # Make side right face: merge side_right_out and side_right_in
        alpha_over(sides["side_right"], side_right_out, (11, 0))
    if side_right_in:
        alpha_over(sides["side_right"], side_right_in, (11, 0))

    sides["back"] = Image.new("RGBA", (24, 24), self.bgcolor)
    if back_out:
        # Make back face: merge back_out and back_in
        back_out = ImageOps.mirror(self.transform_image_side(back_out))
        alpha_over(sides["back"], back_out, (0, 0))
    if back_in:
        back_in = ImageOps.mirror(self.transform_image_side(back_in))
        alpha_over(sides["back"], back_in, (0, 0))

    block = Image.new("RGBA", (24, 24), self.bgcolor)

    for side in orderKeySide:
        if sides[side]:
            alpha_over(block, sides[side], (0, 0))

    block = removeAlphaSup100(block)

    return block


# normal, locked (used in april's fool day), ender and trapped chest
# NOTE:  locked chest used to be id95 (which is now stained glass)
@material(blockid=ids.group_chest, data=list(range(30)), transparent = True)
def chests(self, blockid, data):
    # the first 3 bits are the orientation as stored in minecraft, 
    # bits 0x8 and 0x10 indicate which half of the double chest is it.

    # first, do the rotation if needed
    orientation_data = data & 7
    if self.rotation == 1:
        if orientation_data == 2:
            data = 5 | (data & 24)
        elif orientation_data == 3:
            data = 4 | (data & 24)
        elif orientation_data == 4:
            data = 2 | (data & 24)
        elif orientation_data == 5:
            data = 3 | (data & 24)
    elif self.rotation == 2:
        if orientation_data == 2:
            data = 3 | (data & 24)
        elif orientation_data == 3:
            data = 2 | (data & 24)
        elif orientation_data == 4:
            data = 5 | (data & 24)
        elif orientation_data == 5:
            data = 4 | (data & 24)
    elif self.rotation == 3:
        if orientation_data == 2:
            data = 4 | (data & 24)
        elif orientation_data == 3:
            data = 5 | (data & 24)
        elif orientation_data == 4:
            data = 3 | (data & 24)
        elif orientation_data == 5:
            data = 2 | (data & 24)

    if blockid == 130 and data not in [2, 3, 4, 5]:
        return None
        # iterate.c will only return the ancil data (without pseudo
        # ancil data) for locked and ender chests, so only
        # ancilData = 2,3,4,5 are used for this blockids

    if data & 24 == 0:
        if blockid == 130:
            t = self.load_image("assets/minecraft/textures/entity/chest/ender.png")
        else:
            try:
                t = self.load_image("assets/minecraft/textures/entity/chest/normal.png")
            except (TextureException, IOError):
                t = self.load_image("assets/minecraft/textures/entity/chest/chest.png")

        t = ImageOps.flip(t) # for some reason the 1.15 images are upside down

        # the textures is no longer in terrain.png, get it from
        # item/chest.png and get by cropping all the needed stuff
        if t.size != (64, 64): t = t.resize((64, 64), Image.ANTIALIAS)
        # top
        top = t.crop((28, 50, 42, 64))
        top.load() # every crop need a load, crop is a lazy operation
                   # see PIL manual
        img = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(img, top, (1, 1))
        top = img
        # front
        front_top = t.crop((42, 45, 56, 50))
        front_top.load()
        front_bottom = t.crop((42, 21, 56, 31))
        front_bottom.load()
        front_lock = t.crop((1, 59, 3, 63))
        front_lock.load()
        front = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(front, front_top, (1, 1))
        alpha_over(front, front_bottom, (1, 5))
        alpha_over(front, front_lock, (7, 3))
        # left side
        # left side, right side, and back are essentially the same for
        # the default texture, we take it anyway just in case other
        # textures make use of it.
        side_l_top = t.crop((14, 45, 28, 50))
        side_l_top.load()
        side_l_bottom = t.crop((14, 21, 28, 31))
        side_l_bottom.load()
        side_l = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(side_l, side_l_top, (1, 1))
        alpha_over(side_l, side_l_bottom, (1, 5))
        # right side
        side_r_top = t.crop((28, 45, 42, 50))
        side_r_top.load()
        side_r_bottom = t.crop((28, 21, 42, 31))
        side_r_bottom.load()
        side_r = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(side_r, side_r_top, (1, 1))
        alpha_over(side_r, side_r_bottom, (1, 5))
        # back
        back_top = t.crop((0, 45, 14, 50))
        back_top.load()
        back_bottom = t.crop((0, 21, 14, 31))
        back_bottom.load()
        back = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(back, back_top, (1, 1))
        alpha_over(back, back_bottom, (1, 5))

    else:
        # large chest
        # the textures is no longer in terrain.png, get it from 
        # item/chest.png and get all the needed stuff
        t_left = self.load_image("assets/minecraft/textures/entity/chest/normal_left.png")
        t_right = self.load_image("assets/minecraft/textures/entity/chest/normal_right.png")
        # for some reason the 1.15 images are upside down
        t_left = ImageOps.flip(t_left)
        t_right = ImageOps.flip(t_right)

        # Top
        top_left = t_right.crop((29, 50, 44, 64))
        top_left.load()
        top_right = t_left.crop((29, 50, 44, 64))
        top_right.load()

        top = Image.new("RGBA", (32, 16), self.bgcolor)
        alpha_over(top,top_left, (1, 1))
        alpha_over(top,top_right, (16, 1))

        # Front
        front_top_left = t_left.crop((43, 45, 58, 50))
        front_top_left.load()
        front_top_right = t_right.crop((43, 45, 58, 50))
        front_top_right.load()

        front_bottom_left = t_left.crop((43, 21, 58, 31))
        front_bottom_left.load()
        front_bottom_right = t_right.crop((43, 21, 58, 31))
        front_bottom_right.load()

        front_lock = t_left.crop((1, 59, 3, 63))
        front_lock.load()

        front = Image.new("RGBA", (32, 16), self.bgcolor)
        alpha_over(front, front_top_left, (1, 1))
        alpha_over(front, front_top_right, (16, 1))
        alpha_over(front, front_bottom_left, (1, 5))
        alpha_over(front, front_bottom_right, (16, 5))
        alpha_over(front, front_lock, (15, 3))

        # Back
        back_top_left = t_right.crop((14, 45, 29, 50))
        back_top_left.load()
        back_top_right = t_left.crop((14, 45, 29, 50))
        back_top_right.load()

        back_bottom_left = t_right.crop((14, 21, 29, 31))
        back_bottom_left.load()
        back_bottom_right = t_left.crop((14, 21, 29, 31))
        back_bottom_right.load()

        back = Image.new("RGBA", (32, 16), self.bgcolor)
        alpha_over(back, back_top_left, (1, 1))
        alpha_over(back, back_top_right, (16, 1))
        alpha_over(back, back_bottom_left, (1, 5))
        alpha_over(back, back_bottom_right, (16, 5))
        
        # left side
        side_l_top = t_left.crop((29, 45, 43, 50))
        side_l_top.load()
        side_l_bottom = t_left.crop((29, 21, 43, 31))
        side_l_bottom.load()
        side_l = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(side_l, side_l_top, (1, 1))
        alpha_over(side_l, side_l_bottom, (1, 5))
        # right side
        side_r_top = t_right.crop((0, 45, 14, 50))
        side_r_top.load()
        side_r_bottom = t_right.crop((0, 21, 14, 31))
        side_r_bottom.load()
        side_r = Image.new("RGBA", (16, 16), self.bgcolor)
        alpha_over(side_r, side_r_top, (1, 1))
        alpha_over(side_r, side_r_bottom, (1, 5))

        # double chest, left half
        if ((data & 24 == 8 and data & 7 in [3, 5]) or (data & 24 == 16 and data & 7 in [2, 4])):
            top = top.crop((0, 0, 16, 16))
            top.load()
            front = front.crop((0, 0, 16, 16))
            front.load()
            back = back.crop((0, 0, 16, 16))
            back.load()
            #~ side = side_l

        # double chest, right half
        elif ((data & 24 == 16 and data & 7 in [3, 5]) or (data & 24 == 8 and data & 7 in [2, 4])):
            top = top.crop((16, 0, 32, 16))
            top.load()
            front = front.crop((16, 0, 32, 16))
            front.load()
            back = back.crop((16, 0, 32, 16))
            back.load()
            #~ side = side_r

        else: # just in case
            return None

    # compose the final block
    img = Image.new("RGBA", (24, 24), self.bgcolor)
    if data & 7 == 2: # north
        side = self.transform_image_side(side_r)
        alpha_over(img, side, (1, 7))
        back = self.transform_image_side(back)
        alpha_over(img, back.transpose(Image.FLIP_LEFT_RIGHT), (11, 7))
        front = self.transform_image_side(front)
        top = self.transform_image_top(top.rotate(180))
        alpha_over(img, top, (0, 2))

    elif data & 7 == 3: # south
        side = self.transform_image_side(side_l)
        alpha_over(img, side, (1, 7))
        front = self.transform_image_side(front).transpose(Image.FLIP_LEFT_RIGHT)
        top = self.transform_image_top(top.rotate(180))
        alpha_over(img, top, (0, 2))
        alpha_over(img, front, (11, 7))

    elif data & 7 == 4: # west
        side = self.transform_image_side(side_r)
        alpha_over(img, side.transpose(Image.FLIP_LEFT_RIGHT), (11, 7))
        front = self.transform_image_side(front)
        alpha_over(img, front, (1, 7))
        top = self.transform_image_top(top.rotate(270))
        alpha_over(img, top, (0, 2))

    elif data & 7 == 5: # east
        back = self.transform_image_side(back)
        side = self.transform_image_side(side_l).transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, side, (11, 7))
        alpha_over(img, back, (1, 7))
        top = self.transform_image_top(top.rotate(270))
        alpha_over(img, top, (0, 2))
        
    else: # just in case
        img = None

    return img


# redstone wire
# uses pseudo-ancildata found in iterate.c
@material(blockid=ids.block_minecraft__redstone_wire, data=[0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 21, 32, 33, 34, 35, 36, 37, 40, 41, 42, 43, 44, 45, 48, 49, 50, 51, 52, 53, 64, 65, 66, 67, 68, 69, 72, 73, 74, 75, 76, 77, 80, 81, 82, 83, 84, 85, 128, 129, 130, 131, 132, 133, 136, 137, 138, 139, 140, 141, 144, 145, 146, 147, 148, 149, 160, 161, 162, 163, 164, 165, 168, 169, 170, 171, 172, 173, 176, 177, 178, 179, 180, 181, 192, 193, 194, 195, 196, 197, 200, 201, 202, 203, 204, 205, 208, 209, 210, 211, 212, 213, 256, 257, 258, 259, 260, 261, 264, 265, 266, 267, 268, 269, 272, 273, 274, 275, 276, 277, 288, 289, 290, 291, 292, 293, 296, 297, 298, 299, 300, 301, 304, 305, 306, 307, 308, 309, 320, 321, 322, 323, 324, 325, 328, 329, 330, 331, 332, 333, 336, 337, 338, 339, 340, 341], transparent=True)
def wire(self, blockid, data):

    redstone_cross_t = None
    if data & 0b1 == 1:  # powered redstone wire
        redstone_wire_t = self.load_image_texture("assets/minecraft/textures/block/redstone_dust_line0.png").rotate(90)
        redstone_wire_t = self.tint_texture(redstone_wire_t, (255, 0, 0))
        redstone_dust_t = self.load_image_texture("assets/minecraft/textures/block/redstone_dust_dot.png")
        redstone_dust_t = self.tint_texture(redstone_dust_t, (255, 0, 0))

    else:  # unpowered redstone wire
        redstone_wire_t = self.load_image_texture("assets/minecraft/textures/block/redstone_dust_line0.png").rotate(90)
        redstone_wire_t = self.tint_texture(redstone_wire_t, (48, 0, 0))
        redstone_dust_t = self.load_image_texture("assets/minecraft/textures/block/redstone_dust_dot.png")
        redstone_dust_t = self.tint_texture(redstone_dust_t, (48, 0, 0))

    # data & 2 --> east side
    # data & 8 --> north side
    # data & 32 --> west side
    # data & 128 --> south side
    # data & 4 --> east up
    # data & 16 --> north up
    # data & 64 --> west up
    # data & 256 --> south up

    # [0, 270, 180, 90][self.rotation]
    for i in range(0, self.rotation):
        dirData = data >> 1
        data = (((dirData >> 2) + ((dirData & 0b11) << 6)) << 1) + (data & 0b1)

    has_e_up = bool(data & 4)
    has_n_up = bool(data & 16)
    has_w_up = bool(data & 64)
    has_s_up = bool(data & 256)
    has_e = bool(data & 2 or has_e_up)
    has_n = bool(data & 8 or has_n_up)
    has_w = bool(data & 32 or has_w_up)
    has_s = bool(data & 128 or has_s_up)

    if has_n and has_e and has_w and has_s:
        redstone_cross_t = redstone_dust_t.copy()
        alpha_over(redstone_cross_t, redstone_wire_t.copy())
        alpha_over(redstone_cross_t, redstone_wire_t.copy().rotate(90))
        bottom = redstone_cross_t.copy()

    elif has_n and has_s and not(has_w or has_e):
        bottom = redstone_wire_t.copy().rotate(90)

    elif has_w and has_e and not(has_n or has_s):
        bottom = redstone_wire_t.copy()

    else:
        bottom = redstone_dust_t.copy()
        if not redstone_cross_t:
            redstone_cross_t = redstone_dust_t.copy()
            alpha_over(redstone_cross_t, redstone_wire_t.copy())
            alpha_over(redstone_cross_t, redstone_wire_t.copy().rotate(90))
        if has_e:
            branch_right = redstone_cross_t.copy()
            ImageDraw.Draw(branch_right).rectangle((0, 0, 15, 4), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_right).rectangle((0, 0, 4, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_right).rectangle((0, 11, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            alpha_over(bottom, branch_right.copy())
        if has_n:
            # generate an image per redstone direction
            branch_top = redstone_cross_t.copy()
            ImageDraw.Draw(branch_top).rectangle((0, 0, 4, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_top).rectangle((11, 0, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_top).rectangle((0, 11, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            alpha_over(bottom, branch_top.copy())
        if has_s:
            branch_bottom = redstone_cross_t.copy()
            ImageDraw.Draw(branch_bottom).rectangle((0, 0, 15, 4), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_bottom).rectangle((0, 0, 4, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_bottom).rectangle((11, 0, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            alpha_over(bottom, branch_bottom.copy())
        if has_w:
            branch_left = redstone_cross_t.copy()
            ImageDraw.Draw(branch_left).rectangle((0, 0, 15, 4), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_left).rectangle((11, 0, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            ImageDraw.Draw(branch_left).rectangle((0, 11, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
            alpha_over(bottom, branch_left.copy())

    side1 = None
    side2 = None
    if has_e_up:
        side2 = redstone_wire_t.rotate(90)
    if has_n_up:
        side1 = redstone_wire_t.rotate(90)

    img = self.build_full_block(None, side1, side2, None, None, bottom)

    return img


# Table blocks with no facing or other properties where sides are not all the same
# Includes: Crafting table, fletching table, cartography table, smithing table
@material(blockid=ids.group_table, solid=True, nodata=True)
def block_table(self, blockid, data):
    block_name = {ids.block_minecraft__crafting_table: "crafting_table",
                  ids.block_minecraft__fletching_table: "fletching_table",
                  ids.block_minecraft__cartography_table: "cartography_table",
                  ids.block_minecraft__smithing_table: "smithing_table"}[blockid]

    # Top texture doesn't vary with self.rotation, but texture rotation does
    top_tex = block_name + "_top"
    top_rot = [0, 270, 180, 90][self.rotation]

    # List of side textures from side 1 to 4 for each blockid
    side_tex_map = {ids.block_minecraft__crafting_table: ["front", "side", "front", "side"],
                    ids.block_minecraft__fletching_table: ["front", "side", "side", "front"],
                    ids.block_minecraft__cartography_table: ["side3", "side3", "side2", "side1"],
                    ids.block_minecraft__smithing_table: ["front", "side", "side", "front"]}[blockid]
    # Determine which side textures to use
    side3_id = [2, 3, 1, 0][self.rotation]
    side4_id = [3, 1, 0, 2][self.rotation]
    side3_tex = block_name + "_" + side_tex_map[side3_id]
    side4_tex = block_name + "_" + side_tex_map[side4_id]

    tex_path = "assets/minecraft/textures/block"
    top = self.load_image_texture("{}/{}.png".format(tex_path, top_tex)).copy()
    side3 = self.load_image_texture("{}/{}.png".format(tex_path, side3_tex))
    side4 = self.load_image_texture("{}/{}.png".format(tex_path, side4_tex)).copy()
    top = top.rotate(top_rot)
    side4 = side4.transpose(Image.FLIP_LEFT_RIGHT)

    return self.build_full_block(top, None, None, side3, side4, None)


@material(blockid=ids.block_minecraft__lectern, data=list(range(8)), transparent=True, solid=True, nospawn=True)
def lectern(self, blockid, data):
    # Do rotation, mask to not clobber book data
    data = data & 0b100 | ((self.rotation + (data & 0b11)) % 4)

    # Load textures
    base_raw_t = self.load_image_texture("assets/minecraft/textures/block/lectern_base.png")
    front_raw_t = self.load_image_texture("assets/minecraft/textures/block/lectern_front.png")
    side_raw_t = self.load_image_texture("assets/minecraft/textures/block/lectern_sides.png")
    top_raw_t = self.load_image_texture("assets/minecraft/textures/block/lectern_top.png")

    def create_tile(img_src, coord_crop, coord_paste, rot):
        # Takes an image, crops a region, optionally rotates the
        #   texture, then finally pastes it onto a 16x16 image
        img_out = Image.new("RGBA", (16, 16), self.bgcolor)
        img_in = img_src.crop(coord_crop)
        if rot != 0:
            img_in = img_in.rotate(rot, expand=True)
        img_out.paste(img_in, coord_paste)
        return img_out

    def darken_image(img_src, darken_value):
        # Takes an image & alters the brightness, leaving alpha intact
        alpha = img_src.split()[3]
        img_out = ImageEnhance.Brightness(img_src).enhance(darken_value)
        img_out.putalpha(alpha)
        return img_out

    # Generate base
    base_top_t = base_raw_t.rotate([0, 270, 180, 90][data & 0b11])
    # Front & side textures are one pixel taller than they should be
    #   pre-transformation as otherwise the topmost row of pixels
    #   post-transformation are rather transparent, which results in
    #   a visible gap between the base's sides & top
    base_front_t = create_tile(base_raw_t, (0, 13, 16, 16), (0, 13), 0)
    base_side_t = create_tile(base_raw_t, (0, 5, 16, 8), (0, 13), 0)
    base_side3_t = base_front_t if data & 0b11 == 1 else base_side_t
    base_side4_t = base_front_t if data & 0b11 == 0 else base_side_t
    img = self.build_full_block((base_top_t, 14), None, None, base_side3_t, base_side4_t, None)

    # Generate central pillar
    side_flip_t = side_raw_t.transpose(Image.FLIP_LEFT_RIGHT)
    # Define parameters used to obtain the texture for each side
    pillar_param = [{'img': front_raw_t, 'crop': (8, 4, 16, 16), 'paste': (4, 2), 'rot': 0},    # South
                    {'img': side_raw_t, 'crop': (2, 8, 15, 16), 'paste': (4, 1), 'rot': 270},  # West
                    {'img': front_raw_t, 'crop': (0, 4, 8, 13), 'paste': (4, 5), 'rot': 0},    # North
                    {'img': side_flip_t, 'crop': (2, 8, 15, 16), 'paste': (4, 1), 'rot': 90}]   # East
    # Determine which sides are rendered
    pillar_side = [pillar_param[(3 - (data & 0b11)) % 4], pillar_param[(2 - (data & 0b11)) % 4]]

    pillar_side3_t = create_tile(pillar_side[0]['img'], pillar_side[0]['crop'],
                                 pillar_side[0]['paste'], pillar_side[0]['rot'])
    pillar_side4_t = create_tile(pillar_side[1]['img'], pillar_side[1]['crop'],
                                 pillar_side[1]['paste'], pillar_side[1]['rot'])
    pillar_side4_t = pillar_side4_t.transpose(Image.FLIP_LEFT_RIGHT)
    pillar_side3_t = self.transform_image_side(pillar_side3_t)
    pillar_side3_t = darken_image(pillar_side3_t, 0.9)
    pillar_side4_t = self.transform_image_side(pillar_side4_t).transpose(Image.FLIP_LEFT_RIGHT)
    pillar_side4_t = darken_image(pillar_side4_t, 0.8)
    alpha_over(img, pillar_side3_t, (3, 4), pillar_side3_t)
    alpha_over(img, pillar_side4_t, (9, 4), pillar_side4_t)

    # Generate stand
    if (data & 0b11) in [0, 1]:  # South, West
        stand_side3_t = create_tile(side_raw_t, (0, 0, 16, 4), (0, 4), 0)
        stand_side4_t = create_tile(side_raw_t, (0, 4, 13, 8), (0, 0), -22.5)
    else:  # North, East
        stand_side3_t = create_tile(side_raw_t, (0, 4, 16, 8), (0, 0), 0)
        stand_side4_t = create_tile(side_raw_t, (0, 4, 13, 8), (0, 0), 22.5)

    stand_side3_t = self.transform_image_angle(stand_side3_t, math.radians(22.5))
    stand_side3_t = darken_image(stand_side3_t, 0.9)
    stand_side4_t = self.transform_image_side(stand_side4_t).transpose(Image.FLIP_LEFT_RIGHT)
    stand_side4_t = darken_image(stand_side4_t, 0.8)
    stand_top_t = create_tile(top_raw_t, (0, 1, 16, 14), (0, 1), 0)
    if data & 0b100:
        # Lectern has a book, modify the stand top texture
        book_raw_t = self.load_image("assets/minecraft/textures/entity/enchanting_table_book.png")
        book_t = Image.new("RGBA", (14, 10), self.bgcolor)
        book_part_t = book_raw_t.crop((0, 0, 7, 10))  # Left cover
        alpha_over(stand_top_t, book_part_t, (1, 3), book_part_t)
        book_part_t = book_raw_t.crop((15, 0, 22, 10))  # Right cover
        alpha_over(stand_top_t, book_part_t, (8, 3))
        book_part_t = book_raw_t.crop((24, 10, 29, 18)).rotate(180)  # Left page
        alpha_over(stand_top_t, book_part_t, (3, 4), book_part_t)
        book_part_t = book_raw_t.crop((29, 10, 34, 18)).rotate(180)  # Right page
        alpha_over(stand_top_t, book_part_t, (8, 4), book_part_t)

    # Perform affine transformation
    transform_matrix = numpy.matrix(numpy.identity(3))
    if (data & 0b11) in [0, 1]:  # South, West
        # Translate: 8 -X, 8 -Y
        transform_matrix *= numpy.matrix([[1, 0, 8], [0, 1, 8], [0, 0, 1]])
        # Rotate 40 degrees clockwise
        tc = math.cos(math.radians(40))
        ts = math.sin(math.radians(40))
        transform_matrix *= numpy.matrix([[tc, ts, 0], [-ts, tc, 0], [0, 0, 1]])
        # Shear in the Y direction
        tt = math.tan(math.radians(10))
        transform_matrix *= numpy.matrix([[1, 0, 0], [tt, 1, 0], [0, 0, 1]])
        # Scale to 70% height & 110% width
        transform_matrix *= numpy.matrix([[1 / 1.1, 0, 0], [0, 1 / 0.7, 0], [0, 0, 1]])
        # Translate: 12 +X, 8 +Y
        transform_matrix *= numpy.matrix([[1, 0, -12], [0, 1, -8], [0, 0, 1]])
    else:  # North, East
        # Translate: 8 -X, 8 -Y
        transform_matrix *= numpy.matrix([[1, 0, 8], [0, 1, 8], [0, 0, 1]])
        # Shear in the X direction
        tt = math.tan(math.radians(25))
        transform_matrix *= numpy.matrix([[1, tt, 0], [0, 1, 0], [0, 0, 1]])
        # Scale to 80% height
        transform_matrix *= numpy.matrix([[1, 0, 0], [0, 1 / 0.8, 0], [0, 0, 1]])
        # Rotate 220 degrees clockwise
        tc = math.cos(math.radians(40 + 180))
        ts = math.sin(math.radians(40 + 180))
        transform_matrix *= numpy.matrix([[tc, ts, 0], [-ts, tc, 0], [0, 0, 1]])
        # Scale to 60% height
        transform_matrix *= numpy.matrix([[1, 0, 0], [0, 1 / 0.6, 0], [0, 0, 1]])
        # Translate: +13 X, +7 Y
        transform_matrix *= numpy.matrix([[1, 0, -13], [0, 1, -7], [0, 0, 1]])

    transform_matrix = numpy.array(transform_matrix)[:2, :].ravel().tolist()
    stand_top_t = stand_top_t.transform((24, 24), Image.AFFINE, transform_matrix)

    img_stand = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(img_stand, stand_side3_t, (-4, 2), stand_side3_t)  # Fix some holes
    alpha_over(img_stand, stand_side3_t, (-3, 3), stand_side3_t)
    alpha_over(img_stand, stand_side4_t, (12, 5), stand_side4_t)
    alpha_over(img_stand, stand_top_t, (0, 0), stand_top_t)
    # Flip the stand if North or South facing
    if (data & 0b11) in [0, 2]:
        img_stand = img_stand.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(img, img_stand, (0, -2), img_stand)

    return img


@material(blockid=ids.block_minecraft__loom, data=list(range(4)), solid=True)
def loom(self, blockid, data):
    # Do rotation
    data = (self.rotation + data) % 4

    top_rot = [180, 90, 0, 270][data]
    side3_tex = "front" if data == 1 else "side"
    side4_tex = "front" if data == 0 else "side"

    tex_path = "assets/minecraft/textures/block"
    top = self.load_image_texture("{}/loom_top.png".format(tex_path)).copy()
    side3 = self.load_image_texture("{}/loom_{}.png".format(tex_path, side3_tex))
    side4 = self.load_image_texture("{}/loom_{}.png".format(tex_path, side4_tex)).copy()
    top = top.rotate(top_rot)
    side4 = side4.transpose(Image.FLIP_LEFT_RIGHT)

    return self.build_full_block(top, None, None, side3, side4, None)


@material(blockid=ids.block_minecraft__stonecutter, data=list(range(4)), transparent=True, solid=True, nospawn=True)
def stonecutter(self, blockid, data):
    # Do rotation
    data = (self.rotation + data) % 4

    top_t = self.load_image_texture("assets/minecraft/textures/block/stonecutter_top.png").copy()
    side_t = self.load_image_texture("assets/minecraft/textures/block/stonecutter_side.png")
    # Stonecutter saw texture contains multiple tiles, since it's
    #   16px wide rely on load_image_texture() to crop appropriately
    blade_t = self.load_image_texture("assets/minecraft/textures/block/stonecutter_saw.png").copy()

    top_t = top_t.rotate([180, 90, 0, 270][data])
    img = self.build_full_block((top_t, 7), None, None, side_t, side_t, None)

    # Add saw blade
    if data in [0, 2]:
        blade_t = blade_t.transpose(Image.FLIP_LEFT_RIGHT)
    blade_t = self.transform_image_side(blade_t)
    if data in [0, 2]:
        blade_t = blade_t.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(img, blade_t, (6, -4), blade_t)

    return img


@material(blockid=ids.block_minecraft__grindstone, data=list(range(12)), transparent=True, solid=True, nospawn=True)
def grindstone(self, blockid, data):
    # Do rotation, mask to not clobber mounting info
    data = data & 0b1100 | ((self.rotation + (data & 0b11)) % 4)

    # Load textures
    side_raw_t = self.load_image_texture("assets/minecraft/textures/block/grindstone_side.png").copy()
    round_raw_t = self.load_image_texture("assets/minecraft/textures/block/grindstone_round.png").copy()
    pivot_raw_t = self.load_image_texture("assets/minecraft/textures/block/grindstone_pivot.png").copy()
    leg_raw_t = self.load_image_texture("assets/minecraft/textures/block/dark_oak_log.png").copy()

    def create_tile(img_src, coord_crop, coord_paste,  scale):
        # Takes an image, crops a region, optionally scales the
        #   texture, then finally pastes it onto a 16x16 image
        img_out = Image.new("RGBA", (16, 16), self.bgcolor)
        img_in = img_src.crop(coord_crop)
        if scale >= 0 and scale != 1:
            w, h = img_in.size
            img_in = img_in.resize((int(w * scale), int(h * scale)), Image.NEAREST)
        img_out.paste(img_in, coord_paste)
        return img_out

    # Set variables defining positions of various parts
    wall_mounted = bool(data & 0b0100)
    rot_leg = [0, 270, 0][data >> 2]
    if wall_mounted:
        pos_leg = (32, 28) if data & 0b11 in [2, 3] else (10, 18)
        coord_leg = [(0, 0), (-10, -1), (2, 3)]
        offset_final = [(2, 1), (-2, 1), (-2, -1), (2, -1)][data & 0b11]
    else:
        pos_leg = [(22, 31), (22, 9)][data >> 3]
        coord_leg = [(0, 0), (-1, 2), (-2, -3)]
        offset_final = (0, 2 * (data >> 2) - 1)

    # Create parts
    # Scale up small parts like pivot & leg to avoid ugly results
    #   when shearing & combining parts, then scale down to original
    #   size just before final image composition
    scale_factor = 2
    side_t = create_tile(side_raw_t, (0, 0, 12, 12), (2, 0), 1)
    round_ud_t = create_tile(round_raw_t, (0, 0, 8, 12), (4, 2), 1)
    round_lr_t = create_tile(round_raw_t, (0, 0, 8, 12), (4, 0), 1)
    pivot_outer_t = create_tile(pivot_raw_t, (0, 0, 6, 6), (2, 2), scale_factor)
    pivot_lr_t = create_tile(pivot_raw_t, (6, 0, 8, 6), (2, 2), scale_factor)
    pivot_ud_t = create_tile(pivot_raw_t, (8, 0, 10, 6), (2, 2), scale_factor)
    leg_outer_t = create_tile(leg_raw_t, (6, 9, 10, 16), (2, 2), scale_factor).rotate(rot_leg)
    leg_lr_t = create_tile(leg_raw_t, (12, 9, 14, 16), (2, 2), scale_factor).rotate(rot_leg)
    leg_ud_t = create_tile(leg_raw_t, (2, 6, 4, 10), (2, 2), scale_factor)

    # Transform to block sides & tops
    side_t = self.transform_image_side(side_t)
    round_ud_t = self.transform_image_top(round_ud_t)
    round_lr_t = self.transform_image_side(round_lr_t).transpose(Image.FLIP_LEFT_RIGHT)
    pivot_outer_t = self.transform_image_side(pivot_outer_t)
    pivot_lr_t = self.transform_image_side(pivot_lr_t).transpose(Image.FLIP_LEFT_RIGHT)
    pivot_ud_t = self.transform_image_top(pivot_ud_t)
    leg_outer_t = self.transform_image_side(leg_outer_t)
    if wall_mounted:
        leg_lr_t = self.transform_image_top(leg_lr_t).transpose(Image.FLIP_LEFT_RIGHT)
        leg_ud_t = self.transform_image_side(leg_ud_t).transpose(Image.FLIP_LEFT_RIGHT)
    else:
        leg_lr_t = self.transform_image_side(leg_lr_t).transpose(Image.FLIP_LEFT_RIGHT)
        leg_ud_t = self.transform_image_top(leg_ud_t)

    # Compose leg texture
    img_leg = Image.new("RGBA", (24 * scale_factor, 24 * scale_factor), self.bgcolor)
    alpha_over(img_leg, leg_outer_t, coord_leg[0], leg_outer_t)
    alpha_over(img_leg, leg_lr_t, coord_leg[1], leg_lr_t)
    alpha_over(img_leg, leg_ud_t, coord_leg[2], leg_ud_t)

    # Compose pivot texture (& combine with leg)
    img_pivot = Image.new("RGBA", (24 * scale_factor, 24 * scale_factor), self.bgcolor)
    alpha_over(img_pivot, pivot_ud_t, (20, 18), pivot_ud_t)
    alpha_over(img_pivot, pivot_lr_t, (23, 24), pivot_lr_t)  # Fix gaps between face edges
    alpha_over(img_pivot, pivot_lr_t, (24, 24), pivot_lr_t)
    alpha_over(img_pivot, img_leg, pos_leg, img_leg)
    alpha_over(img_pivot, pivot_outer_t, (21, 21), pivot_outer_t)
    if hasattr(Image, "LANCZOS"):   # workaround for older Pillow
        img_pivot = img_pivot.resize((24, 24), Image.LANCZOS)
    else:
        img_pivot = img_pivot.resize((24, 24))

    # Combine leg, side, round & pivot
    img = Image.new("RGBA", (24, 24), self.bgcolor)
    img_final = img.copy()
    alpha_over(img, img_pivot, (1, -5), img_pivot)
    alpha_over(img, round_ud_t, (0, 2), round_ud_t)  # Fix gaps between face edges
    alpha_over(img, side_t, (3, 6), side_t)
    alpha_over(img, round_ud_t, (0, 1), round_ud_t)
    alpha_over(img, round_lr_t, (10, 6), round_lr_t)
    alpha_over(img, img_pivot, (-5, -1), img_pivot)
    if (data & 0b11) in [1, 3]:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(img_final, img, offset_final, img)

    return img_final


# crops with 8 data values (like wheat)
@material(blockid=ids.group_age_8, data=list(range(8)), transparent=True, nospawn=True)
def crops8(self, blockid, data):

    if blockid == ids.block_minecraft__wheat:
        raw_tex = "assets/minecraft/textures/block/wheat_stage%d.png" % data
    else:
        stage = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3}[data]
        if blockid == ids.block_minecraft__carrots:  # carrots
            raw_tex = "assets/minecraft/textures/block/carrots_stage%d.png" % stage
        else:  # potatoes
            raw_tex = "assets/minecraft/textures/block/potatoes_stage%d.png" % stage

    raw_crop = self.load_image_texture(raw_tex)

    crop1 = self.transform_image_top(raw_crop)
    crop2 = self.transform_image_side(raw_crop)
    crop3 = crop2.transpose(Image.FLIP_LEFT_RIGHT)

    img = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(img, crop1, (0, 12), crop1)
    alpha_over(img, crop2, (6, 3), crop2)
    alpha_over(img, crop3, (6, 3), crop3)
    return img


# farmland and grass path (15/16 blocks)
@material(blockid=[ids.block_minecraft__farmland, ids.block_minecraft__grass_path], data=list(range(2)), solid=True, transparent=True, nospawn=True)
def farmland(self, blockid, data):
    if blockid == ids.block_minecraft__farmland:
        side = self.load_image_texture("assets/minecraft/textures/block/dirt.png").copy()
        if data == 0:
            top = self.load_image_texture("assets/minecraft/textures/block/farmland.png")
        else:
            top = self.load_image_texture("assets/minecraft/textures/block/farmland_moist.png")
        # dirt.png is 16 pixels tall, so we need to crop it before building full block
        side = side.crop((0, 1, 16, 16))
    else:
        top = self.load_image_texture("assets/minecraft/textures/block/grass_path_top.png")
        side = self.load_image_texture("assets/minecraft/textures/block/grass_path_side.png")
        # side already has 1 transparent pixel at the top, so it doesn't need to be modified
        # just shift the top image down 1 pixel

    return self.build_full_block((top, 1), side, side, side, side)


# signposts
@material(blockid=ids.group_sign, data=list(range(16)), transparent=True)
def signpost(self, blockid, data):

    # first rotations
    if self.rotation == 1:
        data = (data + 4) % 16
    elif self.rotation == 2:
        data = (data + 8) % 16
    elif self.rotation == 3:
        data = (data + 12) % 16

    sign_texture = {
        # (texture on sign, texture on stick)
        ids.block_minecraft__oak_sign: ("oak_planks.png", "oak_log.png"),
        ids.block_minecraft__spruce_sign: ("spruce_planks.png", "spruce_log.png"),
        ids.block_minecraft__birch_sign: ("birch_planks.png", "birch_log.png"),
        ids.block_minecraft__acacia_sign: ("jungle_planks.png", "jungle_log.png"),
        ids.block_minecraft__jungle_sign: ("acacia_planks.png", "acacia_log.png"),
        ids.block_minecraft__dark_oak_sign: ("dark_oak_planks.png", "dark_oak_log.png"),
        ids.block_minecraft__crimson_sign: ("crimson_planks.png", "crimson_stem.png"),
        ids.block_minecraft__warped_sign: ("warped_planks.png", "warped_stem.png"),
    }
    texture_path, texture_stick_path = ["assets/minecraft/textures/block/" + x for x in sign_texture[blockid]]

    texture = self.load_image_texture(texture_path).copy()

    # cut the planks to the size of a signpost
    ImageDraw.Draw(texture).rectangle((0,12,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # If the signpost is looking directly to the image, draw some 
    # random dots, they will look as text.
    if data in (0,1,2,3,4,5,15):
        for i in range(15):
            x = randint(4,11)
            y = randint(3,7)
            texture.putpixel((x,y),(0,0,0,255))

    # Minecraft uses wood texture for the signpost stick
    texture_stick = self.load_image_texture(texture_stick_path)
    texture_stick = texture_stick.resize((12,12), Image.ANTIALIAS)
    ImageDraw.Draw(texture_stick).rectangle((2,0,12,12),outline=(0,0,0,0),fill=(0,0,0,0))

    img = Image.new("RGBA", (24,24), self.bgcolor)

    #         W                N      ~90       E                   S        ~270
    angles = (330.,345.,0.,15.,30.,55.,95.,120.,150.,165.,180.,195.,210.,230.,265.,310.)
    angle = math.radians(angles[data])
    post = self.transform_image_angle(texture, angle)

    # choose the position of the "3D effect"
    incrementx = 0
    if data in (1,6,7,8,9,14):
        incrementx = -1
    elif data in (3,4,5,11,12,13):
        incrementx = +1

    alpha_over(img, texture_stick,(11, 8),texture_stick)
    # post2 is a brighter signpost pasted with a small shift,
    # gives to the signpost some 3D effect.
    post2 = ImageEnhance.Brightness(post).enhance(1.2)
    alpha_over(img, post2,(incrementx, -3),post2)
    alpha_over(img, post, (0,-2), post)

    return img


# wooden and iron door
# uses pseudo-ancildata found in iterate.c
@material(blockid=ids.group_door, data=list(range(32)), transparent=True)
def door(self, blockid, data):
    #Masked to not clobber block top/bottom & swung info
    if self.rotation == 1:
        if (data & 0b00011) == 0: data = data & 0b11100 | 1
        elif (data & 0b00011) == 1: data = data & 0b11100 | 2
        elif (data & 0b00011) == 2: data = data & 0b11100 | 3
        elif (data & 0b00011) == 3: data = data & 0b11100 | 0
    elif self.rotation == 2:
        if (data & 0b00011) == 0: data = data & 0b11100 | 2
        elif (data & 0b00011) == 1: data = data & 0b11100 | 3
        elif (data & 0b00011) == 2: data = data & 0b11100 | 0
        elif (data & 0b00011) == 3: data = data & 0b11100 | 1
    elif self.rotation == 3:
        if (data & 0b00011) == 0: data = data & 0b11100 | 3
        elif (data & 0b00011) == 1: data = data & 0b11100 | 0
        elif (data & 0b00011) == 2: data = data & 0b11100 | 1
        elif (data & 0b00011) == 3: data = data & 0b11100 | 2

    if data & 0x8 == 0x8:  # top of the door
        if blockid == ids.block_minecraft__oak_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/oak_door_top.png")
        elif blockid == ids.block_minecraft__iron_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/iron_door_top.png")
        elif blockid == ids.block_minecraft__spruce_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/spruce_door_top.png")
        elif blockid == ids.block_minecraft__birch_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/birch_door_top.png")
        elif blockid == ids.block_minecraft__jungle_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/jungle_door_top.png")
        elif blockid == ids.block_minecraft__acacia_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/acacia_door_top.png")
        elif blockid == ids.block_minecraft__dark_oak_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/dark_oak_door_top.png")
        elif blockid == ids.block_minecraft__crimson_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/crimson_door_top.png")
        elif blockid == ids.block_minecraft__warped_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/warped_door_top.png")
    else:  # bottom of the door
        if blockid == ids.block_minecraft__oak_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/oak_door_bottom.png")
        elif blockid == ids.block_minecraft__iron_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/iron_door_bottom.png")
        elif blockid == ids.block_minecraft__spruce_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/spruce_door_bottom.png")
        elif blockid == ids.block_minecraft__birch_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/birch_door_bottom.png")
        elif blockid == ids.block_minecraft__jungle_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/jungle_door_bottom.png")
        elif blockid == ids.block_minecraft__acacia_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/acacia_door_bottom.png")
        elif blockid == ids.block_minecraft__dark_oak_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/dark_oak_door_bottom.png")
        elif blockid == ids.block_minecraft__crimson_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/crimson_door_bottom.png")
        elif blockid == ids.block_minecraft__warped_door:
            raw_door = self.load_image_texture("assets/minecraft/textures/block/warped_door_bottom.png")
    
    # if you want to render all doors as closed, then force
    # force closed to be True
    if data & 0x4 == 0x4:
        closed = False
    else:
        closed = True
    
    if data & 0x10 == 0x10:
        # hinge on the left (facing same door direction)
        hinge_on_left = True
    else:
        # hinge on the right (default single door)
        hinge_on_left = False

    # mask out the high bits to figure out the orientation 
    img = Image.new("RGBA", (24,24), self.bgcolor)
    if (data & 0x03) == 0: # facing west when closed
        if hinge_on_left:
            if closed:
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                alpha_over(img, tex, (0,6), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                tex = tex.transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (12,6), tex)
        else:
            if closed:
                tex = self.transform_image_side(raw_door)    
                alpha_over(img, tex, (0,6), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                tex = tex.transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (0,0), tex)
    
    if (data & 0x03) == 1: # facing north when closed
        if hinge_on_left:
            if closed:
                tex = self.transform_image_side(raw_door).transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (0,0), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door)
                alpha_over(img, tex, (0,6), tex)

        else:
            if closed:
                tex = self.transform_image_side(raw_door).transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (0,0), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door)
                alpha_over(img, tex, (12,0), tex)

                
    if (data & 0x03) == 2: # facing east when closed
        if hinge_on_left:
            if closed:
                tex = self.transform_image_side(raw_door)
                alpha_over(img, tex, (12,0), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door)
                tex = tex.transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (0,0), tex)
        else:
            if closed:
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                alpha_over(img, tex, (12,0), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door).transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (12,6), tex)

    if (data & 0x03) == 3: # facing south when closed
        if hinge_on_left:
            if closed:
                tex = self.transform_image_side(raw_door).transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (12,6), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                alpha_over(img, tex, (12,0), tex)
        else:
            if closed:
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                tex = tex.transpose(Image.FLIP_LEFT_RIGHT)
                alpha_over(img, tex, (12,6), tex)
            else:
                # flip first to set the doornob on the correct side
                tex = self.transform_image_side(raw_door.transpose(Image.FLIP_LEFT_RIGHT))
                alpha_over(img, tex, (0,6), tex)

    return img

# ladder
@material(blockid=ids.block_minecraft__ladder, data=[2, 3, 4, 5], transparent=True)
def ladder(self, blockid, data):

    # first rotations
    if self.rotation == 1:
        if data == 2: data = 5
        elif data == 3: data = 4
        elif data == 4: data = 2
        elif data == 5: data = 3
    elif self.rotation == 2:
        if data == 2: data = 3
        elif data == 3: data = 2
        elif data == 4: data = 5
        elif data == 5: data = 4
    elif self.rotation == 3:
        if data == 2: data = 4
        elif data == 3: data = 5
        elif data == 4: data = 3
        elif data == 5: data = 2

    img = Image.new("RGBA", (24,24), self.bgcolor)
    raw_texture = self.load_image_texture("assets/minecraft/textures/block/ladder.png")

    if data == 5:
        # normally this ladder would be obsured by the block it's attached to
        # but since ladders can apparently be placed on transparent blocks, we 
        # have to render this thing anyway.  same for data == 2
        tex = self.transform_image_side(raw_texture)
        alpha_over(img, tex, (0,6), tex)
        return img
    if data == 2:
        tex = self.transform_image_side(raw_texture).transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, tex, (12,6), tex)
        return img
    if data == 3:
        tex = self.transform_image_side(raw_texture).transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, tex, (0,0), tex)
        return img
    if data == 4:
        tex = self.transform_image_side(raw_texture)
        alpha_over(img, tex, (12,0), tex)
        return img

# wall signs
@material(blockid=ids.group_wall_sign, data=[2, 3, 4, 5], transparent=True)
def wall_sign(self, blockid, data): # wall sign

    # first rotations
    if self.rotation == 1:
        if data == 2: data = 5
        elif data == 3: data = 4
        elif data == 4: data = 2
        elif data == 5: data = 3
    elif self.rotation == 2:
        if data == 2: data = 3
        elif data == 3: data = 2
        elif data == 4: data = 5
        elif data == 5: data = 4
    elif self.rotation == 3:
        if data == 2: data = 4
        elif data == 3: data = 5
        elif data == 4: data = 3
        elif data == 5: data = 2
    
    sign_texture = {
        ids.block_minecraft__oak_wall_sign: "oak_planks.png",
        ids.block_minecraft__spruce_wall_sign: "spruce_planks.png",
        ids.block_minecraft__birch_wall_sign: "birch_planks.png",
        ids.block_minecraft__acacia_wall_sign: "jungle_planks.png",
        ids.block_minecraft__jungle_wall_sign: "acacia_planks.png",
        ids.block_minecraft__dark_oak_wall_sign: "dark_oak_planks.png",
        ids.block_minecraft__warped_wall_sign: "warped_planks.png",
        ids.block_minecraft__crimson_wall_sign: "crimson_planks.png",
    }
    texture_path = "assets/minecraft/textures/block/" + sign_texture[blockid]
    texture = self.load_image_texture(texture_path).copy()
    # cut the planks to the size of a signpost
    ImageDraw.Draw(texture).rectangle((0,12,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # draw some random black dots, they will look as text
    """ don't draw text at the moment, they are used in blank for decoration
    
    if data in (3,4):
        for i in range(15):
            x = randint(4,11)
            y = randint(3,7)
            texture.putpixel((x,y),(0,0,0,255))
    """
    
    img = Image.new("RGBA", (24, 24), self.bgcolor)

    incrementx = 0
    if data == 2:  # east
        incrementx = +1
        sign = self.build_full_block(None, None, None, None, texture)
    elif data == 3:  # west
        incrementx = -1
        sign = self.build_full_block(None, texture, None, None, None)
    elif data == 4:  # north
        incrementx = +1
        sign = self.build_full_block(None, None, texture, None, None)
    elif data == 5:  # south
        incrementx = -1
        sign = self.build_full_block(None, None, None, texture, None)

    sign2 = ImageEnhance.Brightness(sign).enhance(1.2)
    alpha_over(img, sign2,(incrementx, 2),sign2)
    alpha_over(img, sign, (0,3), sign)

    return img

# levers
@material(blockid=ids.block_minecraft__lever, data=list(range(16)), transparent=True)
def levers(self, blockid, data):
    if data & 8 == 8: powered = True
    else: powered = False

    data = data & 7

    # first rotations
    if self.rotation == 1:
        # on wall levers
        if data == 1: data = 3
        elif data == 2: data = 4
        elif data == 3: data = 2
        elif data == 4: data = 1
        # on floor levers
        elif data == 5: data = 6
        elif data == 6: data = 5
    elif self.rotation == 2:
        if data == 1: data = 2
        elif data == 2: data = 1
        elif data == 3: data = 4
        elif data == 4: data = 3
        elif data == 5: data = 5
        elif data == 6: data = 6
    elif self.rotation == 3:
        if data == 1: data = 4
        elif data == 2: data = 3
        elif data == 3: data = 1
        elif data == 4: data = 2
        elif data == 5: data = 6
        elif data == 6: data = 5

    # generate the texture for the base of the lever
    t_base = self.load_image_texture("assets/minecraft/textures/block/stone.png").copy()

    ImageDraw.Draw(t_base).rectangle((0,0,15,3),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t_base).rectangle((0,12,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t_base).rectangle((0,0,4,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t_base).rectangle((11,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # generate the texture for the stick
    stick = self.load_image_texture("assets/minecraft/textures/block/lever.png").copy()
    c_stick = Image.new("RGBA", (16,16), self.bgcolor)
    
    tmp = ImageEnhance.Brightness(stick).enhance(0.8)
    alpha_over(c_stick, tmp, (1,0), tmp)
    alpha_over(c_stick, stick, (0,0), stick)
    t_stick = self.transform_image_side(c_stick.rotate(45, Image.NEAREST))

    # where the lever will be composed
    img = Image.new("RGBA", (24,24), self.bgcolor)
    
    # wall levers
    if data == 1: # facing SOUTH
        # levers can't be placed in transparent blocks, so this
        # direction is almost invisible
        return None

    elif data == 2: # facing NORTH
        base = self.transform_image_side(t_base)
        
        # paste it twice with different brightness to make a fake 3D effect
        alpha_over(img, base, (12,-1), base)

        alpha = base.split()[3]
        base = ImageEnhance.Brightness(base).enhance(0.9)
        base.putalpha(alpha)
        
        alpha_over(img, base, (11,0), base)

        # paste the lever stick
        pos = (7,-7)
        if powered:
            t_stick = t_stick.transpose(Image.FLIP_TOP_BOTTOM)
            pos = (7,6)
        alpha_over(img, t_stick, pos, t_stick)

    elif data == 3: # facing WEST
        base = self.transform_image_side(t_base)
        
        # paste it twice with different brightness to make a fake 3D effect
        base = base.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, base, (0,-1), base)

        alpha = base.split()[3]
        base = ImageEnhance.Brightness(base).enhance(0.9)
        base.putalpha(alpha)
        
        alpha_over(img, base, (1,0), base)
        
        # paste the lever stick
        t_stick = t_stick.transpose(Image.FLIP_LEFT_RIGHT)
        pos = (5,-7)
        if powered:
            t_stick = t_stick.transpose(Image.FLIP_TOP_BOTTOM)
            pos = (6,6)
        alpha_over(img, t_stick, pos, t_stick)

    elif data == 4: # facing EAST
        # levers can't be placed in transparent blocks, so this
        # direction is almost invisible
        return None

    # floor levers
    elif data == 5: # pointing south when off
        # lever base, fake 3d again
        base = self.transform_image_top(t_base)

        alpha = base.split()[3]
        tmp = ImageEnhance.Brightness(base).enhance(0.8)
        tmp.putalpha(alpha)
        
        alpha_over(img, tmp, (0,12), tmp)
        alpha_over(img, base, (0,11), base)

        # lever stick
        pos = (3,2)
        if not powered:
            t_stick = t_stick.transpose(Image.FLIP_LEFT_RIGHT)
            pos = (11,2)
        alpha_over(img, t_stick, pos, t_stick)

    elif data == 6: # pointing east when off
        # lever base, fake 3d again
        base = self.transform_image_top(t_base.rotate(90))

        alpha = base.split()[3]
        tmp = ImageEnhance.Brightness(base).enhance(0.8)
        tmp.putalpha(alpha)
        
        alpha_over(img, tmp, (0,12), tmp)
        alpha_over(img, base, (0,11), base)

        # lever stick
        pos = (2,3)
        if not powered:
            t_stick = t_stick.transpose(Image.FLIP_LEFT_RIGHT)
            pos = (10,2)
        alpha_over(img, t_stick, pos, t_stick)

    return img


# wooden and stone pressure plates, and weighted pressure plates
@material(blockid=ids.group_pressure_plate, data=[0, 1], transparent=True)
def pressure_plate(self, blockid, data):
    texture_name = {ids.block_minecraft__stone_pressure_plate: "assets/minecraft/textures/block/stone.png",  # stone
                    ids.block_minecraft__oak_pressure_plate: "assets/minecraft/textures/block/oak_planks.png",  # oak
                    ids.block_minecraft__spruce_pressure_plate: "assets/minecraft/textures/block/spruce_planks.png",  # spruce
                    ids.block_minecraft__birch_pressure_plate: "assets/minecraft/textures/block/birch_planks.png",  # birch
                    ids.block_minecraft__jungle_pressure_plate: "assets/minecraft/textures/block/jungle_planks.png",  # jungle
                    ids.block_minecraft__acacia_pressure_plate: "assets/minecraft/textures/block/acacia_planks.png",  # acacia
                    ids.block_minecraft__dark_oak_pressure_plate: "assets/minecraft/textures/block/dark_oak_planks.png",  # dark oak
                    ids.block_minecraft__light_weighted_pressure_plate: "assets/minecraft/textures/block/gold_block.png",  # light golden
                    ids.block_minecraft__heavy_weighted_pressure_plate: "assets/minecraft/textures/block/iron_block.png",  # heavy iron
                    ids.block_minecraft__crimson_pressure_plate: "assets/minecraft/textures/block/crimson_planks.png",
                    ids.block_minecraft__warped_pressure_plate: "assets/minecraft/textures/block/warped_planks.png",
                    ids.block_minecraft__polished_blackstone_pressure_plate: "assets/minecraft/textures/block/polished_blackstone.png",
                   }[blockid]
    t = self.load_image_texture(texture_name).copy()
    
    # cut out the outside border, pressure plates are smaller
    # than a normal block
    ImageDraw.Draw(t).rectangle((0,0,15,15),outline=(0,0,0,0))
    
    # create the textures and a darker version to make a 3d by 
    # pasting them with an offstet of 1 pixel
    img = Image.new("RGBA", (24,24), self.bgcolor)
    
    top = self.transform_image_top(t)
    
    alpha = top.split()[3]
    topd = ImageEnhance.Brightness(top).enhance(0.8)
    topd.putalpha(alpha)
    
    #show it 3d or 2d if unpressed or pressed
    if data == 0:
        alpha_over(img,topd, (0,12),topd)
        alpha_over(img,top, (0,11),top)
    elif data == 1:
        alpha_over(img,top, (0,12),top)
    
    return img

# stone a wood buttons
@material(blockid=ids.group_button, data=list(range(16)), transparent=True)
def buttons(self, blockid, data):

    # 0x8 is set if the button is pressed mask this info and render
    # it as unpressed
    data = data & 0x7

    if self.rotation == 1:
        if data == 1: data = 3
        elif data == 2: data = 4
        elif data == 3: data = 2
        elif data == 4: data = 1
        elif data == 5: data = 6
        elif data == 6: data = 5
    elif self.rotation == 2:
        if data == 1: data = 2
        elif data == 2: data = 1
        elif data == 3: data = 4
        elif data == 4: data = 3
    elif self.rotation == 3:
        if data == 1: data = 4
        elif data == 2: data = 3
        elif data == 3: data = 1
        elif data == 4: data = 2
        elif data == 5: data = 6
        elif data == 6: data = 5

    texturepath = {
        ids.block_minecraft__stone_button: "assets/minecraft/textures/block/stone.png",
        ids.block_minecraft__oak_button: "assets/minecraft/textures/block/oak_planks.png",
        ids.block_minecraft__spruce_button: "assets/minecraft/textures/block/spruce_planks.png",
        ids.block_minecraft__birch_button: "assets/minecraft/textures/block/birch_planks.png",
        ids.block_minecraft__jungle_button: "assets/minecraft/textures/block/jungle_planks.png",
        ids.block_minecraft__acacia_button: "assets/minecraft/textures/block/acacia_planks.png",
        ids.block_minecraft__dark_oak_button: "assets/minecraft/textures/block/dark_oak_planks.png",
        ids.block_minecraft__crimson_button: "assets/minecraft/textures/block/crimson_planks.png",
        ids.block_minecraft__warped_button: "assets/minecraft/textures/block/warped_planks.png",
        ids.block_minecraft__polished_blackstone_button: "assets/minecraft/textures/block/polished_blackstone.png",
    }[blockid]
    t = self.load_image_texture(texturepath).copy()

    # generate the texture for the button
    ImageDraw.Draw(t).rectangle((0,0,15,5),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t).rectangle((0,10,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t).rectangle((0,0,4,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(t).rectangle((11,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    img = Image.new("RGBA", (24,24), self.bgcolor)

    if data < 5:
        button = self.transform_image_side(t)

        if data == 1: # facing SOUTH
            # buttons can't be placed in transparent blocks, so this
            # direction can't be seen
            return None

        elif data == 2: # facing NORTH
            # paste it twice with different brightness to make a 3D effect
            alpha_over(img, button, (12,-1), button)

            alpha = button.split()[3]
            button = ImageEnhance.Brightness(button).enhance(0.9)
            button.putalpha(alpha)

            alpha_over(img, button, (11,0), button)

        elif data == 3: # facing WEST
            # paste it twice with different brightness to make a 3D effect
            button = button.transpose(Image.FLIP_LEFT_RIGHT)
            alpha_over(img, button, (0,-1), button)

            alpha = button.split()[3]
            button = ImageEnhance.Brightness(button).enhance(0.9)
            button.putalpha(alpha)

            alpha_over(img, button, (1,0), button)

        elif data == 4: # facing EAST
            # buttons can't be placed in transparent blocks, so this
            # direction can't be seen
            return None

    else:
        if data == 5: # long axis east-west
            button = self.transform_image_top(t)
        else: # long axis north-south
            button = self.transform_image_top(t.rotate(90))

        # paste it twice with different brightness to make a 3D effect
        alpha_over(img, button, (0,12), button)

        alpha = button.split()[3]
        button = ImageEnhance.Brightness(button).enhance(0.9)
        button.putalpha(alpha)

        alpha_over(img, button, (0,11), button)

    return img

# snow
@material(blockid=ids.block_minecraft__snow, data=list(range(16)), transparent=True, solid=True)
def snow(self, blockid, data):
    # still not rendered correctly: data other than 0
    
    tex = self.load_image_texture("assets/minecraft/textures/block/snow.png")
    
    # make the side image, top 3/4 transparent
    mask = tex.crop((0,12,16,16))
    sidetex = Image.new(tex.mode, tex.size, self.bgcolor)
    alpha_over(sidetex, mask, (0,12,16,16), mask)
    
    img = Image.new("RGBA", (24,24), self.bgcolor)
    
    top = self.transform_image_top(tex)
    side = self.transform_image_side(sidetex)
    otherside = side.transpose(Image.FLIP_LEFT_RIGHT)
    
    alpha_over(img, side, (0,6), side)
    alpha_over(img, otherside, (12,6), otherside)
    alpha_over(img, top, (0,9), top)
    
    return img


# cactus
@material(blockid=ids.block_minecraft__cactus, data=0, transparent=False, solid=True, nospawn=True)
def cactus(self, blockid, data):
    top = self.load_image_texture("assets/minecraft/textures/block/cactus_top.png")
    side = self.load_image_texture("assets/minecraft/textures/block/cactus_side.png")

    img = Image.new("RGBA", (24, 24), self.bgcolor)

    top = self.transform_image_top(top)
    side = self.transform_image_side(side)
    otherside = side.transpose(Image.FLIP_LEFT_RIGHT)

    sidealpha = side.split()[3]
    side = ImageEnhance.Brightness(side).enhance(0.9)
    side.putalpha(sidealpha)
    othersidealpha = otherside.split()[3]
    otherside = ImageEnhance.Brightness(otherside).enhance(0.8)
    otherside.putalpha(othersidealpha)

    alpha_over(img, side, (1, 6), side)
    alpha_over(img, otherside, (11, 6), otherside)
    alpha_over(img, top, (0, 0), top)

    return img


# nether and normal fences
# uses pseudo-ancildata found in iterate.c
@material(blockid=ids.group_fence, data=list(range(16)), transparent=True, nospawn=True)
def fence(self, blockid, data):
    # no need for rotations, it uses pseudo data.
    # create needed images for Big stick fence
    if blockid == ids.block_minecraft__oak_fence:  # normal fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/oak_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/oak_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/oak_planks.png").copy()
    elif blockid == ids.block_minecraft__nether_brick_fence:  # spruce fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/spruce_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/spruce_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/spruce_planks.png").copy()
    elif blockid == ids.block_minecraft__spruce_fence:  # birch fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/birch_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/birch_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/birch_planks.png").copy()
    elif blockid == ids.block_minecraft__birch_fence:  # jungle fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/jungle_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/jungle_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/jungle_planks.png").copy()
    elif blockid == ids.block_minecraft__jungle_fence:  # big/dark oak fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png").copy()
    elif blockid == ids.block_minecraft__acacia_fence:  # acacia oak fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/acacia_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/acacia_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/acacia_planks.png").copy()
    elif blockid == ids.block_minecraft__dark_oak_fence:  # netherbrick fence
        fence_top = self.load_image_texture("assets/minecraft/textures/block/nether_bricks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/nether_bricks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/nether_bricks.png").copy()
    elif blockid == ids.block_minecraft__crimson_fence:
        fence_top = self.load_image_texture("assets/minecraft/textures/block/crimson_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/crimson_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/crimson_planks.png").copy()
    elif blockid == ids.block_minecraft__warped_fence:
        fence_top = self.load_image_texture("assets/minecraft/textures/block/warped_planks.png").copy()
        fence_side = self.load_image_texture("assets/minecraft/textures/block/warped_planks.png").copy()
        fence_small_side = self.load_image_texture("assets/minecraft/textures/block/warped_planks.png").copy()

    # generate the textures of the fence
    ImageDraw.Draw(fence_top).rectangle((0,0,5,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_top).rectangle((10,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_top).rectangle((0,0,15,5),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_top).rectangle((0,10,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    ImageDraw.Draw(fence_side).rectangle((0,0,5,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_side).rectangle((10,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # Create the sides and the top of the big stick
    fence_side = self.transform_image_side(fence_side)
    fence_other_side = fence_side.transpose(Image.FLIP_LEFT_RIGHT)
    fence_top = self.transform_image_top(fence_top)

    # Darken the sides slightly. These methods also affect the alpha layer,
    # so save them first (we don't want to "darken" the alpha layer making
    # the block transparent)
    sidealpha = fence_side.split()[3]
    fence_side = ImageEnhance.Brightness(fence_side).enhance(0.9)
    fence_side.putalpha(sidealpha)
    othersidealpha = fence_other_side.split()[3]
    fence_other_side = ImageEnhance.Brightness(fence_other_side).enhance(0.8)
    fence_other_side.putalpha(othersidealpha)

    # Compose the fence big stick
    fence_big = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(fence_big,fence_side, (5,4),fence_side)
    alpha_over(fence_big,fence_other_side, (7,4),fence_other_side)
    alpha_over(fence_big,fence_top, (0,0),fence_top)
    
    # Now render the small sticks.
    # Create needed images
    ImageDraw.Draw(fence_small_side).rectangle((0,0,15,0),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_small_side).rectangle((0,4,15,6),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_small_side).rectangle((0,10,15,16),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_small_side).rectangle((0,0,4,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(fence_small_side).rectangle((11,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # Create the sides and the top of the small sticks
    fence_small_side = self.transform_image_side(fence_small_side)
    fence_small_other_side = fence_small_side.transpose(Image.FLIP_LEFT_RIGHT)
    
    # Darken the sides slightly. These methods also affect the alpha layer,
    # so save them first (we don't want to "darken" the alpha layer making
    # the block transparent)
    sidealpha = fence_small_other_side.split()[3]
    fence_small_other_side = ImageEnhance.Brightness(fence_small_other_side).enhance(0.9)
    fence_small_other_side.putalpha(sidealpha)
    sidealpha = fence_small_side.split()[3]
    fence_small_side = ImageEnhance.Brightness(fence_small_side).enhance(0.9)
    fence_small_side.putalpha(sidealpha)

    # Create img to compose the fence
    img = Image.new("RGBA", (24,24), self.bgcolor)

    # Position of fence small sticks in img.
    # These postitions are strange because the small sticks of the 
    # fence are at the very left and at the very right of the 16x16 images
    pos_top_left = (2,3)
    pos_top_right = (10,3)
    pos_bottom_right = (10,7)
    pos_bottom_left = (2,7)
    
    # +x axis points top right direction
    # +y axis points bottom right direction
    # First compose small sticks in the back of the image, 
    # then big stick and thecn small sticks in the front.

    if (data & 0b0001) == 1:
        alpha_over(img,fence_small_side, pos_top_left,fence_small_side)                # top left
    if (data & 0b1000) == 8:
        alpha_over(img,fence_small_other_side, pos_top_right,fence_small_other_side)    # top right
        
    alpha_over(img,fence_big,(0,0),fence_big)
        
    if (data & 0b0010) == 2:
        alpha_over(img,fence_small_other_side, pos_bottom_left,fence_small_other_side)      # bottom left    
    if (data & 0b0100) == 4:
        alpha_over(img,fence_small_side, pos_bottom_right,fence_small_side)                  # bottom right
    
    return img

# pumpkin
@material(blockid=ids.group_pumpkin, data=list(range(4)), solid=True)
def pumpkin(self, blockid, data): # pumpkins, jack-o-lantern
    # rotation
    if self.rotation == 1:
        if data == 0: data = 1
        elif data == 1: data = 2
        elif data == 2: data = 3
        elif data == 3: data = 0
    elif self.rotation == 2:
        if data == 0: data = 2
        elif data == 1: data = 3
        elif data == 2: data = 0
        elif data == 3: data = 1
    elif self.rotation == 3:
        if data == 0: data = 3
        elif data == 1: data = 0
        elif data == 2: data = 1
        elif data == 3: data = 2
    
    # texture generation
    top = self.load_image_texture("assets/minecraft/textures/block/pumpkin_top.png")
    frontName = {ids.block_minecraft__pumpkin: "assets/minecraft/textures/block/pumpkin_side.png",
                 ids.block_minecraft__jack_o_lantern: "assets/minecraft/textures/block/jack_o_lantern.png",
                 ids.block_minecraft__carved_pumpkin: "assets/minecraft/textures/block/carved_pumpkin.png"
                }[blockid]
    front = self.load_image_texture(frontName)
    side = self.load_image_texture("assets/minecraft/textures/block/pumpkin_side.png")

    if data == 0: # pointing west
        img = self.build_full_block(top, None, None, side, front)

    elif data == 1: # pointing north
        img = self.build_full_block(top, None, None, front, side)

    else: # in any other direction the front can't be seen
        img = self.build_full_block(top, None, None, side, side)

    return img



# portal
@material(blockid=ids.block_minecraft__nether_portal, data=[1, 2, 4, 5, 8, 10], transparent=True)
def portal(self, blockid, data):
    # no rotations, uses pseudo data
    portaltexture = self.load_portal()
    img = Image.new("RGBA", (24,24), self.bgcolor)

    side = self.transform_image_side(portaltexture)
    otherside = side.transpose(Image.FLIP_TOP_BOTTOM)

    if data in (1,4,5):
        alpha_over(img, side, (5,4), side)

    if data in (2,8,10):
        alpha_over(img, otherside, (5,4), otherside)

    return img


# cake!
@material(blockid=ids.block_minecraft__cake, data=list(range(7)), transparent=True, nospawn=True)
def cake(self, blockid, data):
    # cake textures
    top = self.load_image_texture("assets/minecraft/textures/block/cake_top.png").copy()
    side = self.load_image_texture("assets/minecraft/textures/block/cake_side.png").copy()
    fullside = side.copy()
    inside = self.load_image_texture("assets/minecraft/textures/block/cake_inner.png")

    img = Image.new("RGBA", (24, 24), self.bgcolor)
    if data == 0:  # unbitten cake
        top = self.transform_image_top(top)
        side = self.transform_image_side(side)
        otherside = side.transpose(Image.FLIP_LEFT_RIGHT)

        # darken sides slightly
        sidealpha = side.split()[3]
        side = ImageEnhance.Brightness(side).enhance(0.9)
        side.putalpha(sidealpha)
        othersidealpha = otherside.split()[3]
        otherside = ImageEnhance.Brightness(otherside).enhance(0.8)
        otherside.putalpha(othersidealpha)

        # composite the cake
        alpha_over(img, side, (1, 6), side)
        alpha_over(img, otherside, (11, 5), otherside)  # workaround, fixes a hole
        alpha_over(img, otherside, (12, 6), otherside)
        alpha_over(img, top, (0, 6), top)

    else:
        # cut the textures for a bitten cake
        bite_width = int(14 / 7)  # Cake is 14px wide with 7 slices
        coord = 1 + bite_width * data
        ImageDraw.Draw(side).rectangle((16 - coord, 0, 16, 16), outline=(0, 0, 0, 0),
                                       fill=(0, 0, 0, 0))
        ImageDraw.Draw(top).rectangle((0, 0, coord - 1, 16), outline=(0, 0, 0, 0),
                                      fill=(0, 0, 0, 0))

        # the bitten part of the cake always points to the west
        # composite the cake for every north orientation
        if self.rotation == 0:  # north top-left
            # create right side
            rs = self.transform_image_side(side).transpose(Image.FLIP_LEFT_RIGHT)
            # create bitten side and its coords
            deltax = bite_width * data
            deltay = -1 * data
            if data in [3, 4, 5, 6]:
                deltax -= 1
            ls = self.transform_image_side(inside)
            # create top side
            t = self.transform_image_top(top)
            # darken sides slightly
            sidealpha = ls.split()[3]
            ls = ImageEnhance.Brightness(ls).enhance(0.9)
            ls.putalpha(sidealpha)
            othersidealpha = rs.split()[3]
            rs = ImageEnhance.Brightness(rs).enhance(0.8)
            rs.putalpha(othersidealpha)
            # compose the cake
            alpha_over(img, rs, (12, 6), rs)
            alpha_over(img, ls, (1 + deltax, 6 + deltay), ls)
            alpha_over(img, t, (1, 6), t)

        elif self.rotation == 1:  # north top-right
            # bitten side not shown
            # create left side
            ls = self.transform_image_side(side.transpose(Image.FLIP_LEFT_RIGHT))
            # create top
            t = self.transform_image_top(top.rotate(-90))
            # create right side
            rs = self.transform_image_side(fullside).transpose(Image.FLIP_LEFT_RIGHT)
            # darken sides slightly
            sidealpha = ls.split()[3]
            ls = ImageEnhance.Brightness(ls).enhance(0.9)
            ls.putalpha(sidealpha)
            othersidealpha = rs.split()[3]
            rs = ImageEnhance.Brightness(rs).enhance(0.8)
            rs.putalpha(othersidealpha)
            # compose the cake
            alpha_over(img, ls, (2, 6), ls)
            alpha_over(img, t, (1, 6), t)
            alpha_over(img, rs, (12, 6), rs)

        elif self.rotation == 2:  # north bottom-right
            # bitten side not shown
            # left side
            ls = self.transform_image_side(fullside)
            # top
            t = self.transform_image_top(top.rotate(180))
            # right side
            rs = self.transform_image_side(side.transpose(Image.FLIP_LEFT_RIGHT))
            rs = rs.transpose(Image.FLIP_LEFT_RIGHT)
            # darken sides slightly
            sidealpha = ls.split()[3]
            ls = ImageEnhance.Brightness(ls).enhance(0.9)
            ls.putalpha(sidealpha)
            othersidealpha = rs.split()[3]
            rs = ImageEnhance.Brightness(rs).enhance(0.8)
            rs.putalpha(othersidealpha)
            # compose the cake
            alpha_over(img, ls, (2, 6), ls)
            alpha_over(img, t, (1, 6), t)
            alpha_over(img, rs, (12, 6), rs)

        elif self.rotation == 3:  # north bottom-left
            # create left side
            ls = self.transform_image_side(side)
            # create top
            t = self.transform_image_top(top.rotate(90))
            # create right side and its coords
            deltax = 12 - bite_width * data
            deltay = -1 * data
            if data in [3, 4, 5, 6]:
                deltax += 1
            rs = self.transform_image_side(inside).transpose(Image.FLIP_LEFT_RIGHT)
            # darken sides slightly
            sidealpha = ls.split()[3]
            ls = ImageEnhance.Brightness(ls).enhance(0.9)
            ls.putalpha(sidealpha)
            othersidealpha = rs.split()[3]
            rs = ImageEnhance.Brightness(rs).enhance(0.8)
            rs.putalpha(othersidealpha)
            # compose the cake
            alpha_over(img, ls, (2, 6), ls)
            alpha_over(img, t, (1, 6), t)
            alpha_over(img, rs, (1 + deltax, 6 + deltay), rs)

    return img


# redstone repeaters ON and OFF
@material(blockid=ids.block_minecraft__repeater, data=list(range(16)), transparent=True, nospawn=True)
def repeater(self, blockid, data):
    # rotation
    # Masked to not clobber delay info
    if self.rotation == 1:
        if (data & 0b0011) == 0: data = data & 0b1100 | 1
        elif (data & 0b0011) == 1: data = data & 0b1100 | 2
        elif (data & 0b0011) == 2: data = data & 0b1100 | 3
        elif (data & 0b0011) == 3: data = data & 0b1100 | 0
    elif self.rotation == 2:
        if (data & 0b0011) == 0: data = data & 0b1100 | 2
        elif (data & 0b0011) == 1: data = data & 0b1100 | 3
        elif (data & 0b0011) == 2: data = data & 0b1100 | 0
        elif (data & 0b0011) == 3: data = data & 0b1100 | 1
    elif self.rotation == 3:
        if (data & 0b0011) == 0: data = data & 0b1100 | 3
        elif (data & 0b0011) == 1: data = data & 0b1100 | 0
        elif (data & 0b0011) == 2: data = data & 0b1100 | 1
        elif (data & 0b0011) == 3: data = data & 0b1100 | 2
    
    # generate the diode
    top = self.load_image_texture("assets/minecraft/textures/block/repeater.png") if blockid == 93 else self.load_image_texture("assets/minecraft/textures/block/repeater_on.png")
    side = self.load_image_texture("assets/minecraft/textures/block/smooth_stone_slab_side.png")
    increment = 13
    
    if (data & 0x3) == 0: # pointing east
        pass
    
    if (data & 0x3) == 1: # pointing south
        top = top.rotate(270)

    if (data & 0x3) == 2: # pointing west
        top = top.rotate(180)

    if (data & 0x3) == 3: # pointing north
        top = top.rotate(90)

    img = self.build_full_block( (top, increment), None, None, side, side)

    # compose a "3d" redstone torch
    t = self.load_image_texture("assets/minecraft/textures/block/redstone_torch_off.png").copy() if blockid == 93 else self.load_image_texture("assets/minecraft/textures/block/redstone_torch.png").copy()
    torch = Image.new("RGBA", (24,24), self.bgcolor)
    
    t_crop = t.crop((2,2,14,14))
    slice = t_crop.copy()
    ImageDraw.Draw(slice).rectangle((6,0,12,12),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(slice).rectangle((0,0,4,12),outline=(0,0,0,0),fill=(0,0,0,0))
    
    alpha_over(torch, slice, (6,4))
    alpha_over(torch, t_crop, (5,5))
    alpha_over(torch, t_crop, (6,5))
    alpha_over(torch, slice, (6,6))
    
    # paste redstone torches everywhere!
    # the torch is too tall for the repeater, crop the bottom.
    ImageDraw.Draw(torch).rectangle((0,16,24,24),outline=(0,0,0,0),fill=(0,0,0,0))
    
    # touch up the 3d effect with big rectangles, just in case, for other texture packs
    ImageDraw.Draw(torch).rectangle((0,24,10,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(torch).rectangle((12,15,24,24),outline=(0,0,0,0),fill=(0,0,0,0))
    
    # torch positions for every redstone torch orientation.
    #
    # This is a horrible list of torch orientations. I tried to 
    # obtain these orientations by rotating the positions for one
    # orientation, but pixel rounding is horrible and messes the
    # torches.

    if (data & 0x3) == 0: # pointing east
        if (data & 0xC) == 0: # one tick delay
            moving_torch = (1,1)
            static_torch = (-3,-1)
            
        elif (data & 0xC) == 4: # two ticks delay
            moving_torch = (2,2)
            static_torch = (-3,-1)
            
        elif (data & 0xC) == 8: # three ticks delay
            moving_torch = (3,2)
            static_torch = (-3,-1)
            
        elif (data & 0xC) == 12: # four ticks delay
            moving_torch = (4,3)
            static_torch = (-3,-1)
    
    elif (data & 0x3) == 1: # pointing south
        if (data & 0xC) == 0: # one tick delay
            moving_torch = (1,1)
            static_torch = (5,-1)
            
        elif (data & 0xC) == 4: # two ticks delay
            moving_torch = (0,2)
            static_torch = (5,-1)
            
        elif (data & 0xC) == 8: # three ticks delay
            moving_torch = (-1,2)
            static_torch = (5,-1)
            
        elif (data & 0xC) == 12: # four ticks delay
            moving_torch = (-2,3)
            static_torch = (5,-1)

    elif (data & 0x3) == 2: # pointing west
        if (data & 0xC) == 0: # one tick delay
            moving_torch = (1,1)
            static_torch = (5,3)
            
        elif (data & 0xC) == 4: # two ticks delay
            moving_torch = (0,0)
            static_torch = (5,3)
            
        elif (data & 0xC) == 8: # three ticks delay
            moving_torch = (-1,0)
            static_torch = (5,3)
            
        elif (data & 0xC) == 12: # four ticks delay
            moving_torch = (-2,-1)
            static_torch = (5,3)

    elif (data & 0x3) == 3: # pointing north
        if (data & 0xC) == 0: # one tick delay
            moving_torch = (1,1)
            static_torch = (-3,3)
            
        elif (data & 0xC) == 4: # two ticks delay
            moving_torch = (2,0)
            static_torch = (-3,3)
            
        elif (data & 0xC) == 8: # three ticks delay
            moving_torch = (3,0)
            static_torch = (-3,3)
            
        elif (data & 0xC) == 12: # four ticks delay
            moving_torch = (4,-1)
            static_torch = (-3,3)
    
    # this paste order it's ok for east and south orientation
    # but it's wrong for north and west orientations. But using the
    # default texture pack the torches are small enough to no overlap.
    alpha_over(img, torch, static_torch, torch) 
    alpha_over(img, torch, moving_torch, torch)

    return img

# redstone comparator (149 is inactive, 150 is active)
@material(blockid=[ids.block_minecraft__comparator], data=list(range(16)), transparent=True, nospawn=True)
def comparator(self, blockid, data):

    # rotation
    # add self.rotation to the lower 2 bits,  mod 4
    data = data & 0b1100 | (((data & 0b11) + self.rotation) % 4)


    top = self.load_image_texture("assets/minecraft/textures/block/comparator.png") if blockid == ids.block_minecraft__comparator else self.load_image_texture("assets/minecraft/textures/block/comparator_on.png")
    side = self.load_image_texture("assets/minecraft/textures/block/smooth_stone_slab_side.png")
    increment = 13

    if (data & 0x3) == 0: # pointing north
        pass
        static_torch = (-3,-1)
        torch = ((0,2),(6,-1))
    
    if (data & 0x3) == 1: # pointing east
        top = top.rotate(270)
        static_torch = (5,-1)
        torch = ((-4,-1),(0,2))

    if (data & 0x3) == 2: # pointing south
        top = top.rotate(180)
        static_torch = (5,3)
        torch = ((0,-4),(-4,-1))

    if (data & 0x3) == 3: # pointing west
        top = top.rotate(90)
        static_torch = (-3,3)
        torch = ((1,-4),(6,-1))


    def build_torch(active):
        # compose a "3d" redstone torch
        t = self.load_image_texture("assets/minecraft/textures/block/redstone_torch_off.png").copy() if not active else self.load_image_texture("assets/minecraft/textures/block/redstone_torch.png").copy()
        torch = Image.new("RGBA", (24,24), self.bgcolor)
        
        t_crop = t.crop((2,2,14,14))
        slice = t_crop.copy()
        ImageDraw.Draw(slice).rectangle((6,0,12,12),outline=(0,0,0,0),fill=(0,0,0,0))
        ImageDraw.Draw(slice).rectangle((0,0,4,12),outline=(0,0,0,0),fill=(0,0,0,0))
        
        alpha_over(torch, slice, (6,4))
        alpha_over(torch, t_crop, (5,5))
        alpha_over(torch, t_crop, (6,5))
        alpha_over(torch, slice, (6,6))

        return torch
    
    active_torch = build_torch(True)
    inactive_torch = build_torch(False)
    back_torch = active_torch if (blockid == 150 or data & 0b1000 == 0b1000) else inactive_torch
    static_torch_img = active_torch if (data & 0b100 == 0b100) else inactive_torch 

    img = self.build_full_block( (top, increment), None, None, side, side)

    alpha_over(img, static_torch_img, static_torch, static_torch_img) 
    alpha_over(img, back_torch, torch[0], back_torch) 
    alpha_over(img, back_torch, torch[1], back_torch) 
    return img
    

# trapdoor
# the trapdoor is looks like a sprite when opened, that's not good
@material(blockid=ids.group_trapdoor, data=list(range(16)), transparent=True, nospawn=True)
def trapdoor(self, blockid, data):

    # rotation
    # Masked to not clobber opened/closed info
    if self.rotation == 1:
        if (data & 0b0011) == 0: data = data & 0b1100 | 3
        elif (data & 0b0011) == 1: data = data & 0b1100 | 2
        elif (data & 0b0011) == 2: data = data & 0b1100 | 0
        elif (data & 0b0011) == 3: data = data & 0b1100 | 1
    elif self.rotation == 2:
        if (data & 0b0011) == 0: data = data & 0b1100 | 1
        elif (data & 0b0011) == 1: data = data & 0b1100 | 0
        elif (data & 0b0011) == 2: data = data & 0b1100 | 3
        elif (data & 0b0011) == 3: data = data & 0b1100 | 2
    elif self.rotation == 3:
        if (data & 0b0011) == 0: data = data & 0b1100 | 2
        elif (data & 0b0011) == 1: data = data & 0b1100 | 3
        elif (data & 0b0011) == 2: data = data & 0b1100 | 1
        elif (data & 0b0011) == 3: data = data & 0b1100 | 0

    # texture generation
    texturepath = {ids.block_minecraft__oak_trapdoor: "assets/minecraft/textures/block/oak_trapdoor.png",
                   ids.block_minecraft__iron_trapdoor: "assets/minecraft/textures/block/iron_trapdoor.png",
                   ids.block_minecraft__spruce_trapdoor: "assets/minecraft/textures/block/spruce_trapdoor.png",
                   ids.block_minecraft__birch_trapdoor: "assets/minecraft/textures/block/birch_trapdoor.png",
                   ids.block_minecraft__jungle_trapdoor: "assets/minecraft/textures/block/jungle_trapdoor.png",
                   ids.block_minecraft__acacia_trapdoor: "assets/minecraft/textures/block/acacia_trapdoor.png",
                   ids.block_minecraft__dark_oak_trapdoor: "assets/minecraft/textures/block/dark_oak_trapdoor.png",
                    ids.block_minecraft__crimson_trapdoor: "assets/minecraft/textures/block/crimson_trapdoor.png",
                    ids.block_minecraft__warped_trapdoor: "assets/minecraft/textures/block/warped_trapdoor.png",

                  }[blockid]

    if data & 0x4 == 0x4: # opened trapdoor
        if data & 0x08 == 0x08: texture = self.load_image_texture(texturepath).transpose(Image.FLIP_TOP_BOTTOM)
        else: texture = self.load_image_texture(texturepath)

        if data & 0x3 == 0: # west
            img = self.build_full_block(None, None, None, None, texture)
        if data & 0x3 == 1: # east
            img = self.build_full_block(None, texture, None, None, None)
        if data & 0x3 == 2: # south
            img = self.build_full_block(None, None, texture, None, None)
        if data & 0x3 == 3: # north
            img = self.build_full_block(None, None, None, texture, None)

    elif data & 0x4 == 0: # closed trapdoor
        texture = self.load_image_texture(texturepath)
        if data & 0x8 == 0x8: # is a top trapdoor
            img = Image.new("RGBA", (24,24), self.bgcolor)
            t = self.build_full_block((texture, 12), None, None, texture, texture)
            alpha_over(img, t, (0,-9),t)
        else: # is a bottom trapdoor
            img = self.build_full_block((texture, 12), None, None, texture, texture)
    
    return img



# huge brown/red mushrooms, and mushroom stems
@material(blockid=ids.group_mushroom, data=list(range(64)), solid=True)
def huge_mushroom(self, blockid, data):
    # Re-arrange the bits in data based on self.rotation
    # rotation  bit: 654321
    #        0       DUENWS
    #        1       DUNWSE
    #        2       DUWSEN
    #        3       DUSENW
    if self.rotation in [1, 2, 3]:
        bit_map = {1: [6, 5, 3, 2, 1, 4],
                   2: [6, 5, 2, 1, 4, 3],
                   3: [6, 5, 1, 4, 3, 2]}
        new_data = 0

        # Add the ith bit to new_data then shift left one at a time,
        # re-ordering data's bits in the order specified in bit_map
        for i in bit_map[self.rotation]:
            new_data = new_data << 1
            new_data |= (data >> (i - 1)) & 1
        data = new_data

    # texture generation
    texture_map = {ids.block_minecraft__brown_mushroom_block: "brown_mushroom_block",
                   ids.block_minecraft__red_mushroom_block: "red_mushroom_block",
                   ids.block_minecraft__mushroom_stem: "mushroom_stem"}
    cap =  self.load_image_texture("assets/minecraft/textures/block/%s.png" % texture_map[blockid])
    porous = self.load_image_texture("assets/minecraft/textures/block/mushroom_block_inside.png")

    # Faces visible after amending data for rotation are: up, West, and South
    side_up    = cap if data & 0b010000 else porous  # Up
    side_west  = cap if data & 0b000010 else porous  # West
    side_south = cap if data & 0b000001 else porous  # South
    side_south = side_south.transpose(Image.FLIP_LEFT_RIGHT)

    return self.build_full_block(side_up, None, None, side_west, side_south)


# iron bars and glass pane
# TODO glass pane is not a sprite, it has a texture for the side,
# at the moment is not used
@material(blockid=ids.group_pane_and_bars, data=list(range(256)), transparent=True, nospawn=True)
def panes(self, blockid, data):
    # no rotation, uses pseudo data

    if blockid == ids.block_minecraft__iron_bars:
        t = self.load_image_texture("assets/minecraft/textures/block/iron_bars.png")
    elif blockid == ids.block_minecraft__glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/glass.png")
    elif blockid == ids.block_minecraft__white_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/white_stained_glass.png")
    elif blockid == ids.block_minecraft__orange_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/orange_stained_glass.png")
    elif blockid == ids.block_minecraft__magenta_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/magenta_stained_glass.png")
    elif blockid == ids.block_minecraft__light_blue_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/light_blue_stained_glass.png")
    elif blockid == ids.block_minecraft__yellow_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/yellow_stained_glass.png")
    elif blockid == ids.block_minecraft__lime_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/lime_stained_glass.png")
    elif blockid == ids.block_minecraft__pink_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/pink_stained_glass.png")
    elif blockid == ids.block_minecraft__gray_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/gray_stained_glass.png")
    elif blockid == ids.block_minecraft__light_gray_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/light_gray_stained_glass.png")
    elif blockid == ids.block_minecraft__cyan_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/cyan_stained_glass.png")
    elif blockid == ids.block_minecraft__purple_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/purple_stained_glass.png")
    elif blockid == ids.block_minecraft__blue_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/blue_stained_glass.png")
    elif blockid == ids.block_minecraft__brown_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/brown_stained_glass.png")
    elif blockid == ids.block_minecraft__green_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/green_stained_glass.png")
    elif blockid == ids.block_minecraft__red_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/red_stained_glass.png")
    elif blockid == ids.block_minecraft__black_stained_glass_pane:
        t = self.load_image_texture("assets/minecraft/textures/block/black_stained_glass.png")

    left = t.copy()
    right = t.copy()

    # generate the four small pieces of the glass pane
    ImageDraw.Draw(right).rectangle((0,0,7,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(left).rectangle((8,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    
    up_left = self.transform_image_side(left)
    up_right = self.transform_image_side(right).transpose(Image.FLIP_TOP_BOTTOM)
    dw_right = self.transform_image_side(right)
    dw_left = self.transform_image_side(left).transpose(Image.FLIP_TOP_BOTTOM)

    # Create img to compose the texture
    img = Image.new("RGBA", (24,24), self.bgcolor)

    # +x axis points top right direction
    # +y axis points bottom right direction
    # First compose things in the back of the image, 
    # then things in the front.

    # the lower 4 bits encode color, the upper 4 encode adjencies
    data = data >> 4

    if (data & 0b0001) == 1 or data == 0:
        alpha_over(img,up_left, (6,3),up_left)    # top left
    if (data & 0b1000) == 8 or data == 0:
        alpha_over(img,up_right, (6,3),up_right)  # top right
    if (data & 0b0010) == 2 or data == 0:
        alpha_over(img,dw_left, (6,3),dw_left)    # bottom left    
    if (data & 0b0100) == 4 or data == 0:
        alpha_over(img,dw_right, (6,3),dw_right)  # bottom right

    return img


# pumpkin and melon stem
# TODO To render it as in game needs from pseudo data and ancil data:
# once fully grown the stem bends to the melon/pumpkin block,
# at the moment only render the growing stem
@material(blockid=ids.group_melon_pumpkin_stem, data=list(range(8)), transparent=True)
def stem(self, blockid, data):
    # the ancildata value indicates how much of the texture
    # is shown.

    # not fully grown stem or no pumpkin/melon touching it,
    # straight up stem
    t = self.load_image_texture("assets/minecraft/textures/block/melon_stem.png").copy()
    img = Image.new("RGBA", (16,16), self.bgcolor)
    alpha_over(img, t, (0, int(16 - 16*((data + 1)/8.))), t)
    img = self.build_sprite(t)
    if data & 7 == 7:
        # fully grown stem gets brown color!
        # there is a conditional in rendermode-normal.c to not
        # tint the data value 7
        img = self.tint_texture(img, (211,169,116))
    return img


# vines
@material(blockid=ids.block_minecraft__vine, data=list(range(32)), transparent=True, solid=False, nospawn=True)
def vines(self, blockid, data):
    # Re-arrange the bits in data based on self.rotation
    # rotation  bit: 54321
    #        0       UENWS
    #        1       UNWSE
    #        2       UWSEN
    #        3       USENW
    if self.rotation in [1, 2, 3]:
        bit_map = {1: [5, 3, 2, 1, 4],
                   2: [5, 2, 1, 4, 3],
                   3: [5, 1, 4, 3, 2]}
        new_data = 0

        # Add the ith bit to new_data then shift left one at a time,
        # re-ordering data's bits in the order specified in bit_map
        for i in bit_map[self.rotation]:
            new_data = new_data << 1
            new_data |= (data >> (i - 1)) & 1
        data = new_data

    # decode data and prepare textures
    raw_texture = self.load_image_texture("assets/minecraft/textures/block/vine.png")

    side_up    = raw_texture if data & 0b10000 else None  # Up
    side_east  = raw_texture if data & 0b01000 else None  # East
    side_north = raw_texture if data & 0b00100 else None  # North
    side_west  = raw_texture if data & 0b00010 else None  # West
    side_south = raw_texture if data & 0b00001 else None  # South

    return self.build_full_block(side_up, side_north, side_east, side_west, side_south)


# fence gates
@material(blockid=ids.group_gate, data=list(range(8)), transparent=True, nospawn=True)
def fence_gate(self, blockid, data):

    # rotation
    opened = False
    if data & 0x4:
        data = data & 0x3
        opened = True
    if self.rotation == 1:
        if data == 0: data = 1
        elif data == 1: data = 2
        elif data == 2: data = 3
        elif data == 3: data = 0
    elif self.rotation == 2:
        if data == 0: data = 2
        elif data == 1: data = 3
        elif data == 2: data = 0
        elif data == 3: data = 1
    elif self.rotation == 3:
        if data == 0: data = 3
        elif data == 1: data = 0
        elif data == 2: data = 1
        elif data == 3: data = 2
    if opened:
        data = data | 0x4

    # create the closed gate side
    if blockid == ids.block_minecraft__oak_fence_gate: # Oak
        gate_side = self.load_image_texture("assets/minecraft/textures/block/oak_planks.png").copy()
    elif blockid == ids.block_minecraft__spruce_fence_gate: # Spruce
        gate_side = self.load_image_texture("assets/minecraft/textures/block/spruce_planks.png").copy()
    elif blockid == ids.block_minecraft__birch_fence_gate: # Birch
        gate_side = self.load_image_texture("assets/minecraft/textures/block/birch_planks.png").copy()
    elif blockid == ids.block_minecraft__jungle_fence_gate: # Jungle
        gate_side = self.load_image_texture("assets/minecraft/textures/block/jungle_planks.png").copy()
    elif blockid == ids.block_minecraft__dark_oak_fence_gate: # Dark Oak
        gate_side = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png").copy()
    elif blockid == ids.block_minecraft__acacia_fence_gate: # Acacia
        gate_side = self.load_image_texture("assets/minecraft/textures/block/acacia_planks.png").copy()
    elif blockid == ids.block_minecraft__crimson_fence_gate: # Acacia
        gate_side = self.load_image_texture("assets/minecraft/textures/block/crimson_planks.png").copy()
    elif blockid == ids.block_minecraft__warped_fence_gate: # Acacia
        gate_side = self.load_image_texture("assets/minecraft/textures/block/warped_planks.png").copy()


    else:
        return None

    gate_side_draw = ImageDraw.Draw(gate_side)
    gate_side_draw.rectangle((7,0,15,0),outline=(0,0,0,0),fill=(0,0,0,0))
    gate_side_draw.rectangle((7,4,9,6),outline=(0,0,0,0),fill=(0,0,0,0))
    gate_side_draw.rectangle((7,10,15,16),outline=(0,0,0,0),fill=(0,0,0,0))
    gate_side_draw.rectangle((0,12,15,16),outline=(0,0,0,0),fill=(0,0,0,0))
    gate_side_draw.rectangle((0,0,4,15),outline=(0,0,0,0),fill=(0,0,0,0))
    gate_side_draw.rectangle((14,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    
    # darken the sides slightly, as with the fences
    sidealpha = gate_side.split()[3]
    gate_side = ImageEnhance.Brightness(gate_side).enhance(0.9)
    gate_side.putalpha(sidealpha)
    
    # create the other sides
    mirror_gate_side = self.transform_image_side(gate_side.transpose(Image.FLIP_LEFT_RIGHT))
    gate_side = self.transform_image_side(gate_side)
    gate_other_side = gate_side.transpose(Image.FLIP_LEFT_RIGHT)
    mirror_gate_other_side = mirror_gate_side.transpose(Image.FLIP_LEFT_RIGHT)
    
    # Create img to compose the fence gate
    img = Image.new("RGBA", (24,24), self.bgcolor)
    
    if data & 0x4:
        # opened
        data = data & 0x3
        if data == 0:
            alpha_over(img, gate_side, (2,8), gate_side)
            alpha_over(img, gate_side, (13,3), gate_side)
        elif data == 1:
            alpha_over(img, gate_other_side, (-1,3), gate_other_side)
            alpha_over(img, gate_other_side, (10,8), gate_other_side)
        elif data == 2:
            alpha_over(img, mirror_gate_side, (-1,7), mirror_gate_side)
            alpha_over(img, mirror_gate_side, (10,2), mirror_gate_side)
        elif data == 3:
            alpha_over(img, mirror_gate_other_side, (2,1), mirror_gate_other_side)
            alpha_over(img, mirror_gate_other_side, (13,7), mirror_gate_other_side)
    else:
        # closed
        
        # positions for pasting the fence sides, as with fences
        pos_top_left = (2,3)
        pos_top_right = (10,3)
        pos_bottom_right = (10,7)
        pos_bottom_left = (2,7)
        
        if data == 0 or data == 2:
            alpha_over(img, gate_other_side, pos_top_right, gate_other_side)
            alpha_over(img, mirror_gate_other_side, pos_bottom_left, mirror_gate_other_side)
        elif data == 1 or data == 3:
            alpha_over(img, gate_side, pos_top_left, gate_side)
            alpha_over(img, mirror_gate_side, pos_bottom_right, mirror_gate_side)
    
    return img


# lilypad
# At the moment of writing this lilypads has no ancil data and their
# orientation depends on their position on the map. So it uses pseudo
# ancildata.
@material(blockid=ids.block_minecraft__lily_pad, data=list(range(4)), transparent=True)
def lilypad(self, blockid, data):
    t = self.load_image_texture("assets/minecraft/textures/block/lily_pad.png").copy()
    if data == 0:
        t = t.rotate(180)
    elif data == 1:
        t = t.rotate(270)
    elif data == 2:
        t = t
    elif data == 3:
        t = t.rotate(90)

    return self.build_full_block(None, None, None, None, None, t)


# enchantment table
# TODO there's no book at the moment
@material(blockid=ids.block_minecraft__enchanting_table, transparent=True, nodata=True)
def enchantment_table(self, blockid, data):
    # no book at the moment
    top = self.load_image_texture("assets/minecraft/textures/block/enchanting_table_top.png")
    side = self.load_image_texture("assets/minecraft/textures/block/enchanting_table_side.png")
    img = self.build_full_block((top, 4), None, None, side, side)

    return img

# brewing stand
# TODO this is a place holder, is a 2d image pasted
@material(blockid=ids.block_minecraft__brewing_stand, data=list(range(5)), transparent=True)
def brewing_stand(self, blockid, data):
    base = self.load_image_texture("assets/minecraft/textures/block/brewing_stand_base.png")
    img = self.build_full_block(None, None, None, None, None, base)
    t = self.load_image_texture("assets/minecraft/textures/block/brewing_stand.png")
    stand = self.build_billboard(t)
    alpha_over(img,stand,(0,-2))
    return img


# cauldron
@material(blockid=ids.block_minecraft__cauldron, data=list(range(4)), transparent=True, solid=True, nospawn=True)
def cauldron(self, blockid, data):
    side = self.load_image_texture("assets/minecraft/textures/block/cauldron_side.png").copy()
    top = self.load_image_texture("assets/minecraft/textures/block/cauldron_top.png")
    bottom = self.load_image_texture("assets/minecraft/textures/block/cauldron_inner.png")
    water = self.transform_image_top(self.load_image_texture("water.png"))
    # Side texture isn't transparent between the feet, so adjust the texture
    ImageDraw.Draw(side).rectangle((5, 14, 11, 16), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))

    if data == 0:  # Empty
        img = self.build_full_block(top, side, side, side, side)
    else:  # Part or fully filled
        # Is filled in increments of a third, with data indicating how many thirds are filled
        img = self.build_full_block(None, side, side, None, None)
        alpha_over(img, water, (0, 12 - data * 4), water)
        img2 = self.build_full_block(top, None, None, side, side)
        alpha_over(img, img2, (0, 0), img2)

    return img


# end portal and end_gateway
@material(blockid=ids.group_end_portal, transparent=True, nodata=True)
def end_portal(self, blockid, data):
    img = Image.new("RGBA", (24,24), self.bgcolor)
    # generate a black texure with white, blue and grey dots resembling stars
    t = Image.new("RGBA", (16,16), (0,0,0,255))
    for color in [(155,155,155,255), (100,255,100,255), (255,255,255,255)]:
        for i in range(6):
            x = randint(0,15)
            y = randint(0,15)
            t.putpixel((x,y),color)
    if blockid == 209: # end_gateway
        return  self.build_block(t, t)
        
    t = self.transform_image_top(t)
    alpha_over(img, t, (0,0), t)

    return img


# end portal frame (data range 8 to get all orientations of filled)
@material(blockid=ids.block_minecraft__end_portal_frame, data=list(range(8)), transparent=True, solid=True, nospawn=True)
def end_portal_frame(self, blockid, data):
    # Do rotation, only seems to affect ender eye & top of frame
    data = data & 0b100 | ((self.rotation + (data & 0b11)) % 4)

    top = self.load_image_texture("assets/minecraft/textures/block/end_portal_frame_top.png").copy()
    top = top.rotate((data % 4) * 90)
    side = self.load_image_texture("assets/minecraft/textures/block/end_portal_frame_side.png")
    img = self.build_full_block((top, 4), None, None, side, side)
    if data & 0x4 == 0x4:  # ender eye on it
        # generate the eye
        eye_t = self.load_image_texture("assets/minecraft/textures/block/end_portal_frame_eye.png").copy()
        eye_t_s = eye_t.copy()
        # cut out from the texture the side and the top of the eye
        ImageDraw.Draw(eye_t).rectangle((0, 0, 15, 4), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
        ImageDraw.Draw(eye_t_s).rectangle((0, 4, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
        # transform images and paste
        eye = self.transform_image_top(eye_t.rotate((data % 4) * 90))
        eye_s = self.transform_image_side(eye_t_s)
        eye_os = eye_s.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, eye_s, (5, 5), eye_s)
        alpha_over(img, eye_os, (9, 5), eye_os)
        alpha_over(img, eye, (0, 0), eye)

    return img




# inactive redstone lamp active redstone lamp
@material(blockid=ids.block_minecraft__redstone_lamp, data=[0, 1], solid=True)
def redstone_lamp(self, blockid, data):
    if data == 0:
        tex = self.load_image_texture("assets/minecraft/textures/block/redstone_lamp.png")
    else:
        tex = self.load_image_texture("assets/minecraft/textures/block/redstone_lamp_on.png")
    return self.build_block(tex, tex)

# daylight sensor.  
@material(blockid=[ids.block_minecraft__daylight_detector], transparent=True)
def daylight_sensor(self, blockid, data):
    if blockid == ids.block_minecraft__daylight_detector: # daylight sensor
        top = self.load_image_texture("assets/minecraft/textures/block/daylight_detector_top.png")
    else: # inverted daylight sensor
        top = self.load_image_texture("assets/minecraft/textures/block/daylight_detector_inverted_top.png")
    side = self.load_image_texture("assets/minecraft/textures/block/daylight_detector_side.png")

    # cut the side texture in half
    mask = side.crop((0,8,16,16))
    side = Image.new(side.mode, side.size, self.bgcolor)
    alpha_over(side, mask,(0,0,16,8), mask)

    # plain slab
    top = self.transform_image_top(top)
    side = self.transform_image_side(side)
    otherside = side.transpose(Image.FLIP_LEFT_RIGHT)
    
    sidealpha = side.split()[3]
    side = ImageEnhance.Brightness(side).enhance(0.9)
    side.putalpha(sidealpha)
    othersidealpha = otherside.split()[3]
    otherside = ImageEnhance.Brightness(otherside).enhance(0.8)
    otherside.putalpha(othersidealpha)
    
    img = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(img, side, (0,12), side)
    alpha_over(img, otherside, (12,12), otherside)
    alpha_over(img, top, (0,6), top)
    
    return img


# cocoa plant
@material(blockid=ids.block_minecraft__cocoa, data=list(range(12)), transparent=True)
def cocoa_plant(self, blockid, data):
    orientation = data & 3
    # rotation
    if self.rotation == 1:
        if orientation == 0: orientation = 1
        elif orientation == 1: orientation = 2
        elif orientation == 2: orientation = 3
        elif orientation == 3: orientation = 0
    elif self.rotation == 2:
        if orientation == 0: orientation = 2
        elif orientation == 1: orientation = 3
        elif orientation == 2: orientation = 0
        elif orientation == 3: orientation = 1
    elif self.rotation == 3:
        if orientation == 0: orientation = 3
        elif orientation == 1: orientation = 0
        elif orientation == 2: orientation = 1
        elif orientation == 3: orientation = 2

    size = data & 12
    if size == 8: # big
        t = self.load_image_texture("assets/minecraft/textures/block/cocoa_stage2.png")
        c_left = (0,3)
        c_right = (8,3)
        c_top = (5,2)
    elif size == 4: # normal
        t = self.load_image_texture("assets/minecraft/textures/block/cocoa_stage1.png")
        c_left = (-2,2)
        c_right = (8,2)
        c_top = (5,2)
    elif size == 0: # small
        t = self.load_image_texture("assets/minecraft/textures/block/cocoa_stage0.png")
        c_left = (-3,2)
        c_right = (6,2)
        c_top = (5,2)

    # let's get every texture piece necessary to do this
    stalk = t.copy()
    ImageDraw.Draw(stalk).rectangle((0,0,11,16),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(stalk).rectangle((12,4,16,16),outline=(0,0,0,0),fill=(0,0,0,0))
    
    top = t.copy() # warning! changes with plant size
    ImageDraw.Draw(top).rectangle((0,7,16,16),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(top).rectangle((7,0,16,6),outline=(0,0,0,0),fill=(0,0,0,0))

    side = t.copy() # warning! changes with plant size
    ImageDraw.Draw(side).rectangle((0,0,6,16),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(side).rectangle((0,0,16,3),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(side).rectangle((0,14,16,16),outline=(0,0,0,0),fill=(0,0,0,0))
    
    # first compose the block of the cocoa plant
    block = Image.new("RGBA", (24,24), self.bgcolor)
    tmp = self.transform_image_side(side).transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over (block, tmp, c_right,tmp) # right side
    tmp = tmp.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over (block, tmp, c_left,tmp) # left side
    tmp = self.transform_image_top(top)
    alpha_over(block, tmp, c_top,tmp)
    if size == 0:
        # fix a pixel hole
        block.putpixel((6,9), block.getpixel((6,10)))

    # compose the cocoa plant
    img = Image.new("RGBA", (24,24), self.bgcolor)
    if orientation in (2,3): # south and west
        tmp = self.transform_image_side(stalk).transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, block,(-1,-2), block)
        alpha_over(img, tmp, (4,-2), tmp)
        if orientation == 3:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation in (0,1): # north and east
        tmp = self.transform_image_side(stalk.transpose(Image.FLIP_LEFT_RIGHT))
        alpha_over(img, block,(-1,5), block)
        alpha_over(img, tmp, (2,12), tmp)
        if orientation == 0:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)

    return img

# command block
@material(blockid=ids.group_command_block, solid=True, nodata=True)
def command_block(self, blockid, data):
    if blockid == ids.block_minecraft__repeating_command_block:
        front = self.load_image_texture("assets/minecraft/textures/block/repeating_command_block_front.png")
        side = self.load_image_texture("assets/minecraft/textures/block/repeating_command_block_side.png")
        back = self.load_image_texture("assets/minecraft/textures/block/repeating_command_block_back.png")
    elif blockid == ids.block_minecraft__chain_command_block:
        front = self.load_image_texture("assets/minecraft/textures/block/chain_command_block_front.png")
        side = self.load_image_texture("assets/minecraft/textures/block/chain_command_block_side.png")
        back = self.load_image_texture("assets/minecraft/textures/block/chain_command_block_back.png")
    else:
        front = self.load_image_texture("assets/minecraft/textures/block/command_block_front.png")
        side = self.load_image_texture("assets/minecraft/textures/block/command_block_side.png")
        back = self.load_image_texture("assets/minecraft/textures/block/command_block_back.png")
    return self.build_full_block(side, side, back, front, side)

# beacon block
# at the moment of writing this, it seems the beacon block doens't use
# the data values
@material(blockid=ids.block_minecraft__beacon, transparent=True, nodata = True)
def beacon(self, blockid, data):
    # generate the three pieces of the block
    t = self.load_image_texture("assets/minecraft/textures/block/glass.png")
    glass = self.build_block(t,t)
    t = self.load_image_texture("assets/minecraft/textures/block/obsidian.png")
    obsidian = self.build_full_block((t,12),None, None, t, t)
    obsidian = obsidian.resize((20,20), Image.ANTIALIAS)
    t = self.load_image_texture("assets/minecraft/textures/block/beacon.png")
    crystal = self.build_block(t,t)
    crystal = crystal.resize((16,16),Image.ANTIALIAS)
    
    # compose the block
    img = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(img, obsidian, (2, 4), obsidian)
    alpha_over(img, crystal, (4,3), crystal)
    alpha_over(img, glass, (0,0), glass)
    
    return img

# cobblestone and mossy cobblestone walls, chorus plants, mossy stone brick walls
# one additional bit of data value added for mossy and cobblestone
@material(blockid=ids.group_wall_chorus, data=list(range(32)), transparent=True, nospawn=True)
def cobblestone_wall(self, blockid, data):
    walls_id_to_tex = {
          ids.block_minecraft__chorus_plant: "assets/minecraft/textures/block/chorus_plant.png", # chorus plants
        ids.block_minecraft__andesite_wall: "assets/minecraft/textures/block/andesite.png",
        ids.block_minecraft__brick_wall: "assets/minecraft/textures/block/bricks.png",
        ids.block_minecraft__cobblestone_wall: "assets/minecraft/textures/block/cobblestone.png",
        ids.block_minecraft__diorite_wall: "assets/minecraft/textures/block/diorite.png",
        ids.block_minecraft__end_stone_brick_wall: "assets/minecraft/textures/block/end_stone_bricks.png",
        ids.block_minecraft__granite_wall: "assets/minecraft/textures/block/granite.png",
        ids.block_minecraft__mossy_cobblestone_wall: "assets/minecraft/textures/block/mossy_cobblestone.png",
        ids.block_minecraft__mossy_stone_brick_wall: "assets/minecraft/textures/block/mossy_stone_bricks.png",
        ids.block_minecraft__nether_brick_wall: "assets/minecraft/textures/block/nether_bricks.png",
        ids.block_minecraft__prismarine_wall: "assets/minecraft/textures/block/prismarine.png",
        ids.block_minecraft__red_nether_brick_wall: "assets/minecraft/textures/block/red_nether_bricks.png",
        ids.block_minecraft__red_sandstone_wall: "assets/minecraft/textures/block/red_sandstone.png",
        ids.block_minecraft__sandstone_wall: "assets/minecraft/textures/block/sandstone.png",
        ids.block_minecraft__stone_brick_wall: "assets/minecraft/textures/block/stone_bricks.png",
        ids.block_minecraft__polished_blackstone_brick_wall: "assets/minecraft/textures/block/polished_blackstone_bricks.png",
        ids.block_minecraft__polished_blackstone_wall: "assets/minecraft/textures/block/polished_blackstone.png",
        ids.block_minecraft__blackstone_wall: "assets/minecraft/textures/block/blackstone.png",
    }
    t = self.load_image_texture(walls_id_to_tex[blockid]).copy()

    wall_pole_top = t.copy()
    wall_pole_side = t.copy()
    wall_side_top = t.copy()
    wall_side = t.copy()
    # _full is used for walls without pole
    wall_side_top_full = t.copy()
    wall_side_full = t.copy()

    # generate the textures of the wall
    ImageDraw.Draw(wall_pole_top).rectangle((0,0,3,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_pole_top).rectangle((12,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_pole_top).rectangle((0,0,15,3),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_pole_top).rectangle((0,12,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    ImageDraw.Draw(wall_pole_side).rectangle((0,0,3,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_pole_side).rectangle((12,0,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # Create the sides and the top of the pole
    wall_pole_side = self.transform_image_side(wall_pole_side)
    wall_pole_other_side = wall_pole_side.transpose(Image.FLIP_LEFT_RIGHT)
    wall_pole_top = self.transform_image_top(wall_pole_top)

    # Darken the sides slightly. These methods also affect the alpha layer,
    # so save them first (we don't want to "darken" the alpha layer making
    # the block transparent)
    sidealpha = wall_pole_side.split()[3]
    wall_pole_side = ImageEnhance.Brightness(wall_pole_side).enhance(0.8)
    wall_pole_side.putalpha(sidealpha)
    othersidealpha = wall_pole_other_side.split()[3]
    wall_pole_other_side = ImageEnhance.Brightness(wall_pole_other_side).enhance(0.7)
    wall_pole_other_side.putalpha(othersidealpha)

    # Compose the wall pole
    wall_pole = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(wall_pole,wall_pole_side, (3,4),wall_pole_side)
    alpha_over(wall_pole,wall_pole_other_side, (9,4),wall_pole_other_side)
    alpha_over(wall_pole,wall_pole_top, (0,0),wall_pole_top)

    # create the sides and the top of a wall attached to a pole
    ImageDraw.Draw(wall_side).rectangle((0,0,15,2),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side).rectangle((0,0,11,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side_top).rectangle((0,0,11,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side_top).rectangle((0,0,15,4),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side_top).rectangle((0,11,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    # full version, without pole
    ImageDraw.Draw(wall_side_full).rectangle((0,0,15,2),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side_top_full).rectangle((0,4,15,15),outline=(0,0,0,0),fill=(0,0,0,0))
    ImageDraw.Draw(wall_side_top_full).rectangle((0,4,15,15),outline=(0,0,0,0),fill=(0,0,0,0))

    # compose the sides of a wall atached to a pole
    tmp = Image.new("RGBA", (24,24), self.bgcolor)
    wall_side = self.transform_image_side(wall_side)
    wall_side_top = self.transform_image_top(wall_side_top)

    # Darken the sides slightly. These methods also affect the alpha layer,
    # so save them first (we don't want to "darken" the alpha layer making
    # the block transparent)
    sidealpha = wall_side.split()[3]
    wall_side = ImageEnhance.Brightness(wall_side).enhance(0.7)
    wall_side.putalpha(sidealpha)

    alpha_over(tmp,wall_side, (0,0),wall_side)
    alpha_over(tmp,wall_side_top, (-5,3),wall_side_top)
    wall_side = tmp
    wall_other_side = wall_side.transpose(Image.FLIP_LEFT_RIGHT)

    # compose the sides of the full wall
    tmp = Image.new("RGBA", (24,24), self.bgcolor)
    wall_side_full = self.transform_image_side(wall_side_full)
    wall_side_top_full = self.transform_image_top(wall_side_top_full.rotate(90))

    # Darken the sides slightly. These methods also affect the alpha layer,
    # so save them first (we don't want to "darken" the alpha layer making
    # the block transparent)
    sidealpha = wall_side_full.split()[3]
    wall_side_full = ImageEnhance.Brightness(wall_side_full).enhance(0.7)
    wall_side_full.putalpha(sidealpha)

    alpha_over(tmp,wall_side_full, (4,0),wall_side_full)
    alpha_over(tmp,wall_side_top_full, (3,-4),wall_side_top_full)
    wall_side_full = tmp
    wall_other_side_full = wall_side_full.transpose(Image.FLIP_LEFT_RIGHT)

    # Create img to compose the wall
    img = Image.new("RGBA", (24,24), self.bgcolor)

    # Position wall imgs around the wall bit stick
    pos_top_left = (-5,-2)
    pos_bottom_left = (-8,4)
    pos_top_right = (5,-3)
    pos_bottom_right = (7,4)
    
    # +x axis points top right direction
    # +y axis points bottom right direction
    # There are two special cases for wall without pole.
    # Normal case: 
    # First compose the walls in the back of the image, 
    # then the pole and then the walls in the front.
    if (data == 0b1010) or (data == 0b11010):
        alpha_over(img, wall_other_side_full,(0,2), wall_other_side_full)
    elif (data == 0b0101) or (data == 0b10101):
        alpha_over(img, wall_side_full,(0,2), wall_side_full)
    else:
        if (data & 0b0001) == 1:
            alpha_over(img,wall_side, pos_top_left,wall_side)                # top left
        if (data & 0b1000) == 8:
            alpha_over(img,wall_other_side, pos_top_right,wall_other_side)    # top right

        alpha_over(img,wall_pole,(0,0),wall_pole)
            
        if (data & 0b0010) == 2:
            alpha_over(img,wall_other_side, pos_bottom_left,wall_other_side)      # bottom left    
        if (data & 0b0100) == 4:
            alpha_over(img,wall_side, pos_bottom_right,wall_side)                  # bottom right
    
    return img

# anvils
@material(blockid=ids.group_anvil, data=list(range(12)), transparent=True, nospawn=True)
def anvil(self, blockid, data):
    # anvils only have two orientations, invert it for rotations 1 and 3
    orientation = data & 0x1
    if self.rotation in (1, 3):
        if orientation == 1:
            orientation = 0
        else:
            orientation = 1

    # get the correct textures
    # the bits 0x4 and 0x8 determine how damaged is the anvil
    if blockid == ids.block_minecraft__anvil:  # non damaged anvil
        top = self.load_image_texture("assets/minecraft/textures/block/anvil_top.png")
    elif blockid == ids.block_minecraft__chipped_anvil:  # slightly damaged
        top = self.load_image_texture("assets/minecraft/textures/block/chipped_anvil_top.png")
    elif blockid == ids.block_minecraft__damaged_anvil:  # very damaged
        top = self.load_image_texture("assets/minecraft/textures/block/damaged_anvil_top.png")
    # everything else use this texture
    big_side = self.load_image_texture("assets/minecraft/textures/block/anvil.png").copy()
    small_side = self.load_image_texture("assets/minecraft/textures/block/anvil.png").copy()
    base = self.load_image_texture("assets/minecraft/textures/block/anvil.png").copy()
    small_base = self.load_image_texture("assets/minecraft/textures/block/anvil.png").copy()

    # cut needed patterns
    ImageDraw.Draw(big_side).rectangle((0, 8, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
    ImageDraw.Draw(small_side).rectangle((0, 0, 2, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
    ImageDraw.Draw(small_side).rectangle((13, 0, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
    ImageDraw.Draw(small_side).rectangle((0, 8, 15, 15), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
    ImageDraw.Draw(base).rectangle((0, 0, 15, 15), outline=(0, 0, 0, 0))
    ImageDraw.Draw(base).rectangle((1, 1, 14, 14), outline=(0, 0, 0, 0))
    ImageDraw.Draw(small_base).rectangle((0, 0, 15, 15), outline=(0, 0, 0, 0))
    ImageDraw.Draw(small_base).rectangle((1, 1, 14, 14), outline=(0, 0, 0, 0))
    ImageDraw.Draw(small_base).rectangle((2, 2, 13, 13), outline=(0, 0, 0, 0))
    ImageDraw.Draw(small_base).rectangle((3, 3, 12, 12), outline=(0, 0, 0, 0))

    # check orientation and compose the anvil
    if orientation == 1:  # bottom-left top-right
        top = top.rotate(90)
        left_side = small_side
        left_pos = (1, 6)
        right_side = big_side
        right_pos = (10, 5)
    else:  # top-left bottom-right
        right_side = small_side
        right_pos = (12, 6)
        left_side = big_side
        left_pos = (3, 5)

    img = Image.new("RGBA", (24, 24), self.bgcolor)

    # darken sides
    alpha = big_side.split()[3]
    big_side = ImageEnhance.Brightness(big_side).enhance(0.8)
    big_side.putalpha(alpha)
    alpha = small_side.split()[3]
    small_side = ImageEnhance.Brightness(small_side).enhance(0.9)
    small_side.putalpha(alpha)
    alpha = base.split()[3]
    base_d = ImageEnhance.Brightness(base).enhance(0.8)
    base_d.putalpha(alpha)

    # compose
    base = self.transform_image_top(base)
    base_d = self.transform_image_top(base_d)
    small_base = self.transform_image_top(small_base)
    top = self.transform_image_top(top)

    alpha_over(img, base_d, (0, 12), base_d)
    alpha_over(img, base_d, (0, 11), base_d)
    alpha_over(img, base_d, (0, 10), base_d)
    alpha_over(img, small_base, (0, 10), small_base)

    alpha_over(img, top, (0, 1), top)  # Fix gap between block edges
    alpha_over(img, top, (0, 0), top)

    left_side = self.transform_image_side(left_side)
    right_side = self.transform_image_side(right_side).transpose(Image.FLIP_LEFT_RIGHT)

    alpha_over(img, left_side, left_pos, left_side)
    alpha_over(img, right_side, right_pos, right_side)

    return img

    
# hopper
@material(blockid=ids.block_minecraft__hopper, data=list(range(4)), transparent=True)
def hopper(self, blockid, data):
    #build the top
    side = self.load_image_texture("assets/minecraft/textures/block/hopper_outside.png")
    top = self.load_image_texture("assets/minecraft/textures/block/hopper_top.png")
    bottom = self.load_image_texture("assets/minecraft/textures/block/hopper_inside.png")
    hop_top = self.build_full_block((top,10), side, side, side, side, side)

    #build a solid block for mid/top
    hop_mid = self.build_full_block((top,5), side, side, side, side, side)
    hop_bot = self.build_block(side,side)

    hop_mid = hop_mid.resize((17,17),Image.ANTIALIAS)
    hop_bot = hop_bot.resize((10,10),Image.ANTIALIAS)
    
    #compose the final block
    img = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(img, hop_bot, (7,14), hop_bot)
    alpha_over(img, hop_mid, (3,3), hop_mid)
    alpha_over(img, hop_top, (0,-6), hop_top)

    return img


# carpet - wool block that's small?
@material(blockid=ids.group_carpet, data=list(range(16)), transparent=True)
def carpet(self, blockid, data):

    if blockid == ids.block_minecraft__white_carpet:
        tex = "assets/minecraft/textures/block/white_wool.png"
    elif blockid == ids.block_minecraft__orange_carpet:
        tex = "assets/minecraft/textures/block/orange_wool.png"
    elif blockid == ids.block_minecraft__magenta_carpet:
        tex = "assets/minecraft/textures/block/magenta_wool.png"
    elif blockid == ids.block_minecraft__light_blue_carpet:
        tex = "assets/minecraft/textures/block/light_blue_wool.png"
    elif blockid == ids.block_minecraft__yellow_carpet:
        tex = "assets/minecraft/textures/block/yellow_wool.png"
    elif blockid == ids.block_minecraft__lime_carpet:
        tex = "assets/minecraft/textures/block/lime_wool.png"
    elif blockid == ids.block_minecraft__pink_carpet:
        tex = "assets/minecraft/textures/block/pink_wool.png"
    elif blockid == ids.block_minecraft__gray_carpet:
        tex = "assets/minecraft/textures/block/gray_wool.png"
    elif blockid == ids.block_minecraft__light_gray_carpet:
        tex = "assets/minecraft/textures/block/light_gray_wool.png"
    elif blockid == ids.block_minecraft__cyan_carpet:
        tex = "assets/minecraft/textures/block/cyan_wool.png"
    elif blockid == ids.block_minecraft__purple_carpet:
        tex = "assets/minecraft/textures/block/purple_wool.png"
    elif blockid == ids.block_minecraft__blue_carpet:
        tex = "assets/minecraft/textures/block/blue_wool.png"
    elif blockid == ids.block_minecraft__brown_carpet:
        tex = "assets/minecraft/textures/block/brown_wool.png"
    elif blockid == ids.block_minecraft__green_carpet:
        tex = "assets/minecraft/textures/block/green_wool.png"
    elif blockid == ids.block_minecraft__red_carpet:
        tex = "assets/minecraft/textures/block/red_wool.png"
    elif blockid == ids.block_minecraft__black_carpet:
        tex = "assets/minecraft/textures/block/black_wool.png"

    texture = self.load_image_texture(tex)

    return self.build_full_block((texture, 15), texture, texture, texture, texture)


@material(blockid=ids.group_tall_sprite, data=list(range(16)), transparent=True)
def flower(self, blockid, data):
    # double_plant_map = ["sunflower", "lilac", "tall_grass", "large_fern", "rose_bush", "peony", "peony", "peony"]
    # plant = double_plant_map[data & 0x7]

    if blockid == ids.block_minecraft__sunflower:
        plant = "sunflower"
    elif blockid == ids.block_minecraft__lilac:
        plant = "lilac"
    elif blockid == ids.block_minecraft__tall_grass:
        plant = "tall_grass"
    elif blockid == ids.block_minecraft__large_fern:
        plant = "large_fern"
    elif blockid == ids.block_minecraft__rose_bush:
        plant = "rose_bush"
    elif blockid == ids.block_minecraft__peony:
        plant = "peony"

    if data == 1:
        part = "top"
    else:
        part = "bottom"

    png = "assets/minecraft/textures/block/%s_%s.png" % (plant,part)
    texture = self.load_image_texture(png)
    img = self.build_sprite(texture)  #build_billboard

    # #sunflower top
    # if data == 8:
    #     bloom_tex = self.load_image_texture("assets/minecraft/textures/block/sunflower_front.png")
    #     alpha_over(img, bloom_tex.resize((14, 11), Image.ANTIALIAS), (5,5))

    return img

# chorus flower
@material(blockid=ids.block_minecraft__chorus_flower, data=list(range(6)), solid=True)
def chorus_flower(self, blockid, data):
    # aged 5, dead
    if data == 5:
        texture = self.load_image_texture("assets/minecraft/textures/block/chorus_flower_dead.png")
    else:
        texture = self.load_image_texture("assets/minecraft/textures/block/chorus_flower.png")

    return self.build_block(texture,texture)


# frosted ice
@material(blockid=ids.block_minecraft__frosted_ice, data=list(range(4)), solid=True)
def frosted_ice(self, blockid, data):
    img = self.load_image_texture("assets/minecraft/textures/block/frosted_ice_%d.png" % data)
    return self.build_block(img, img)


# observer
@material(blockid=ids.block_minecraft__observer, data=[0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13], solid=True, nospawn=True)
def observer(self, blockid, data):
    # Do rotation
    if self.rotation in [1, 2, 3] and (data & 0b111) in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = (data & 0b1000) | rotation_map[self.rotation][data & 0b111]

    front = self.load_image_texture("assets/minecraft/textures/block/observer_front.png")
    side = self.load_image_texture("assets/minecraft/textures/block/observer_side.png")
    top = self.load_image_texture("assets/minecraft/textures/block/observer_top.png")
    file_name_back = "observer_back_on" if data & 0b1000 else "observer_back"
    back = self.load_image_texture("assets/minecraft/textures/block/%s.png" % file_name_back)

    if data & 0b0111 == 0:    # Down
        img = self.build_full_block(back, None, None, side.rotate(90), top)
    elif data & 0b0111 == 1:  # Up
        img = self.build_full_block(front.rotate(180), None, None, side.rotate(90), top.rotate(180))
    elif data & 0b0111 == 2:  # East
        img = self.build_full_block(top.rotate(180), None, None, side, back)
    elif data & 0b0111 == 3:  # West
        img = self.build_full_block(top, None, None, side, front)
    elif data & 0b0111 == 4:  # North
        img = self.build_full_block(top.rotate(270), None, None, front, side)
    elif data & 0b0111 == 5:  # South
        img = self.build_full_block(top.rotate(90), None, None, back, side)

    return img


# shulker box
@material(blockid=ids.group_shulker, data=list(range(6)), solid=True, nospawn=True)
def shulker_box(self, blockid, data):
    # Do rotation
    if self.rotation in [1, 2, 3] and data in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = rotation_map[self.rotation][data]

    if blockid == ids.block_minecraft__shulker_box:
        # Uncolored shulker box
        file_name = "shulker.png"
    elif blockid == ids.block_minecraft__white_shulker_box:
        file_name = "shulker_white.png"
    elif blockid == ids.block_minecraft__orange_shulker_box:
        file_name = "shulker_orange.png"
    elif blockid == ids.block_minecraft__magenta_shulker_box:
        file_name = "shulker_magenta.png"
    elif blockid == ids.block_minecraft__light_blue_shulker_box:
        file_name = "shulker_light_blue.png"
    elif blockid == ids.block_minecraft__yellow_shulker_box:
        file_name = "shulker_yellow.png"
    elif blockid == ids.block_minecraft__lime_shulker_box:
        file_name = "shulker_lime.png"
    elif blockid == ids.block_minecraft__pink_shulker_box:
        file_name = "shulker_pink.png"
    elif blockid == ids.block_minecraft__gray_shulker_box:
        file_name = "shulker_gray.png"
    elif blockid == ids.block_minecraft__light_gray_shulker_box:
        file_name = "shulker_light_gray.png"
    elif blockid == ids.block_minecraft__cyan_shulker_box:
        file_name = "shulker_cyan.png"
    elif blockid == ids.block_minecraft__purple_shulker_box:
        file_name = "shulker_purple.png"
    elif blockid == ids.block_minecraft__blue_shulker_box:
        file_name = "shulker_blue.png"
    elif blockid == ids.block_minecraft__brown_shulker_box:
        file_name = "shulker_brown.png"
    elif blockid == ids.block_minecraft__green_shulker_box:
        file_name = "shulker_green.png"
    elif blockid == ids.block_minecraft__red_shulker_box:
        file_name = "shulker_red.png"
    elif blockid == ids.block_minecraft__black_shulker_box:
        file_name = "shulker_black.png"

    shulker_t = self.load_image("assets/minecraft/textures/entity/shulker/%s" % file_name).copy()
    w, h = shulker_t.size
    res = w // 4
    # Cut out the parts of the shulker texture we need for the box
    top = shulker_t.crop((res, 0, res * 2, res))
    bottom = shulker_t.crop((res * 2, int(res * 1.75), res * 3, int(res * 2.75)))
    side_top = shulker_t.crop((0, res, res, int(res * 1.75)))
    side_bottom = shulker_t.crop((0, int(res * 2.75), res, int(res * 3.25)))
    side = Image.new('RGBA', (res, res))
    side.paste(side_top, (0, 0), side_top)
    side.paste(side_bottom, (0, res // 2), side_bottom)

    if data == 0:    # down
        side = side.rotate(180)
        img = self.build_full_block(bottom, None, None, side, side)
    elif data == 1:  # up
        img = self.build_full_block(top, None, None, side, side)
    elif data == 2:  # east
        img = self.build_full_block(side, None, None, side.rotate(90), bottom)
    elif data == 3:  # west
        img = self.build_full_block(side.rotate(180), None, None, side.rotate(270), top)
    elif data == 4:  # north
        img = self.build_full_block(side.rotate(90), None, None, top, side.rotate(270))
    elif data == 5:  # south
        img = self.build_full_block(side.rotate(270), None, None, bottom, side.rotate(90))

    return img


# structure block
@material(blockid=ids.block_minecraft__structure_block, data=list(range(4)), solid=True)
def structure_block(self, blockid, data):
    if data == 0:
        img = self.load_image_texture("assets/minecraft/textures/block/structure_block_save.png")
    elif data == 1:
        img = self.load_image_texture("assets/minecraft/textures/block/structure_block_load.png")
    elif data == 2:
        img = self.load_image_texture("assets/minecraft/textures/block/structure_block_corner.png")
    elif data == 3:
        img = self.load_image_texture("assets/minecraft/textures/block/structure_block_data.png")
    return self.build_block(img, img)


# Jigsaw block
@material(blockid=ids.block_minecraft__jigsaw, data=list(range(6)), solid=True)
def jigsaw_block(self, blockid, data):
    # Do rotation
    if self.rotation in [1, 2, 3] and data in [2, 3, 4, 5]:
        rotation_map = {1: {2: 5, 3: 4, 4: 2, 5: 3},
                        2: {2: 3, 3: 2, 4: 5, 5: 4},
                        3: {2: 4, 3: 5, 4: 3, 5: 2}}
        data = rotation_map[self.rotation][data]

    top = self.load_image_texture("assets/minecraft/textures/block/jigsaw_top.png")
    bottom = self.load_image_texture("assets/minecraft/textures/block/jigsaw_bottom.png")
    side = self.load_image_texture("assets/minecraft/textures/block/jigsaw_side.png")

    if data == 0:    # Down
        img = self.build_full_block(bottom.rotate(self.rotation * 90), None, None,
                                    side.rotate(180), side.rotate(180))
    elif data == 1:  # Up
        img = self.build_full_block(top.rotate(self.rotation * 90), None, None, side, side)
    elif data == 2:  # North
        img = self.build_full_block(side, None, None, side.rotate(90), bottom.rotate(180))
    elif data == 3:  # South
        img = self.build_full_block(side.rotate(180), None, None, side.rotate(270), top.rotate(270))
    elif data == 4:  # West
        img = self.build_full_block(side.rotate(90), None, None, top.rotate(180), side.rotate(270))
    elif data == 5:  # East
        img = self.build_full_block(side.rotate(270), None, None, bottom.rotate(180),
                                    side.rotate(90))

    return img

# beetroots(207), berry bushes (11505)
@material(blockid=ids.group_age_4, data=list(range(4)), transparent=True, nospawn=True)
def crops(self, blockid, data):
    crops_id_to_tex = {
        ids.block_minecraft__beetroots: "assets/minecraft/textures/block/beetroots_stage%d.png",
      ids.block_minecraft__sweet_berry_bush: "assets/minecraft/textures/block/sweet_berry_bush_stage%d.png",
      ids.block_minecraft__nether_wart: "assets/minecraft/textures/block/nether_wart_stage%d.png",
    }
    if blockid == ids.block_minecraft__nether_wart:
        raw_tex = crops_id_to_tex[blockid] % {0: 0, 1: 1, 2: 1, 3: 2}[data]
    else:
        raw_tex = crops_id_to_tex[blockid] % data
    raw_crop = self.load_image_texture(raw_tex)

    crop1 = self.transform_image_top(raw_crop)
    crop2 = self.transform_image_side(raw_crop)
    crop3 = crop2.transpose(Image.FLIP_LEFT_RIGHT)

    img = Image.new("RGBA", (24,24), self.bgcolor)
    alpha_over(img, crop1, (0,12), crop1)
    alpha_over(img, crop2, (6,3), crop2)
    alpha_over(img, crop3, (6,3), crop3)
    return img


# Glazed Terracotta
@material(blockid=ids.group_glazed_terracotta, data=list(range(4)), solid=True)
def glazed_terracotta(self, blockid, data):
    # Do rotation
    data = (self.rotation + data) % 4

    if blockid == ids.block_minecraft__white_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/white_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__orange_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/orange_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__magenta_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/magenta_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__light_blue_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/light_blue_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__yellow_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/yellow_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__lime_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/lime_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__pink_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/pink_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__gray_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/gray_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__light_gray_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/light_gray_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__cyan_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/cyan_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__purple_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/purple_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__blue_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/blue_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__brown_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/brown_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__green_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/green_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__red_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/red_glazed_terracotta.png")
    elif blockid == ids.block_minecraft__black_glazed_terracotta:
        texture = self.load_image_texture("assets/minecraft/textures/block/black_glazed_terracotta.png")

    texture_side4 = texture.transpose(Image.FLIP_LEFT_RIGHT)

    if data == 0:    # South
        return self.build_full_block(texture, None, None, texture, texture_side4.rotate(270))
    elif data == 1:  # West
        return self.build_full_block(texture.rotate(270), None, None, texture.rotate(90),
                                     texture_side4.rotate(180))
    elif data == 2:  # North
        return self.build_full_block(texture.rotate(180), None, None, texture.rotate(180),
                                     texture_side4.rotate(90))
    elif data == 3:  # East
        return self.build_full_block(texture.rotate(90), None, None, texture.rotate(270),
                                     texture_side4)


# beehive and bee_nest
@material(blockid=ids.group_bee, data=list(range(8)), solid=True)
def beehivenest(self, blockid, data):    
    if blockid == ids.block_minecraft__beehive: #beehive
        t_top = self.load_image("assets/minecraft/textures/block/beehive_end.png")
        t_side = self.load_image("assets/minecraft/textures/block/beehive_side.png")
        t_front = self.load_image("assets/minecraft/textures/block/beehive_front.png")
        t_front_honey = self.load_image("assets/minecraft/textures/block/beehive_front_honey.png")
    elif blockid == ids.block_minecraft__bee_nest: #bee_nest
        t_top = self.load_image("assets/minecraft/textures/block/bee_nest_top.png")
        t_side = self.load_image("assets/minecraft/textures/block/bee_nest_side.png")
        t_front = self.load_image("assets/minecraft/textures/block/bee_nest_front.png")
        t_front_honey = self.load_image("assets/minecraft/textures/block/bee_nest_front_honey.png")

    if data >= 4:
        front = t_front_honey
    else:
        front = t_front

    if self.rotation == 0: # rendering north upper-left
        if data == 0 or data == 4: # south
            return self.build_full_block(t_top, t_side, t_side, t_side, front)
        elif data == 1 or data == 5: # west
            return self.build_full_block(t_top, t_side, t_side, front, t_side)
        elif data == 2 or data == 6: # north
            return self.build_full_block(t_top, t_side, front, t_side, t_side)
        elif data == 3 or data == 7: # east
            return self.build_full_block(t_top, front, t_side, t_side, t_side)

    elif self.rotation == 1: # north upper-right
        if data == 0 or data == 4: # south
            return self.build_full_block(t_top, t_side, t_side, front, t_side)
        elif data == 1 or data == 5: # west
            return self.build_full_block(t_top, t_side, front, t_side, t_side)
        elif data == 2 or data == 6: # north
            return self.build_full_block(t_top, front, t_side, t_side, t_side)
        elif data == 3 or data == 7: # east
            return self.build_full_block(t_top, t_side, t_side, t_side, front)            

    elif self.rotation == 2: # north lower-right
        if data == 0 or data == 4: # south
            return self.build_full_block(t_top, t_side, front, t_side, t_side)
        elif data == 1 or data == 5: # west
            return self.build_full_block(t_top, front, t_side, t_side, t_side)
        elif data == 2 or data == 6: # north
            return self.build_full_block(t_top, t_side, t_side, t_side, front)
        elif data == 3 or data == 7: # east
            return self.build_full_block(t_top, t_side, t_side, front, t_side)
            
    elif self.rotation == 3: # north lower-left
        if data == 0 or data == 4: # south
            return self.build_full_block(t_top, front, t_side, t_side, t_side)
        elif data == 1 or data == 5: # west
            return self.build_full_block(t_top, t_side, t_side, t_side, front)
        elif data == 2 or data == 6: # north
            return self.build_full_block(t_top, t_side, t_side, front, t_side)
        elif data == 3 or data == 7: # east
            return self.build_full_block(t_top, t_side, front, t_side, t_side)


# Barrel
@material(blockid=ids.block_minecraft__barrel, data=list(range(12)), solid=True)
def barrel(self, blockid, data):
    t_bottom = self.load_image("assets/minecraft/textures/block/barrel_bottom.png")
    t_side = self.load_image("assets/minecraft/textures/block/barrel_side.png")

    if data & 0x01:
        t_top = self.load_image("assets/minecraft/textures/block/barrel_top_open.png")
    else:
        t_top = self.load_image("assets/minecraft/textures/block/barrel_top.png")
    data = data >> 1

    if data == 0:       # up
        return self.build_full_block(t_top, None, None, t_side, t_side)
    elif data == 1:     # down
        t_side = t_side.rotate(180)
        return self.build_full_block(t_bottom, None, None, t_side, t_side)
    elif data == 2:     # south
        return self.build_full_block(t_side.rotate(180), None, None, t_side.rotate(270), t_top)
    elif data == 3:     # east
        return self.build_full_block(t_side.rotate(270), None, None, t_bottom, t_side.rotate(90))
    elif data == 4:     # north
        return self.build_full_block(t_side, None, None, t_side.rotate(90), t_bottom)
    else:               # west
        return self.build_full_block(t_side.rotate(90), None, None, t_top, t_side.rotate(270))


# Campfire (11506) and soul campfire (1003)
@material(blockid=ids.group_campfire, data=list(range(8)), solid=True, transparent=True, nospawn=True)
def campfire(self, blockid, data):
    # Do rotation, mask to not clobber lit data
    data = data & 0b100 | ((self.rotation + (data & 0b11)) % 4)
    block_name = "campfire" if blockid == 11506 else "soul_campfire"

    # Load textures
    # Fire & lit log textures contain multiple tiles, since both are
    #   16px wide rely on load_image_texture() to crop appropriately
    fire_raw_t = self.load_image_texture("assets/minecraft/textures/block/" + block_name + "_fire.png")
    log_raw_t = self.load_image_texture("assets/minecraft/textures/block/campfire_log.png")
    log_lit_raw_t = self.load_image_texture("assets/minecraft/textures/block/" + block_name + "_log_lit.png")

    def create_tile(img_src, coord_crop, coord_paste, rot):
        # Takes an image, crops a region, optionally rotates the
        #   texture, then finally pastes it onto a 16x16 image
        img_out = Image.new("RGBA", (16, 16), self.bgcolor)
        img_in = img_src.crop(coord_crop)
        if rot != 0:
            img_in = img_in.rotate(rot, expand=True)
        img_out.paste(img_in, coord_paste)
        return img_out

    # Generate bottom
    bottom_t = log_lit_raw_t if data & 0b100 else log_raw_t
    bottom_t = create_tile(bottom_t, (0, 8, 16, 14), (0, 5), 0)
    bottom_t = self.transform_image_top(bottom_t)

    # Generate two variants of a log: one with a lit side, one without
    log_t = Image.new("RGBA", (24, 24), self.bgcolor)
    log_end_t = create_tile(log_raw_t, (0, 4, 4, 8), (12, 6), 0)
    log_side_t = create_tile(log_raw_t, (0, 0, 16, 4), (0, 6), 0)
    log_side_lit_t = create_tile(log_lit_raw_t, (0, 0, 16, 4), (0, 6), 0)

    log_end_t = self.transform_image_side(log_end_t)
    log_top_t = self.transform_image_top(log_side_t)
    log_side_t = self.transform_image_side(log_side_t).transpose(Image.FLIP_LEFT_RIGHT)
    log_side_lit_t = self.transform_image_side(log_side_lit_t).transpose(Image.FLIP_LEFT_RIGHT)

    alpha_over(log_t, log_top_t, (-2, 2), log_top_t)  # Fix some holes at the edges
    alpha_over(log_t, log_top_t, (-2, 1), log_top_t)
    log_lit_t = log_t.copy()

    # Unlit log
    alpha_over(log_t, log_side_t, (5, 0), log_side_t)
    alpha_over(log_t, log_end_t, (-7, 0), log_end_t)

    # Lit log. For unlit fires, just reference the unlit log texture
    if data & 0b100:
        alpha_over(log_lit_t, log_side_lit_t, (5, 0), log_side_lit_t)
        alpha_over(log_lit_t, log_end_t, (-7, 0), log_end_t)
    else:
        log_lit_t = log_t

    # Log parts. Because fire needs to be in the middle of the logs,
    #   split the logs into two parts: Those appearing behind the fire
    #   and those appearing in front of the fire
    logs_back_t = Image.new("RGBA", (24, 24), self.bgcolor)
    logs_front_t = Image.new("RGBA", (24, 24), self.bgcolor)

    # Back logs
    alpha_over(logs_back_t, log_lit_t, (-1, 7), log_lit_t)
    log_tmp_t = logs_back_t.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(logs_back_t, log_tmp_t, (1, -3), log_tmp_t)

    # Front logs
    alpha_over(logs_front_t, log_t, (7, 10), log_t)
    # Due to the awkward drawing order, take a small part of the back
    #   logs that need to be drawn on top of the front logs despite
    #   the front logs being drawn last
    ImageDraw.Draw(log_tmp_t).rectangle((0, 0, 18, 24), outline=(0, 0, 0, 0), fill=(0, 0, 0, 0))
    alpha_over(logs_front_t, log_tmp_t, (1, -3), log_tmp_t)
    log_tmp_t = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(log_tmp_t, log_lit_t, (7, 10), log_lit_t)
    log_tmp_t = log_tmp_t.transpose(Image.FLIP_LEFT_RIGHT)
    alpha_over(logs_front_t, log_tmp_t, (1, -3), log_tmp_t)

    # Compose final image
    img = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(img, bottom_t, (0, 12), bottom_t)
    alpha_over(img, logs_back_t, (0, 0), logs_back_t)
    if data & 0b100:
        fire_t = fire_raw_t.copy()
        if data & 0b11 in [0, 2]:  # North, South
            fire_t = fire_t.transpose(Image.FLIP_LEFT_RIGHT)
        alpha_over(img, fire_t, (4, 4), fire_t)
    alpha_over(img, logs_front_t, (0, 0), logs_front_t)
    if data & 0b11 in [0, 2]:  # North, South
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    return img


# Bell
@material(blockid=ids.block_minecraft__bell, data=list(range(16)), solid=True, transparent=True, nospawn=True)
def bell(self, blockid, data):
    # Do rotation, mask to not clobber attachment data
    data = data & 0b1100 | ((self.rotation + (data & 0b11)) % 4)

    # Load textures
    bell_raw_t = self.load_image("assets/minecraft/textures/entity/bell/bell_body.png")
    bar_raw_t = self.load_image_texture("assets/minecraft/textures/block/dark_oak_planks.png")
    post_raw_t = self.load_image_texture("assets/minecraft/textures/block/stone.png")

    def create_tile(img_src, coord_crop, coord_paste, rot):
        # Takes an image, crops a region, optionally rotates the
        #   texture, then finally pastes it onto a 16x16 image
        img_out = Image.new("RGBA", (16, 16), self.bgcolor)
        img_in = img_src.crop(coord_crop)
        if rot != 0:
            img_in = img_in.rotate(rot, expand=True)
        img_out.paste(img_in, coord_paste)
        return img_out

    # 0 = floor, 1 = ceiling, 2 = single wall, 3 = double wall
    bell_type = (data & 0b1100) >> 2
    # Should the bar/post texture be flipped? Yes if either:
    #   - Attached to floor and East or West facing
    #   - Not attached to floor and North or South facing
    flip_part = ((bell_type == 0 and data & 0b11 in [1, 3]) or
                 (bell_type != 0 and data & 0b11 in [0, 2]))

    # Generate bell
    # Bell side textures varies based on self.rotation
    bell_sides_idx = [(0 - self.rotation) % 4, (3 - self.rotation) % 4]
    # Upper sides
    bell_coord = [x * 6 for x in bell_sides_idx]
    bell_ul_t = create_tile(bell_raw_t, (bell_coord[0], 6, bell_coord[0] + 6, 13), (5, 4), 180)
    bell_ur_t = create_tile(bell_raw_t, (bell_coord[1], 6, bell_coord[1] + 6, 13), (5, 4), 180)
    bell_ul_t = self.transform_image_side(bell_ul_t)
    bell_ur_t = self.transform_image_side(bell_ur_t.transpose(Image.FLIP_LEFT_RIGHT))
    bell_ur_t = bell_ur_t.transpose(Image.FLIP_LEFT_RIGHT)
    # Lower sides
    bell_coord = [x * 8 for x in bell_sides_idx]
    bell_ll_t = create_tile(bell_raw_t, (bell_coord[0], 21, bell_coord[0] + 8, 23), (4, 11), 180)
    bell_lr_t = create_tile(bell_raw_t, (bell_coord[1], 21, bell_coord[1] + 8, 23), (4, 11), 180)
    bell_ll_t = self.transform_image_side(bell_ll_t)
    bell_lr_t = self.transform_image_side(bell_lr_t.transpose(Image.FLIP_LEFT_RIGHT))
    bell_lr_t = bell_lr_t.transpose(Image.FLIP_LEFT_RIGHT)
    # Upper top
    top_rot = (180 + self.rotation * 90) % 360
    bell_ut_t = create_tile(bell_raw_t, (6, 0, 12, 6), (5, 5), top_rot)
    bell_ut_t = self.transform_image_top(bell_ut_t)
    # Lower top
    bell_lt_t = create_tile(bell_raw_t, (8, 13, 16, 21), (4, 4), top_rot)
    bell_lt_t = self.transform_image_top(bell_lt_t)

    bell_t = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(bell_t, bell_lt_t, (0, 8), bell_lt_t)
    alpha_over(bell_t, bell_ll_t, (3, 4), bell_ll_t)
    alpha_over(bell_t, bell_lr_t, (9, 4), bell_lr_t)
    alpha_over(bell_t, bell_ut_t, (0, 3), bell_ut_t)
    alpha_over(bell_t, bell_ul_t, (4, 4), bell_ul_t)
    alpha_over(bell_t, bell_ur_t, (8, 4), bell_ur_t)

    # Generate bar
    if bell_type == 1:  # Ceiling
        # bar_coord:  Left          Right         Top
        bar_coord = [(4, 2, 6, 5), (6, 2, 8, 5), (1, 3, 3, 5)]
        bar_tile_pos = [(7, 1), (7, 1), (7, 7)]
        bar_over_pos = [(6, 3), (7, 2), (0, 0)]
    else:  # Floor, single wall, double wall
        # Note: For a single wall bell, the position of the bar
        #   varies based on facing
        if bell_type == 2 and data & 0b11 in [2, 3]:  # Single wall, North/East facing
            bar_x_sw = 3
            bar_l_pos_sw = (6, 7)
        else:
            bar_x_sw = 0
            bar_l_pos_sw = (4, 8)
        bar_x = [2, None, bar_x_sw, 0][bell_type]
        bar_len = [12, None, 13, 16][bell_type]
        bar_l_pos = [(6, 7), None, bar_l_pos_sw, (4, 8)][bell_type]
        bar_long_coord = (bar_x, 3, bar_x + bar_len, 5)

        bar_coord = [(5, 4, 7, 6), bar_long_coord, bar_long_coord]
        bar_tile_pos = [(2, 1), (bar_x, 1), (bar_x, 7)]
        bar_over_pos = [bar_l_pos, (7, 3), (0, 1)]

    bar_l_t = create_tile(bar_raw_t, bar_coord[0], bar_tile_pos[0], 0)
    bar_r_t = create_tile(bar_raw_t, bar_coord[1], bar_tile_pos[1], 0)
    bar_t_t = create_tile(bar_raw_t, bar_coord[2], bar_tile_pos[2], 0)
    bar_l_t = self.transform_image_side(bar_l_t)
    bar_r_t = self.transform_image_side(bar_r_t.transpose(Image.FLIP_LEFT_RIGHT))
    bar_r_t = bar_r_t.transpose(Image.FLIP_LEFT_RIGHT)
    bar_t_t = self.transform_image_top(bar_t_t)

    bar_t = Image.new("RGBA", (24, 24), self.bgcolor)
    alpha_over(bar_t, bar_t_t, bar_over_pos[2], bar_t_t)
    alpha_over(bar_t, bar_l_t, bar_over_pos[0], bar_l_t)
    alpha_over(bar_t, bar_r_t, bar_over_pos[1], bar_r_t)
    if flip_part:
        bar_t = bar_t.transpose(Image.FLIP_LEFT_RIGHT)

    # Generate post, only applies to floor attached bell
    if bell_type == 0:
        post_l_t = create_tile(post_raw_t, (0, 1, 4, 16), (6,  1), 0)
        post_r_t = create_tile(post_raw_t, (0, 1, 2, 16), (14, 1), 0)
        post_t_t = create_tile(post_raw_t, (0, 0, 2,  4), (14, 6), 0)
        post_l_t = self.transform_image_side(post_l_t)
        post_r_t = self.transform_image_side(post_r_t.transpose(Image.FLIP_LEFT_RIGHT))
        post_r_t = post_r_t.transpose(Image.FLIP_LEFT_RIGHT)
        post_t_t = self.transform_image_top(post_t_t)

        post_back_t = Image.new("RGBA", (24, 24), self.bgcolor)
        post_front_t = Image.new("RGBA", (24, 24), self.bgcolor)
        alpha_over(post_back_t, post_t_t, (0, 1), post_t_t)
        alpha_over(post_back_t, post_l_t, (10, 0), post_l_t)
        alpha_over(post_back_t, post_r_t, (7, 3), post_r_t)
        alpha_over(post_back_t, post_r_t, (6, 3), post_r_t)  # Fix some holes
        alpha_over(post_front_t, post_back_t, (-10, 5), post_back_t)
        if flip_part:
            post_back_t = post_back_t.transpose(Image.FLIP_LEFT_RIGHT)
            post_front_t = post_front_t.transpose(Image.FLIP_LEFT_RIGHT)

    img = Image.new("RGBA", (24, 24), self.bgcolor)
    if bell_type == 0:
        alpha_over(img, post_back_t, (0, 0), post_back_t)
    alpha_over(img, bell_t, (0, 0), bell_t)
    alpha_over(img, bar_t, (0, 0), bar_t)
    if bell_type == 0:
        alpha_over(img, post_front_t, (0, 0), post_front_t)

    return img


# respawn anchor
@material(blockid=ids.block_minecraft__respawn_anchor, data=list(range(0, 5)), solid=True)
def respawn_anchor(self, blockid, data):
    side = self.load_image_texture("assets/minecraft/textures/block/respawn_anchor_side%s.png" % data)

    if data > 0:
        top = self.load_image_texture("assets/minecraft/textures/block/respawn_anchor_top.png")
    else:
        top = self.load_image_texture("assets/minecraft/textures/block/respawn_anchor_top_off.png")

    return self.build_block(top, side)

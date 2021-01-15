#ifndef __MC_ID_H_INCLUDED__
#define __MC_ID_H_INCLUDED__

#include <stdint.h>

enum mc_block_id {
    block_air = 0,

    block_stone = 1,
    block_granite = 2,
    block_polished_granite = 3,
    block_diorite = 4,
    block_polished_diorite = 5,
    block_andesite = 6,
    block_polished_andesite = 7,
    block_grass_block = 8,
    block_dirt = 9,
    block_coarse_dirt = 10,
    block_podzol = 11,
    block_cobblestone = 12,
    block_infested_stone = 232,
    block_infested_cobblestone = 233,
    block_infested_stone_bricks = 234,
    block_infested_mossy_stone_bricks = 235,
    block_infested_cracked_stone_bricks = 236,
    block_infested_chiseled_stone_bricks = 237,

    block_oak_planks = 13,
    block_spruce_planks = 14,
    block_birch_planks = 15,
    block_jungle_planks = 16,
    block_acacia_planks = 17,
    block_dark_oak_planks = 18,
    block_oak_sapling = 19,
    block_spruce_sapling = 20,
    block_birch_sapling = 21,
    block_jungle_sapling = 22,
    block_acacia_sapling = 23,
    block_dark_oak_sapling = 24,
    block_bedrock = 25,

    //TODO flowing water and lava block
    block_flowing_water = 26,
    block_flowing_lava = 28,

    block_water = 26,
    block_lava = 28,
    block_ice = 185,
    block_glass = 67,

    // block_stained_glass = 95,

    block_white_stained_glass = 206,
    block_orange_stained_glass = 207,
    block_magenta_stained_glass = 208,
    block_light_blue_stained_glass = 209,
    block_yellow_stained_glass = 210,
    block_lime_stained_glass = 211,
    block_pink_stained_glass = 212,
    block_gray_stained_glass = 213,
    block_light_gray_stained_glass = 214,
    block_cyan_stained_glass = 215,
    block_purple_stained_glass = 216,
    block_blue_stained_glass = 217,
    block_brown_stained_glass = 218,
    block_green_stained_glass = 219,
    block_red_stained_glass = 220,
    block_black_stained_glass = 221,

    block_sand = 28,
    block_red_sand = 29,

    block_gravel = 30,
    block_gold_ore = 31,
    block_iron_ore = 32,
    block_coal_ore = 33,

    block_nether_gold_ore = 34,

    block_oak_log = 35,
    block_spruce_log = 36,
    block_birch_log = 37,
    block_jungle_log = 38,
    block_acacia_log = 39,
    block_dark_oak_log = 40,

    block_stripped_oak_log = 46,
    block_stripped_spruce_log = 41,
    block_stripped_birch_log = 42,
    block_stripped_jungle_log = 43,
    block_stripped_acacia_log = 44,
    block_stripped_dark_oak_log = 45,

    block_oak_wood = 47,
    block_spruce_wood = 48,
    block_birch_wood = 49,
    block_jungle_wood = 50,
    block_acacia_wood = 51,
    block_dark_oak_wood = 52,
    block_stripped_oak_wood = 53,
    block_stripped_spruce_wood = 54,
    block_stripped_birch_wood = 55,
    block_stripped_jungle_wood = 56,
    block_stripped_acacia_wood = 57,
    block_stripped_dark_oak_wood = 58,

    block_oak_leaves = 59,
    block_spruce_leaves = 60,
    block_birch_leaves = 61,
    block_jungle_leaves = 62,
    block_acacia_leaves = 63,
    block_dark_oak_leaves = 64,

    block_sponge = 65,
    block_wet_sponge = 66,
    block_lapis_ore = 68,
    block_lapis_block = 69,

    block_sandstone = 71,
    block_chiseled_sandstone = 72,
    block_cut_sandstone = 73,

    block_noteblock = 74,

    block_white_bed = 75,
    block_orange_bed = 76,
    block_magenta_bed = 77,
    block_light_blue_bed = 78,
    block_yellow_bed = 79,
    block_lime_bed = 80,
    block_pink_bed = 81,
    block_gray_bed = 82,
    block_light_gray_bed = 83,
    block_cyan_bed = 84,
    block_purple_bed = 85,
    block_blue_bed = 86,
    block_brown_bed = 87,
    block_green_bed = 88,
    block_red_bed = 89,
    block_black_bed = 90,

    block_powered_rail = 91,
    block_detector_rail = 92,

    block_sticky_piston = 93,
    block_piston = 100,
    block_piston_head = 101,

    block_cobweb = 94,

    block_dead_bush = 97,
    block_grass = 95,
    block_fern = 96,
    block_tall_grass = 414,

    block_white_wool = 102,
    block_orange_wool = 103,
    block_magenta_wool = 104,
    block_light_blue_wool = 105,
    block_yellow_wool = 106,
    block_lime_wool = 107,
    block_pink_wool = 108,
    block_gray_wool = 109,
    block_light_gray_wool = 110,
    block_cyan_wool = 111,
    block_purple_wool = 112,
    block_blue_wool = 113,
    block_brown_wool = 114,
    block_green_wool = 115,
    block_red_wool = 116,
    block_black_wool = 117,

    block_poppy = 120,
    block_blue_orchid = 121,
    block_allium = 122,
    block_azure_bluet = 123,
    block_red_tulip = 124,
    block_orange_tulip = 125,
    block_white_tulip = 126,
    block_pink_tulip = 127,
    block_oxeye_daisy = 128,
    block_dandelion = 119,
    block_wither_rose = 130,
    block_cornflower = 129,
    block_lily_of_the_valley = 131,

    block_brown_mushroom = 132,
    block_red_mushroom = 133,

    block_gold_block = 134,
    block_iron_block = 135,

    // block_stone_slab = 44,
    // block_double_stone_slab = 43,
    // block_double_wooden_slab = 125,
    // block_wooden_slab = 126,
    // block_double_stone_slab2 = 181,
    // block_stone_slab2 = 182,
    // block_purpur_double_slab = 204,
    // block_purpur_slab = 205,
    // block_prismarine_slab = 11340,
    // block_dark_prismarine_slab = 11341,
    // block_prismarine_brick_slab = 11342,
    // block_andesite_slab = 11343,
    // block_diorite_slab = 11344,
    // block_granite_slab = 11345,
    // block_polished_andesite_slab = 11346,
    // block_polished_diorite_slab = 11347,
    // block_polished_granite_slab = 11348,
    // block_red_nether_brick_slab = 11349,
    // block_smooth_sandstone_slab = 11350,
    // block_cut_sandstone_slab = 11351,
    // block_smooth_red_sandstone_slab = 11352,
    // block_cut_red_sandstone_slab = 11353,
    // block_end_stone_brick_slab = 11354,
    // block_mossy_cobblestone_slab = 11355,
    // block_mossy_stone_brick_slab = 11356,
    // block_smooth_quartz_slab = 11357,
    // block_smooth_stone_slab = 11358,

    block_oak_slab = 452,
    block_spruce_slab = 453,
    block_birch_slab = 454,
    block_jungle_slab = 455,
    block_acacia_slab = 456,
    block_dark_oak_slab = 457,
    block_petrified_oak_slab = 462,
    block_stone_slab = 458,
    block_sandstone_slab = 460,
    block_cobblestone_slab = 463,
    block_brick_slab = 464,
    block_stone_brick_slab = 465,
    block_nether_brick_slab = 466,
    block_quartz_slab = 467,
    block_red_sandstone_slab = 468,
    block_purpur_slab = 470,
    block_prismarine_slab = 386,
    block_dark_prismarine_slab = 388,
    block_prismarine_brick_slab = 387,
    block_andesite_slab = 650,
    block_diorite_slab = 653,
    block_granite_slab = 649,
    block_polished_andesite_slab = 652,
    block_polished_diorite_slab = 644,
    block_polished_granite_slab = 641,
    block_red_nether_brick_slab = 651,
    block_smooth_sandstone_slab = 647,
    block_cut_sandstone_slab = 461,
    block_smooth_red_sandstone_slab = 642,
    block_cut_red_sandstone_slab = 469,
    block_end_stone_brick_slab = 646,
    block_mossy_cobblestone_slab = 645,
    block_mossy_stone_brick_slab = 643,
    block_smooth_quartz_slab = 648,
    block_smooth_stone_slab = 459,

    block_brick = 136,
    block_tnt = 137,
                
    block_bookshelf = 138,
    block_mossy_cobblestone = 139,
    block_obsidian = 140,

    block_torch = 141,
    block_wall_torch = 142,
    block_redstone_torch = 181,
    block_redstone_wall_torch = 182,

    block_fire = 143,
    block_spawner = 145,

    block_oak_stairs = 146,
    block_cobblestone_stairs = 164,
    block_brick_stairs = 251,
    block_stone_brick_stairs = 252,
    block_nether_brick_stairs = 257,
    block_sandstone_stairs = 268,
    block_spruce_stairs = 274,
    block_birch_stairs = 275,
    block_jungle_stairs = 276,
    block_quartz_stairs = 340,
    block_acacia_stairs = 375,
    block_dark_oak_stairs = 376,
    block_red_sandstone_stairs = 451,
    block_purpur_stairs = 495,
    block_prismarine_stairs = 383,
    block_dark_prismarine_stairs = 385,
    block_prismarine_brick_stairs = 384,
    block_mossy_stone_brick_stairs = 629,
    block_mossy_cobblestone_stairs = 631,
    block_smooth_sandstone_stairs = 634,
    block_smooth_quartz_stairs = 635,
    block_polished_granite_stairs = 627,
    block_polished_diorite_stairs = 630,
    block_polished_andesite_stairs = 639,
    block_stone_stairs = 633,
    block_granite_stairs = 636,
    block_diorite_stairs = 640,
    block_andesite_stairs = 637,
    block_end_stone_brick_stairs = 632,
    block_red_nether_brick_stairs = 638,
    block_smooth_red_sandstone_stairs = 628,

    block_chest = 147,
    block_ender_chest = 270,
    block_trapped_chest = 329,

    block_redstone_wire = 148,

    block_diamond_ore = 149,
    block_diamond_block = 150,
    block_crafting_table = 151,
    block_fletching_table = 671,
    block_cartography_table = 672,
    block_smithing_table = 675,

    block_wheat = 152,

    block_farmland = 153,
    block_grass_path = 498,

    block_dispenser = 70,
    block_furnace = 154,
    block_dropper = 342,
    block_blast_furnace = 670,
    block_smoker = 669,

    block_oak_sign = 155,
    block_spruce_sign = 156,
    block_birch_sign = 157,
    block_jungle_sign = 158,
    block_acacia_sign = 159,
    block_dark_oak_sign = 160,

    block_oak_door = 161,
    block_iron_door = 173,
    block_spruce_door = 485,
    block_birch_door = 486,
    block_jungle_door = 487,
    block_acacia_door = 488,
    block_dark_oak_door = 489,

    block_ladder = 162,

    block_rail = 163,
    block_activator_rail = 341,

    block_oak_wall_sign = 165,
    block_spruce_wall_sign = 166,
    block_birch_wall_sign = 167,
    block_jungle_wall_sign = 168,
    block_acacia_wall_sign = 169,
    block_dark_oak_wall_sign = 170,

    block_lever = 171,

    block_stone_pressure_plate = 172,
    block_oak_pressure_plate = 174,
    block_spruce_pressure_plate = 175,
    block_birch_pressure_plate = 176,
    block_jungle_pressure_plate = 177,
    block_acacia_pressure_plate = 178,
    block_dark_oak_pressure_plate = 179,
    block_light_weighted_pressure_plate = 330,
    block_heavy_weighted_pressure_plate = 331,

    block_redstone_ore = 180,

    block_stone_button = 183,
    block_oak_button = 308,
    block_spruce_button = 309,
    block_birch_button = 310,
    block_jungle_button = 311,
    block_acacia_button = 312,
    block_dark_oak_button = 313,

    block_snow = 184,
    block_snow_block = 186,

    block_cactus = 187,
    block_clay = 188,

    block_sugar_cane = 189,
    block_jukebox = 190,

    block_oak_fence = 191,
    block_oak_fence_gate = 250,
    block_nether_brick_fence = 256,
    block_spruce_fence_gate = 475,
    block_birch_fence_gate = 476,
    block_jungle_fence_gate = 477,
    block_acacia_fence_gate = 478,
    block_dark_oak_fence_gate = 479,
    block_spruce_fence = 480,
    block_birch_fence = 481,
    block_jungle_fence = 482,
    block_dark_oak_fence = 483,
    block_acacia_fence = 484,

    block_pumpkin = 192,
    block_jack_o_lantern = 203,
    block_carved_pumpkin = 202,

    block_netherrack = 193,
    block_soul_sand = 194,
    block_glowstone = 200,

    block_nether_portal = 201,

    block_oak_trapdoor = 222,
    block_iron_trapdoor = 379,
    block_spruce_trapdoor = 223,
    block_birch_trapdoor = 224,
    block_jungle_trapdoor = 225,
    block_acacia_trapdoor = 226,
    block_dark_oak_trapdoor = 227,

    block_brown_mushroom_block = 238,
    block_red_mushroom_block = 239,
    block_mushroom_stem = 240,

    block_prismarine = 380,
    block_prismarine_bricks = 381,
    block_dark_prismarine = 382,

    block_cake = 204,

    block_repeater = 205,

    block_iron_bars = 241,
    block_glass_pane = 243,
    block_white_stained_glass_pane = 359,
    block_orange_stained_glass_pane = 360,
    block_magenta_stained_glass_pane = 361,
    block_light_blue_stained_glass_pane = 362,
    block_yellow_stained_glass_pane = 363,
    block_lime_stained_glass_pane = 364,
    block_pink_stained_glass_pane = 365,
    block_gray_stained_glass_pane = 366,
    block_light_gray_stained_glass_pane = 367,
    block_cyan_stained_glass_pane = 368,
    block_purple_stained_glass_pane = 369,
    block_blue_stained_glass_pane = 370,
    block_brown_stained_glass_pane = 371,
    block_green_stained_glass_pane = 372,
    block_red_stained_glass_pane = 373,
    block_black_stained_glass_pane = 374,

    block_melon_block = 103,

    block_melon = 244,
    block_attached_pumpkin_stem = 245,
    block_attached_melon_stem = 246,
    block_pumpkin_stem = 247,
    block_melon_stem = 248,


    block_terracotta = 407,
    block_white_terracotta = 343,
    block_orange_terracotta = 344,
    block_magenta_terracotta = 345,
    block_light_blue_terracotta = 346,
    block_yellow_terracotta = 347,
    block_lime_terracotta = 348,
    block_pink_terracotta = 349,
    block_gray_terracotta = 350,
    block_light_gray_terracotta = 351,
    block_cyan_terracotta = 352,
    block_purple_terracotta = 353,
    block_blue_terracotta = 354,
    block_brown_terracotta = 355,
    block_green_terracotta = 356,
    block_red_terracotta = 357,
    block_black_terracotta = 358,

    block_white_glazed_terracotta = 526,
    block_orange_glazed_terracotta = 527,
    block_magenta_glazed_terracotta = 528,
    block_light_blue_glazed_terracotta = 529,
    block_yellow_glazed_terracotta = 530,
    block_lime_glazed_terracotta = 531,
    block_pink_glazed_terracotta = 532,
    block_gray_glazed_terracotta = 533,
    block_light_gray_glazed_terracotta = 534,
    block_cyan_glazed_terracotta = 535,
    block_purple_glazed_terracotta = 536,
    block_blue_glazed_terracotta = 537,
    block_brown_glazed_terracotta = 538,
    block_green_glazed_terracotta = 539,
    block_red_glazed_terracotta = 540,
    block_black_glazed_terracotta = 541,

    block_vine = 249,

    block_mycelium = 253,

    block_lily_pad = 254,

    block_nether_brick = 255,

    block_nether_wart = 258,

    block_enchanting_table = 259,
    block_brewing_stand = 260,
    block_cauldron = 261,
    block_end_portal = 262,
    block_end_portal_frame = 263,
    block_end_stone = 264,
    block_dragon_egg = 265,
    block_redstone_lamp = 266,

    block_cocoa = 267,
    block_emerald_ore = 269,
    block_tripwire_hook = 272,
    block_tripwire_wire = 271,
    block_emerald_block = 273,

    block_beacon = 278,
    block_carrots = 306,
    block_potatoes = 307,

    block_redstone_block = 334,
    block_nether_quartz_ore = 335,
    block_quartz_block = 337,
    block_smooth_quartz = 473,
    block_quartz_pillar = 339,
    block_chiseled_quartz_block = 338,

    block_command_block = 277,
    block_repeating_command_block = 500,
    block_chain_command_block = 501,
    block_slime = 377,

    block_anvil = 326,
    block_chipped_anvil = 327,
    block_damaged_anvil = 328,

    // adding a gap in the numbering of walls to keep them all
    // in one numbering block starting at 1792
    // all blocks between 1792 and 2047 are considered walls
    // this is because our check looks for the prefix 0b11100000000
    block_andesite_wall = 661,
    block_brick_wall = 654,
    block_cobblestone_wall = 279,
    block_diorite_wall = 665,
    block_end_stone_brick_wall = 664,
    block_granite_wall = 658,
    block_mossy_cobblestone_wall = 280,
    block_mossy_stone_brick_wall = 657,
    block_nether_brick_wall = 660,
    block_prismarine_wall = 655,
    block_red_nether_brick_wall = 662,
    block_red_sandstone_wall = 656,
    block_sandstone_wall = 663,
    block_stone_brick_wall = 659,
    // end of walls
    block_chorus_plant = 491,

    block_comparator = 332,
    block_daylight_detector = 333,
    block_hopper = 336,

    block_white_carpet = 391,
    block_orange_carpet = 392,
    block_magenta_carpet = 393,
    block_light_blue_carpet = 394,
    block_yellow_carpet = 395,
    block_lime_carpet = 396,
    block_pink_carpet = 397,
    block_gray_carpet = 398,
    block_light_gray_carpet = 399,
    block_cyan_carpet = 400,
    block_purple_carpet = 401,
    block_blue_carpet = 402,
    block_brown_carpet = 403,
    block_green_carpet = 404,
    block_red_carpet = 405,
    block_black_carpet = 406,

    block_chorus_flower = 492,
    block_purpur_block = 493,
    block_purpur_pillar = 494,

    block_sea_lantern = 389,
    block_hay_block = 390,
    block_coal_block = 408,
    block_packed_ice = 409,

    block_red_sandstone = 448,
    block_chiseled_red_sandstone = 449,
    block_cut_red_sandstone = 450,

    block_end_bricks = 496,
    block_beetroots = 497,
    block_sweet_berry_bush = 682,
    block_frosted_ice = 502,
    block_magma_block = 503,
    block_nether_wart_block = 504,
    block_red_nether_brick = 505,
    block_bone_block = 506,

    block_shulker_box = 509,
    block_white_shulker_box = 510,
    block_orange_shulker_box = 511,
    block_magenta_shulker_box = 512,
    block_light_blue_shulker_box = 513,
    block_yellow_shulker_box = 514,
    block_lime_shulker_box = 515,
    block_pink_shulker_box = 516,
    block_gray_shulker_box = 517,
    block_light_gray_shulker_box = 518,
    block_cyan_shulker_box = 519,
    block_purple_shulker_box = 520,
    block_blue_shulker_box = 521,
    block_brown_shulker_box = 522,
    block_green_shulker_box = 523,
    block_red_shulker_box = 524,
    block_black_shulker_box = 525,

    block_observer = 508,

    block_ancient_debris = 735,
    block_basalt = 196,
    block_polished_basalt = 197,
    block_soul_campfire = 681,
    block_campfire = 680,
    block_blackstone = 743,
    block_netherite_block = 734,

    block_warped_nylium = 687,
    block_crimson_nylium = 696,
    block_soul_soil = 195,

    block_bell = 677,

    block_beehive = 731,
    block_bee_nest = 730,
    block_honeycomb_block = 733,
    block_honey_block = 732,

    block_dried_kelp_block = 576,
    block_scaffolding = 666,

    block_white_concrete = 542,
    block_orange_concrete = 543,
    block_magenta_concrete = 544,
    block_light_blue_concrete = 545,
    block_yellow_concrete = 546,
    block_lime_concrete = 547,
    block_pink_concrete = 548,
    block_gray_concrete = 549,
    block_light_gray_concrete = 550,
    block_cyan_concrete = 551,
    block_purple_concrete = 552,
    block_blue_concrete = 553,
    block_brown_concrete = 554,
    block_green_concrete = 555,
    block_red_concrete = 556,
    block_black_concrete = 557,

    block_white_concrete_powder = 558,
    block_orange_concrete_powder = 559,
    block_magenta_concrete_powder = 560,
    block_light_blue_concrete_powder = 561,
    block_yellow_concrete_powder = 562,
    block_lime_concrete_powder = 563,
    block_pink_concrete_powder = 564,
    block_gray_concrete_powder = 565,
    block_light_gray_concrete_powder = 566,
    block_cyan_concrete_powder = 567,
    block_purple_concrete_powder = 568,
    block_blue_concrete_powder = 569,
    block_brown_concrete_powder = 570,
    block_green_concrete_powder = 571,
    block_red_concrete_powder = 572,
    block_black_concrete_powder = 573,

    block_jigsaw = 727,
    block_structure_block = 726,
    block_warped_wart_block = 689,

    block_shroomlight = 698,
    block_twisting_vines = 701,
    block_twisting_vines_plant = 702,
    block_weeping_vines = 699,
    block_weeping_vines_plant = 700,

    block_grindstone = 673,
    block_loom = 667,
    block_stonecutter = 676,
    block_lectern = 674,

    block_composter = 728,

    block_bamboo = 622,

    block_bamboo_sapling = 621,

    block_warped_fungus = 688,
    block_crimson_fungus = 697,
    block_warped_roots = 690,
    block_crimson_roots = 703,

    block_lantern = 678,

    //TODO
    block_sapling = 116,
    
    
    // block_lit_furnace = 62,
    // block_lit_redstone_ore = 74,

    
    block_monster_egg = 97,
    block_stonebrick = 98,
    block_lit_redstone_lamp = 124,
    block_flower_pot = 140,
    block_skull = 144,
    block_stained_hardened_clay = 159,
    block_barrier = 166,
    block_hardened_clay = 172,
    block_double_plant = 175,
    block_standing_banner = 176,
    block_wall_banner = 177,
    block_end_rod = 198,
    block_structure_void = 217,
    block_end_gateway = 209,
    // 1.16 stuff
    // block_blast_furnace lit 11363
    // block_smoker lit = 11365,
    // 1.15 blocks below
};

typedef uint16_t mc_block_t;

enum mc_item_id {
    item_iron_shovel = 256,
    item_iron_pickaxe = 257,
    item_iron_axe = 258,
    item_flint_and_steel = 259,
    item_apple = 260,
    item_bow = 261,
    item_arrow = 262,
    item_coal = 263,
    item_diamond = 264,
    item_iron_ingot = 265,
    item_gold_ingot = 266,
    item_iron_sword = 267,
    item_wooden_sword = 268,
    item_wooden_shovel = 269,
    item_wooden_pickaxe = 270,
    item_wooden_axe = 271,
    item_stone_sword = 272,
    item_stone_shovel = 273,
    item_stone_pickaxe = 274,
    item_stone_axe = 275,
    item_diamond_sword = 276,
    item_diamond_shovel = 277,
    item_diamond_pickaxe = 278,
    item_diamond_axe = 279,
    item_stick = 280,
    item_bowl = 281,
    item_mushroom_stew = 282,
    item_golden_sword = 283,
    item_golden_shovel = 284,
    item_golden_pickaxe = 285,
    item_golden_axe = 286,
    item_string = 287,
    item_feather = 288,
    item_gunpowder = 289,
    item_wooden_hoe = 290,
    item_stone_hoe = 291,
    item_iron_hoe = 292,
    item_diamond_hoe = 293,
    item_golden_hoe = 294,
    item_wheat_seeds = 295,
    item_wheat = 296,
    item_bread = 297,
    item_leather_helmet = 298,
    item_leather_chestplate = 299,
    item_leather_leggings = 300,
    item_leather_boots = 301,
    item_chainmail_helmet = 302,
    item_chainmail_chestplate = 303,
    item_chainmail_leggings = 304,
    item_chainmail_boots = 305,
    item_iron_helmet = 306,
    item_iron_chestplate = 307,
    item_iron_leggings = 308,
    item_iron_boots = 309,
    item_diamond_helmet = 310,
    item_diamond_chestplate = 311,
    item_diamond_leggings = 312,
    item_diamond_boots = 313,
    item_golden_helmet = 314,
    item_golden_chestplate = 315,
    item_golden_leggings = 316,
    item_golden_boots = 317,
    item_flint = 318,
    item_porkchop = 319,
    item_cooked_porkchop = 320,
    item_painting = 321,
    item_golden_apple = 322,
    item_sign = 323,
    item_wooden_door = 324,
    item_bucket = 325,
    item_water_bucket = 326,
    item_lava_bucket = 327,
    item_minecart = 328,
    item_saddle = 329,
    item_iron_door = 330,
    item_redstone = 331,
    item_snowball = 332,
    item_boat = 333,
    item_leather = 334,
    item_milk_bucket = 335,
    item_brick = 336,
    item_clay_ball = 337,
    item_reeds = 338,
    item_paper = 339,
    item_book = 340,
    item_slime_ball = 341,
    item_chest_minecart = 342,
    item_furnace_minecart = 343,
    item_egg = 344,
    item_compass = 345,
    item_fishing_rod = 346,
    item_clock = 347,
    item_glowstone_dust = 348,
    item_fish = 349,
    item_cooked_fish = 350,
    item_dye = 351,
    item_bone = 352,
    item_sugar = 353,
    item_cake = 354,
    item_bed = 355,
    item_repeater = 356,
    item_cookie = 357,
    item_filled_map = 358,
    item_shears = 359,
    item_melon = 360,
    item_pumpkin_seeds = 361,
    item_melon_seeds = 362,
    item_beef = 363,
    item_cooked_beef = 364,
    item_chicken = 365,
    item_cooked_chicken = 366,
    item_rotten_flesh = 367,
    item_ender_pearl = 368,
    item_blaze_rod = 369,
    item_ghast_tear = 370,
    item_gold_nugget = 371,
    item_nether_wart = 372,
    item_potion = 373,
    item_glass_bottle = 374,
    item_spider_eye = 375,
    item_fermented_spider_eye = 376,
    item_blaze_powder = 377,
    item_magma_cream = 378,
    item_brewing_stand = 379,
    item_cauldron = 380,
    item_ender_eye = 381,
    item_speckled_melon = 382,
    item_spawn_egg = 383,
    item_experience_bottle = 384,
    item_fire_charge = 385,
    item_writable_book = 386,
    item_written_book = 387,
    item_emerald = 388,
    item_item_frame = 389,
    item_flower_pot = 390,
    item_carrot = 391,
    item_potato = 392,
    item_baked_potato = 393,
    item_poisonous_potato = 394,
    item_map = 395,
    item_golden_carrot = 396,
    item_skull = 397,
    item_carrot_on_a_stick = 398,
    item_nether_star = 399,
    item_pumpkin_pie = 400,
    item_fireworks = 401,
    item_firework_charge = 402,
    item_enchanted_book = 403,
    item_comparator = 404,
    item_netherbrick = 405,
    item_quartz = 406,
    item_tnt_minecart = 407,
    item_hopper_minecart = 408,
    item_prismarine_shard = 409,
    item_prismarine_crystals = 410,
    item_rabbit = 411,
    item_cooked_rabbit = 412,
    item_rabbit_stew = 413,
    item_rabbit_foot = 414,
    item_rabbit_hide = 415,
    item_armor_stand = 416,
    item_iron_horse_armor = 417,
    item_golden_horse_armor = 418,
    item_diamond_horse_armor = 419,
    item_lead = 420,
    item_name_tag = 421,
    item_command_block_minecart = 422,
    item_mutton = 423,
    item_cooked_mutton = 424,
    item_banner = 425,
    item_end_crystal = 426,
    item_spruce_door = 427,
    item_birch_door = 428,
    item_jungle_door = 429,
    item_acacia_door = 430,
    item_dark_oak_door = 431,
    item_chorus_fruit = 432,
    item_popped_chorus_fruit = 433,
    item_beetroot = 434,
    item_beetroot_seeds = 435,
    item_beetroot_soup = 436,
    item_dragon_breath = 437,
    item_splash_potion = 438,
    item_spectral_arrow = 439,
    item_tipped_arrow = 440,
    item_lingering_potion = 441,
    item_shield = 442,
    item_elytra = 443,
    item_spruce_boat = 444,
    item_birch_boat = 445,
    item_jungle_boat = 446,
    item_acacia_boat = 447,
    item_dark_oak_boat = 448,
    item_totem_of_undying = 449,
    item_shulker_shell = 450,
    item_iron_nugget = 452,
    item_knowledge_book = 453,
    item_record_13 = 2256,
    item_record_cat = 2257,
    item_record_blocks = 2258,
    item_record_chirp = 2259,
    item_record_far = 2260,
    item_record_mall = 2261,
    item_record_mellohi = 2262,
    item_record_stal = 2263,
    item_record_strad = 2264,
    item_record_ward = 2265,
    item_record_11 = 2266,
    item_record_wait = 2267
};

typedef uint16_t mc_item_t;
#endif

/* 
 * This file is part of the Minecraft Overviewer.
 *
 * Minecraft Overviewer is free software: you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or (at
 * your option) any later version.
 *
 * Minecraft Overviewer is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
 * Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with the Overviewer.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "block_class.h"
#include "utils.h"

#if defined(__i386__) || defined(__x86_64__)
#include <immintrin.h>
#endif

bool block_class_is_subset(
    mc_block_t block,
    const mc_block_t block_class[],
    size_t block_class_len) {
    size_t i = 0;

#ifdef __SSE2__
    for (; i / 8 < block_class_len / 8; i += 8) {
        const __m128i block_class_vec = _mm_loadu_si128(
            (__m128i*)&block_class[i]);
        const __m128i block_vec = _mm_set1_epi16(block);
        const __m128i block_cmp = _mm_cmpeq_epi16(block_vec, block_class_vec);
        if (_mm_movemask_epi8(block_cmp)) {
            return true;
        }
    }
#endif
#ifdef __MMX__
    for (; i / 4 < block_class_len / 4; i += 4) {
        const __m64 block_class_vec = _mm_cvtsi64_m64(
            *(uint64_t*)&block_class[i]);
        const __m64 block_vec = _mm_set1_pi16(block);
        const __m64 block_cmp = _mm_cmpeq_pi16(block_vec, block_class_vec);
        if (_mm_cvtm64_si64(block_cmp)) {
            return true;
        }
    }
#endif
    for (; i < block_class_len; ++i) {
        if (block == block_class[i]) {
            return true;
        }
    }
    return false;
}

bool block_class_is_wall(mc_block_t block) {
    mc_block_t mask = 0b11111111;
    mc_block_t prefix = 0b111 << 8;     // 1792 is the starting offset
    // if the xor zeroes all bits, the prefix must've matched.
    return ((block & ~mask) ^ prefix) == 0;
}

const mc_block_t block_class_stair[] = {
    block_minecraft__oak_stairs,
    block_minecraft__brick_stairs,
    block_minecraft__stone_brick_stairs,
    block_minecraft__nether_brick_stairs,
    block_minecraft__sandstone_stairs,
    block_minecraft__spruce_stairs,
    block_minecraft__birch_stairs,
    block_minecraft__jungle_stairs,
    block_minecraft__quartz_stairs,
    block_minecraft__acacia_stairs,
    block_minecraft__dark_oak_stairs,
    block_minecraft__red_sandstone_stairs,
    block_minecraft__smooth_red_sandstone_stairs,
    block_minecraft__purpur_stairs,
    block_minecraft__prismarine_stairs,
    block_minecraft__dark_prismarine_stairs,
    block_minecraft__prismarine_brick_stairs,
    block_minecraft__mossy_cobblestone_stairs,
    block_minecraft__cobblestone_stairs,
    block_minecraft__smooth_quartz_stairs,
    block_minecraft__polished_granite_stairs,
    block_minecraft__polished_diorite_stairs,
    block_minecraft__polished_andesite_stairs,
    block_minecraft__stone_stairs,
    block_minecraft__granite_stairs,
    block_minecraft__diorite_stairs,
    block_minecraft__andesite_stairs,
    block_minecraft__end_stone_brick_stairs,
    block_minecraft__red_nether_brick_stairs,
    block_minecraft__mossy_stone_brick_stairs,
    block_minecraft__smooth_sandstone_stairs,
    block_minecraft__crimson_stairs,
    block_minecraft__warped_stairs,
    block_minecraft__blackstone_stairs,
    block_minecraft__polished_blackstone_brick_stairs,
    block_minecraft__polished_blackstone_stairs
};

const size_t block_class_stair_len = COUNT_OF(block_class_stair);

const mc_block_t block_class_door[] = {
    block_minecraft__oak_door,
    block_minecraft__iron_door,
    block_minecraft__spruce_door,
    block_minecraft__birch_door,
    block_minecraft__jungle_door,
    block_minecraft__acacia_door,
    block_minecraft__dark_oak_door,
    block_minecraft__crimson_door,
    block_minecraft__warped_door};
const size_t block_class_door_len = COUNT_OF(block_class_door);

const mc_block_t block_class_fence[] = {
    block_minecraft__oak_fence,
    block_minecraft__spruce_fence,
    block_minecraft__birch_fence,
    block_minecraft__jungle_fence,
    block_minecraft__acacia_fence,
    block_minecraft__crimson_fence,
    block_minecraft__warped_fence,
    block_minecraft__dark_oak_fence,
    block_minecraft__nether_brick_fence};
const size_t block_class_fence_len = COUNT_OF(block_class_fence);

const mc_block_t block_class_fence_gate[] = {
    block_minecraft__oak_fence_gate,
    block_minecraft__spruce_fence_gate,
    block_minecraft__birch_fence_gate,
    block_minecraft__jungle_fence_gate,
    block_minecraft__acacia_fence_gate,
    block_minecraft__crimson_fence_gate,
    block_minecraft__warped_fence_gate,
    block_minecraft__dark_oak_fence_gate};
const size_t block_class_fence_gate_len = COUNT_OF(block_class_fence_gate);

const mc_block_t block_class_ancil[] = {
    block_minecraft__oak_door,
    block_minecraft__iron_door,
    block_minecraft__spruce_door,
    block_minecraft__birch_door,
    block_minecraft__jungle_door,
    block_minecraft__acacia_door,
    block_minecraft__dark_oak_door,
    block_minecraft__crimson_door,
    block_minecraft__warped_door,
    // block_minecraft__oak_stairs,
    // block_minecraft__brick_stairs,
    // block_minecraft__stone_brick_stairs,
    // block_minecraft__nether_brick_stairs,
    // block_minecraft__sandstone_stairs,
    // block_minecraft__spruce_stairs,
    // block_minecraft__birch_stairs,
    // block_minecraft__jungle_stairs,
    // block_minecraft__quartz_stairs,
    // block_minecraft__acacia_stairs,
    // block_minecraft__dark_oak_stairs,
    // block_minecraft__red_sandstone_stairs,
    // block_minecraft__smooth_red_sandstone_stairs,
    // block_minecraft__purpur_stairs,
    // block_minecraft__prismarine_stairs,
    // block_minecraft__dark_prismarine_stairs,
    // block_minecraft__prismarine_brick_stairs,
    // block_minecraft__cobblestone_stairs,
    // block_minecraft__mossy_cobblestone_stairs,
    // block_minecraft__mossy_stone_brick_stairs,
    // block_minecraft__smooth_quartz_stairs,
    // block_minecraft__polished_granite_stairs,
    // block_minecraft__polished_diorite_stairs,
    // block_minecraft__polished_andesite_stairs,
    // block_minecraft__stone_stairs,
    // block_minecraft__granite_stairs,
    // block_minecraft__diorite_stairs,
    // block_minecraft__andesite_stairs,
    // block_minecraft__end_stone_brick_stairs,
    // block_minecraft__red_nether_brick_stairs,
    // block_minecraft__smooth_sandstone_stairs,
    // block_minecraft__crimson_stairs,
    // block_minecraft__warped_stairs,
    // block_minecraft__blackstone_stairs,
    // block_minecraft__polished_blackstone_brick_stairs,
    // block_minecraft__polished_blackstone_stairs,
    block_minecraft__grass_block,
    block_minecraft__flowing_water,
    block_minecraft__water,
    block_minecraft__glass,
    block_minecraft__ice,
    block_minecraft__oak_fence,
    block_minecraft__nether_portal,
    block_minecraft__iron_bars,
    block_minecraft__glass_pane,
    block_minecraft__lily_pad,
    block_minecraft__nether_brick_fence,
    block_minecraft__andesite_wall,
    block_minecraft__brick_wall,
    block_minecraft__cobblestone_wall,
    block_minecraft__diorite_wall,
    block_minecraft__end_stone_brick_wall,
    block_minecraft__granite_wall,
    block_minecraft__mossy_cobblestone_wall,
    block_minecraft__mossy_stone_brick_wall,
    block_minecraft__nether_brick_wall,
    block_minecraft__prismarine_wall,
    block_minecraft__red_nether_brick_wall,
    block_minecraft__red_sandstone_wall,
    block_minecraft__sandstone_wall,
    block_minecraft__stone_brick_wall,
    block_minecraft__blackstone_wall,
    block_minecraft__polished_blackstone_brick_wall,
    block_minecraft__polished_blackstone_wall,
    block_minecraft__double_plant,
    block_minecraft__white_stained_glass_pane,
    block_minecraft__orange_stained_glass_pane,
    block_minecraft__magenta_stained_glass_pane,
    block_minecraft__light_blue_stained_glass_pane,
    block_minecraft__yellow_stained_glass_pane,
    block_minecraft__lime_stained_glass_pane,
    block_minecraft__pink_stained_glass_pane,
    block_minecraft__gray_stained_glass_pane,
    block_minecraft__light_gray_stained_glass_pane,
    block_minecraft__cyan_stained_glass_pane,
    block_minecraft__purple_stained_glass_pane,
    block_minecraft__blue_stained_glass_pane,
    block_minecraft__brown_stained_glass_pane,
    block_minecraft__green_stained_glass_pane,
    block_minecraft__red_stained_glass_pane,
    block_minecraft__black_stained_glass_pane,

    block_minecraft__white_stained_glass,
    block_minecraft__orange_stained_glass,
    block_minecraft__magenta_stained_glass,
    block_minecraft__light_blue_stained_glass,
    block_minecraft__yellow_stained_glass,
    block_minecraft__lime_stained_glass,
    block_minecraft__pink_stained_glass,
    block_minecraft__gray_stained_glass,
    block_minecraft__light_gray_stained_glass,
    block_minecraft__cyan_stained_glass,
    block_minecraft__purple_stained_glass,
    block_minecraft__blue_stained_glass,
    block_minecraft__brown_stained_glass,
    block_minecraft__green_stained_glass,
    block_minecraft__red_stained_glass,
    block_minecraft__black_stained_glass,
    block_minecraft__crimson_fence,
    block_minecraft__warped_fence,

    block_minecraft__spruce_fence,
    block_minecraft__birch_fence,
    block_minecraft__jungle_fence,
    block_minecraft__dark_oak_fence,
    block_minecraft__acacia_fence};
const size_t block_class_ancil_len = COUNT_OF(block_class_ancil);

const mc_block_t block_class_alt_height[] = {
    // block_minecraft__oak_stairs,
    // block_minecraft__brick_stairs,
    // block_minecraft__stone_brick_stairs,
    // block_minecraft__nether_brick_stairs,
    // block_minecraft__sandstone_stairs,
    // block_minecraft__spruce_stairs,
    // block_minecraft__birch_stairs,
    // block_minecraft__jungle_stairs,
    // block_minecraft__quartz_stairs,
    // block_minecraft__acacia_stairs,
    // block_minecraft__dark_oak_stairs,
    // block_minecraft__red_sandstone_stairs,
    // block_minecraft__smooth_red_sandstone_stairs,
    // block_minecraft__prismarine_stairs,
    // block_minecraft__dark_prismarine_stairs,
    // block_minecraft__prismarine_brick_stairs,
    // block_minecraft__cobblestone_stairs,
    // block_minecraft__mossy_cobblestone_stairs,
    // block_minecraft__mossy_stone_brick_stairs,
    // block_minecraft__smooth_quartz_stairs,
    // block_minecraft__polished_granite_stairs,
    // block_minecraft__polished_diorite_stairs,
    // block_minecraft__polished_andesite_stairs,
    // block_minecraft__stone_stairs,
    // block_minecraft__granite_stairs,
    // block_minecraft__diorite_stairs,
    // block_minecraft__andesite_stairs,
    // block_minecraft__end_stone_brick_stairs,
    // block_minecraft__red_nether_brick_stairs,
    // block_minecraft__smooth_sandstone_stairs,
    block_minecraft__oak_slab,
    block_minecraft__spruce_slab,
    block_minecraft__birch_slab,
    block_minecraft__jungle_slab,
    block_minecraft__acacia_slab,
    block_minecraft__dark_oak_slab,
    block_minecraft__petrified_oak_slab,
    block_minecraft__stone_slab,
    block_minecraft__sandstone_slab,
    block_minecraft__cobblestone_slab,
    block_minecraft__brick_slab,
    block_minecraft__stone_brick_slab,
    block_minecraft__nether_brick_slab,
    block_minecraft__quartz_slab,
    block_minecraft__red_sandstone_slab,
    block_minecraft__purpur_slab,
    block_minecraft__prismarine_slab,
    block_minecraft__dark_prismarine_slab,
    block_minecraft__prismarine_brick_slab,
    block_minecraft__andesite_slab,
    block_minecraft__diorite_slab,
    block_minecraft__granite_slab,
    block_minecraft__polished_andesite_slab,
    block_minecraft__polished_diorite_slab,
    block_minecraft__polished_granite_slab,
    block_minecraft__red_nether_brick_slab,
    block_minecraft__smooth_sandstone_slab,
    block_minecraft__cut_sandstone_slab,
    block_minecraft__smooth_red_sandstone_slab,
    block_minecraft__cut_red_sandstone_slab,
    block_minecraft__end_stone_brick_slab,
    block_minecraft__mossy_cobblestone_slab,
    block_minecraft__mossy_stone_brick_slab,
    block_minecraft__smooth_quartz_slab,
    block_minecraft__smooth_stone_slab,
    block_minecraft__crimson_slab,
    block_minecraft__warped_slab,
    block_minecraft__polished_blackstone_brick_slab,
    block_minecraft__blackstone_slab,
    block_minecraft__polished_blackstone_slab
    };
const size_t block_class_alt_height_len = COUNT_OF(block_class_alt_height);

const mc_block_t block_class_slab[] = {
    block_minecraft__oak_slab,
    block_minecraft__spruce_slab,
    block_minecraft__birch_slab,
    block_minecraft__jungle_slab,
    block_minecraft__acacia_slab,
    block_minecraft__dark_oak_slab,
    block_minecraft__petrified_oak_slab,
    block_minecraft__stone_slab,
    block_minecraft__sandstone_slab,
    block_minecraft__cobblestone_slab,
    block_minecraft__brick_slab,
    block_minecraft__stone_brick_slab,
    block_minecraft__nether_brick_slab,
    block_minecraft__quartz_slab,
    block_minecraft__red_sandstone_slab,
    block_minecraft__purpur_slab,
    block_minecraft__prismarine_slab,
    block_minecraft__dark_prismarine_slab,
    block_minecraft__prismarine_brick_slab,
    block_minecraft__andesite_slab,
    block_minecraft__diorite_slab,
    block_minecraft__granite_slab,
    block_minecraft__polished_andesite_slab,
    block_minecraft__polished_diorite_slab,
    block_minecraft__polished_granite_slab,
    block_minecraft__red_nether_brick_slab,
    block_minecraft__smooth_sandstone_slab,
    block_minecraft__cut_sandstone_slab,
    block_minecraft__smooth_red_sandstone_slab,
    block_minecraft__cut_red_sandstone_slab,
    block_minecraft__end_stone_brick_slab,
    block_minecraft__mossy_cobblestone_slab,
    block_minecraft__mossy_stone_brick_slab,
    block_minecraft__smooth_quartz_slab,
    block_minecraft__smooth_stone_slab,
    block_minecraft__crimson_slab,
    block_minecraft__warped_slab,
    block_minecraft__polished_blackstone_brick_slab,
    block_minecraft__blackstone_slab,
    block_minecraft__polished_blackstone_slab
    };
const size_t block_class_slab_len = COUNT_OF(block_class_slab);

const mc_block_t block_class_nether_roof[] = {
    block_minecraft__bedrock,
    block_minecraft__netherrack,
    block_minecraft__nether_quartz_ore,
    block_minecraft__lava,
    block_minecraft__soul_sand,
    block_minecraft__basalt,
    block_minecraft__blackstone,
    block_minecraft__soul_soil,
    block_minecraft__nether_gold_ore};
const size_t block_class_nether_roof_len = COUNT_OF(block_class_nether_roof);

const mc_block_t block_class_no_inner_surfaces[] = {
    block_minecraft__white_stained_glass,
    block_minecraft__orange_stained_glass,
    block_minecraft__magenta_stained_glass,
    block_minecraft__light_blue_stained_glass,
    block_minecraft__yellow_stained_glass,
    block_minecraft__lime_stained_glass,
    block_minecraft__pink_stained_glass,
    block_minecraft__gray_stained_glass,
    block_minecraft__light_gray_stained_glass,
    block_minecraft__cyan_stained_glass,
    block_minecraft__purple_stained_glass,
    block_minecraft__blue_stained_glass,
    block_minecraft__brown_stained_glass,
    block_minecraft__green_stained_glass,
    block_minecraft__red_stained_glass,
    block_minecraft__black_stained_glass,
    block_minecraft__glass,
    block_minecraft__ice};
const size_t block_class_no_inner_surfaces_len = COUNT_OF(block_class_no_inner_surfaces);

const mc_block_t block_class_pane_and_bars[] = {
    block_minecraft__iron_bars,
    block_minecraft__glass_pane,
    block_minecraft__white_stained_glass_pane,
    block_minecraft__orange_stained_glass_pane,
    block_minecraft__magenta_stained_glass_pane,
    block_minecraft__light_blue_stained_glass_pane,
    block_minecraft__yellow_stained_glass_pane,
    block_minecraft__lime_stained_glass_pane,
    block_minecraft__pink_stained_glass_pane,
    block_minecraft__gray_stained_glass_pane,
    block_minecraft__light_gray_stained_glass_pane,
    block_minecraft__cyan_stained_glass_pane,
    block_minecraft__purple_stained_glass_pane,
    block_minecraft__blue_stained_glass_pane,
    block_minecraft__brown_stained_glass_pane,
    block_minecraft__green_stained_glass_pane,
    block_minecraft__red_stained_glass_pane,
    block_minecraft__black_stained_glass_pane};
const size_t block_class_pane_and_bars_len = COUNT_OF(block_class_pane_and_bars);

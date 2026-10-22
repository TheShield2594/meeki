# Mood sprites

The idle poses in `assets/sprites/mood_*` (one per mood level: `sad`,
`sulky`, `content`, `happy`, `overjoyed`) are AI-generated with
[Higgsfield](https://higgsfield.ai), unlike the rest of the art, which
`tools/generate_assets.py` draws in code. This page records how they were
made so they can be redone or extended.

## 1. A still per mood

Model: GPT Image 2.5 (`gpt_image_2_5`), quality *high*, 1:1, transparent
background. References: the generated `idle/00.png` (plus `sulky/00.png` for
sad and sulky, `celebrating/00.png` for happy and overjoyed), each flattened
on white and upscaled to 1024 px.

Shared prompt, with the emotion line swapped per mood:

> The exact same chibi puppy from the reference images (fluffy white Bichon x
> Biewer pup, dark slate ear patches and saddle, gold cheeks, pink bow on
> top-knot), same flat vector cartoon style, same framing: sitting, front
> view, centred, filling about 80% of a square canvas. Emotion: … Transparent
> background, soft grey ground ellipse, no text.

| Mood | Emotion line |
|---|---|
| sad | SAD. Ears drooping low, big glossy eyes with a single small tear, eyebrows tilted up in the middle, tiny downturned mouth, head slightly lowered, tail tucked. |
| sulky | SULKY / grumpy pout. Half-lidded unimpressed eyes glancing to the side, cheeks puffed, little pouting mouth, one ear flicked back, head turned slightly away, paws crossed. Cute, not angry. |
| content | CONTENT / calm. Relaxed soft eyes, gentle closed-mouth smile, ears relaxed, head tilted very slightly, peaceful. |
| happy | HAPPY. Bright sparkling eyes, open-mouth smile with a little pink tongue, ears perked up, rosy cheeks, fluffy tail wagging up behind it. |
| overjoyed | OVERJOYED. Mid-hop with both front paws raised, eyes squeezed into happy upside-down U shapes, wide open grin with tongue out, ears flying up, tail a blur of wagging, two tiny pink hearts floating above its head. (Drop "same framing: sitting, front view".) |

The transparent output has a soft half-transparent halo. Before the next
step, keep only pixels with alpha ≥ 200 (shrink that mask by 1 px and blur
it by 1 px), then put the still on flat green `#00b140` at its original
1024 px framing. Every mood keeps the same scale.

## 2. A looping clip per still

Model: MiniMax H3 Max (`minimax_h3_max`), 5 s, 768p, 1:1. The green still is
both the **start and the end frame**, so the clip loops. Shared prompt, with
the motion line swapped per mood:

> Flat 2D cartoon animation, locked-off static camera, no zoom, no pan. …
> Crisp clean outlines on every frame, no motion blur, no smears. Keep the
> exact flat vector art style and colours of the start image. Solid flat
> green background stays perfectly uniform, no shadows, no lighting changes,
> no extra objects, no text.

| Mood | Motion line |
|---|---|
| sad | The sad chibi puppy stays seated in exactly the same spot and size the whole time. Slow, gentle motion: a slow sad sigh that makes its shoulders rise and fall, droopy ears sag a little lower, one slow blink, the small tear glistens, tail stays still. Calm, slow, looping game sprite idle animation. |
| sulky | The sulky chibi puppy stays seated with paws crossed in exactly the same spot and size the whole time. Small, slow motion: it huffs once, puffing its cheeks, glances further away then side-eyes back, one ear flicks, a slow unimpressed blink. Cute grumpy pout, looping game sprite idle animation. |
| content | The calm, content chibi puppy stays seated in exactly the same spot and size the whole time. Gentle, small motion: soft breathing, a slight head tilt and back, one relaxed blink, a slow lazy tail sway. Peaceful looping game sprite idle animation. |
| happy | The happy chibi puppy stays seated in exactly the same spot and size the whole time. Cheerful motion at a relaxed pace: tail wags slowly side to side, head bobs gently, ears give a little bounce, one happy blink, open-mouth smile with tongue out. Looping game sprite idle animation. (Also "including the tail, … no speed lines".) |
| overjoyed | The overjoyed chibi puppy bounces up and down on the spot in small happy hops, landing in exactly the same place, staying the same size, front paws waving, ears flapping, big grin with tongue out, the two little pink hearts above its head bob gently. Energetic but clean looping game sprite animation. (Also "no speed lines".) |

## 3. Clips to frames

```sh
python tools/key_sprites.py --ref mood_content \
    mood_sad=sad.mp4 mood_sulky=sulky.mp4 mood_content=content.mp4 \
    mood_happy=happy.mp4 mood_overjoyed=overjoyed.mp4
```

The tool samples 8 fps (41 frames per 5 s clip) and keys out the green. It
crops every clip with one shared box, scaled so the content pup is as tall
as the generated idle pup and stands on the same ground line, then writes
256-colour PNGs. Pass all five clips in one run, or the moods come out at
different sizes.

## Known limits

- The other states (focusing, break, celebrating, petted, the one-shots)
  are still drawn by `generate_assets.py`. Moving between them and a mood
  pose changes the art style a little.
- AI frames are not perfectly consistent. Ear markings shift slightly and
  fast motion can soften outlines. Check new clips at the real ~112 px size.
- The first and last frames are close but not identical, so a loop can
  hitch slightly.

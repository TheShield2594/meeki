# AI-generated sprites

All of the bundled pup in `assets/sprites/` is AI-generated with
[Higgsfield](https://higgsfield.ai). That covers the mood poses (`mood_sad`,
`mood_sulky`, `mood_content`, `mood_happy`, `mood_overjoyed`) and every other
state. The older pup that `tools/generate_assets.py` draws in code served
as the style reference. This page records how the art was made, so it can be
redone or extended.

## 1. A still per pose

### Mood stills

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

### The other poses

Same model and settings. References: the content mood still (as a job
reference) for the character, plus the old drawn sprite of that state for the
pose. Shared opening: *"Draw the puppy from the FIRST reference image (its
exact character design, proportions, colours, outline and flat vector style)
in the pose of the SECOND reference image: …"*.

| State | Pose line |
|---|---|
| focusing | FOCUSING. It sits facing the viewer wearing small round black-rimmed glasses, front paws resting on the keyboard of a small open silver laptop in front of its chest (laptop lid faces the viewer and has a small red apple-shaped tomato logo). Calm concentrated expression, eyes looking down at the screen, gentle closed-mouth smile. |
| break (nap) | NAPPING. It lies curled up on its tummy, seen from the front-side, chin resting on its front paws, eyes peacefully closed in calm curved lines, relaxed smile, fluffy tail curled round beside it, pink bow on top. (Also "no Z letters".) |
| celebrating | CELEBRATING. It springs up in a joyful jump with both front paws thrown up high, eyes squeezed shut in happy upside-down U shapes, big open grin with tongue out, ears flying up, with a few small colourful confetti pieces around it. |
| petted | BEING PETTED. It sits facing the viewer, head tilted to one side leaning into an invisible stroke, eyes closed in blissful happy upside-down U shapes, open-mouth smile with a little tongue, rosy blushing cheeks, ears relaxed back, tail wagging. No hands, no hearts. |

Green confetti or other green bits would be keyed out with the green screen,
so recolour them (orange) before putting a still on green.

## 2. A clip per still

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

The other states use the same settings. Looping states start and end on
their own still. The one-shot idle animations start and end on the **content
mood still**, so they flow out of and back into the idle pose.

| State | Motion line |
|---|---|
| focusing | The chibi puppy wearing glasses stays seated behind its little laptop in exactly the same spot and size the whole time. Calm focused motion: its front paws tap gently on the keyboard, small thoughtful head nod, one slow blink behind the glasses, ears settle. The laptop stays still. |
| break | The chibi puppy sleeps curled up in exactly the same spot and size the whole time, eyes closed. Very slow, peaceful motion: slow deep breathing so its body gently rises and falls, one small ear twitch, the tail tip flicks once. (Also "no Z letters".) |
| celebrating | The chibi puppy celebrates on the spot: bouncy little jumps landing in the same place, front paws pumping in the air, ears flapping, big grin with tongue out, eyes squeezed happily, confetti pieces drift and twirl around it. Same size the whole time. (Also "no green confetti".) |
| petted | The chibi puppy stays seated in exactly the same spot and size, blissfully enjoying being petted by an invisible hand: it leans and nuzzles its head side to side, eyes closed happily, wiggles with delight, tail wags, cheeks blush. No hands visible. |
| look | It curiously looks around: turns its head to look left, then to the right with ears perked and a little head tilt, then back to the front. |
| yawn | It does one big sleepy yawn: eyes close, head tilts back, mouth opens wide showing a little pink tongue, ears droop, then it smacks its lips, blinks and settles back to the calm smile. |
| scratch | It tilts its head and scratches behind its ear with a hind paw a few quick times, eyes squeezed happily, then puts the paw down and settles back to the calm pose. |
| stretch | It does a big lazy stretch: slides its front paws forward into a play-bow with its bottom up and tail high, eyes closed contentedly, then sits back up and settles into the calm pose. |

Higgsfield sometimes suggests a preset instead of running a clip. If it
does, resubmit the same request with that preset declined.

## 3. Clips to frames

All clips go through one run, so they share a scale (`idle` and `sulky` reuse
the content and sulky mood clips):

```sh
python tools/key_sprites.py --ref mood_content \
    mood_content=content.mp4 mood_sad=sad.mp4 mood_sulky=sulky.mp4 \
    mood_happy=happy.mp4 mood_overjoyed=overjoyed.mp4 \
    idle=content.mp4 sulky=sulky.mp4 \
    focusing=focusing.mp4 break=break.mp4 celebrating=celebrating.mp4 \
    petted=petted.mp4 look=look.mp4 yawn=yawn.mp4 scratch=scratch.mp4 \
    stretch=stretch.mp4
```

The tool samples 8 fps (41 frames per 5 s clip) and keys out the green. It
crops every clip with one shared box, scaled so the content pup is as tall
as the old idle pup and stands on the same ground line. A pose too wide for
that box, like the nap, is re-centred on itself at the same scale. The tool
writes 256-colour PNGs plus an `fps` file per state, so the app plays them
at the speed they were made. Then run `python tools/generate_assets.py`. It
keeps these folders and redraws the app icon from the content pup.

## Known limits

- The nap no longer has the old floating "Zzz".
- AI frames are not perfectly consistent. Ear markings shift slightly and
  fast motion can soften outlines. Check new clips at the real ~112 px size.
- The first and last frames are close but not identical, so a loop can
  hitch slightly.

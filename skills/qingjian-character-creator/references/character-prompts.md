# Reusable Qingjian character prompts

[简体中文](character-prompts.zh_CN.md) · English

These templates reconstruct a reusable method from Qingjian artwork iterations, not verbatim historical prompts. Replace brackets, remove inapplicable clauses, and attach actual references with the image tool. Naming a reference in text does not attach it.

## Character brief

```text
Character: [name or original description]
Reference roles: [A = accepted face; B = outfit; C = anatomy/pose]
Keep across stages: [face, eyes, species/anatomy, recognizable silhouette]
Style/proportions: [chosen art style and body proportions]
Pose: [balanced stance or explicit character pose; limb orientation]
Stage 0: [initial outfit, hair color, accessories, expression]
Stage 1: [allowed changes; other identity traits remain unchanged]
Stage 2: [final changes, pose, optional restrained effects]
Avoid: [rejected face, cropped feet, unwanted pose, incorrect accessories]
Output: three separate sharp PNGs with genuine RGBA transparency.
```

Fill settled details from context. For non-humanoids, replace face/legs language with their actual identifying structures and appendages.

## Initial form

```text
Create a standalone full-body image of [CHARACTER], in [STYLE/PROPORTIONS].
Use attached [A] for the accepted face/identity, [B] for [CLOTHING], and [C] for
[POSE/ANATOMY]. Preserve [KEEP LIST]. Appearance: [STAGE 0]. Pose: [POSE].
Show the entire silhouette and [FEET/TAIL/WINGS/OTHER EXTREMITIES], with a small
transparent margin. For this humanoid design, use complete proportionate legs
and the specified natural stance; no forced inward-pointing knees or feet.
Use clear facial landmarks, coherent garment shapes, and clean edges readable
on a small black badge. Output one sharp source PNG, preferably about 1024 × 1024,
with genuine RGBA alpha transparency. No white rectangle, painted checkerboard,
scene, added caption, or watermark. Do not crop the head or feet or redesign
the accepted face. Preserve deliberate markings on the character itself.
```

## Growth form

```text
Edit the attached accepted stage-0 image of [CHARACTER] into the growth stage.
Keep the same recognized identity: [FACE/ANATOMY/STYLE KEEP LIST].
Change only [STAGE 1 OUTFIT, HAIR COLOR, ACCESSORIES, POSE OR EFFECTS].
Use additional reference [IMAGE] only for [ASSIGNED ROLE]. Do not redesign the
face to suggest an upgrade. Keep comparable character scale, complete body,
proportionate limbs, specified stance, and safe framing margins.
Deliver one separate sharp PNG, using the same source canvas when practical,
with real RGBA transparency and clean edges on black. No background rectangle,
baked checkerboard, added text, or watermark.
```

## Final form

```text
Edit the attached accepted [STAGE 0/1] image into the final form.
Keep character identity and the accepted face exactly as [KEEP LIST].
Allowed final changes: [STAGE 2 OUTFIT, HAIR COLOR, ACCESSORIES, EXPRESSION, POSE].
When blending poses, use [A] for [TORSO/ARMS] and [B] for [LEGS/BALANCE], adapting
them into coherent anatomy and a stable center of gravity.
Optional effect: [SPECIFIED SMALL EFFECT], clear of the face and silhouette.
Retain the whole body and all extremities with a small transparent margin.
Keep accepted proportions and stance; do not invent an unwanted inward-leg pose.
Match earlier stages' rendering and scale. Deliver one sharp RGBA PNG with
genuine transparency, clean edges suitable for black, no scene or added captions.
```

## Targeted repair instead of redesign

```text
Edit this accepted image only to correct [OBSERVED DEFECT]. Retain the face from
[ACCEPTED VERSION] and [LOCKED TRAITS]. Change only [REGION/FEATURE], preserving
identity, proportions, full-body framing, stage outfit, and genuine transparency.
Do not invent a new face or pose. Improve specified contours/details from the
reference; do not substitute a generic face or claim enlargement restores detail.
```

## Delivery manifest

Fill actual values. This describes the asset package; **it is not an app configuration import format**. Omit thresholds to preserve current app settings, or add user-chosen positive integers with stage one below stage two within the app's accepted range.

```json
{
  "schema_version": 1,
  "character": "Your character",
  "identity_notes": "Accepted face and identifying traits preserved.",
  "artwork_origin": "AI-generated using user-provided references",
  "prompt_file": "character-prompts.md",
  "stages": [
    {"stage": 0, "file": "stage-0.png", "appearance": "Initial outfit"},
    {"stage": 1, "file": "stage-1.png", "appearance": "Growth outfit"},
    {"stage": 2, "file": "stage-2.png", "appearance": "Final form"}
  ],
  "qa": {"alpha": "passed", "visual_review": "passed", "device_preview": "not_checked"},
  "image_transfer": "USB",
  "rights_note": "Actual source and redistribution limits."
}
```

Use the actual source/editing provenance, including `user-provided` when no new art was generated. Set QA from checks performed; alpha alone cannot certify identity, anatomy, or device appearance. Do not include private reference paths or images without intended sharing rights.

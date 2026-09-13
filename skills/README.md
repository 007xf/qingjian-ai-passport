<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Skills

This directory is reserved for reusable AI-agent skills and workflows. Keep each skill in its own clearly named subdirectory.

Each skill must contain at least `SKILL.md` with YAML frontmatter defining `name` and a trigger-focused `description`. Complex skills may add `references/`, `scripts/`, and `assets/`. Keep documentation as plain Markdown and register every added skill in this index.

## Current skills

| Skill | What it does |
| --- | --- |
| [qingjian-character-creator](qingjian-character-creator/SKILL.md) | Character Workshop: use user-provided references and editable prompts to prepare three transparent character PNGs for Qingjian's USB image upload workflow. |
| [issue-suggestions](issue-suggestions/SKILL.md) | After a release, collect the releasing developer's own improvement points and file them as feature request issues against the upstream project. |
| [experience-pr](experience-pr/SKILL.md) | After a release, collect reusable development experience and submit it as a documentation pull request. |
| [plays-archive](plays-archive/SKILL.md) | After a release, archive the published application into the upstream `plays/` with an AI-generated bilingual summary and a cover image. |

## Install Character Workshop

Download [qingjian-character-creator.zip](https://github.com/007xf/qingjian-ai-passport/releases/download/v1.4.0/qingjian-character-creator.zip) and extract its `qingjian-character-creator` folder into `~/.codex/skills/`. The entry file must be `~/.codex/skills/qingjian-character-creator/SKILL.md`. Preserve any customized copy before replacing it, then start a new Codex session.

Invoke `$qingjian-character-creator`, attach your own reference images, and describe the three stage designs and the traits to preserve. The [prompt templates](qingjian-character-creator/references/character-prompts.md) can also be copied and edited separately. New image creation or editing requires an available image tool; without one, use existing PNGs you provide. The skill does not run AI on the badge or guarantee a perfect result without review.

Choose each reviewed transparent PNG in Qingjian's corresponding stage, set the evolution thresholds as desired, and upload through USB. See the [project guide](../README.md#character-workshop-make-your-own-three-forms) for the complete flow and the one-custom-image device cache limit.

# SRD 5.2.1 catalogue

`srd_5_2_1.json` is generated from the System Reference Document 5.2.1 PDF by
`scripts/build_srd_catalogue.py`, then loaded read-only by `catalogue.py`.

| Section | Entries | Fields kept |
| --- | --- | --- |
| Monsters A–Z + Animals | 328 | size, type, tags, alignment, AC, HP, hit dice, speed, ability scores, senses, languages, CR, XP, trait and action names |
| Magic Items A–Z | 259 | category, rarity, attunement |
| Species | 9 | creature type, size, speed, trait names |
| Classes | 12 | primary ability, hit die |
| Backgrounds | 4 | ability scores, feat, skills |
| Prices | 33 | lifestyle, lodging, hirelings and gear lines that parse cleanly |

World documents reference entries by id (`srd_ref`, e.g. `srd:ghoul`) rather than copying them.
The biome bestiary, NPC role templates and culture mappings in `catalogue.py` are the engine's own choices.

To rebuild after a new SRD release:

```bash
python scripts/build_srd_catalogue.py path/to/SRD_CC_v5.2.1.pdf
```

## Licence and attribution

This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1") by Wizards of the Coast LLC,
available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0
International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

The rest of this repository is MIT licensed; the catalogue data stays under CC-BY-4.0.

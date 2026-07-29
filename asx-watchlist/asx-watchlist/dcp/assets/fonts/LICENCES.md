# Bundled font licences

These fonts ship with the skill. All are freely redistributable. Two roles:

**Rendering.** Open Sans is written into PDF reports, so it must travel with the skill
or reports render in a substitute face.

**Measurement.** Carlito, Caladea and TeX Gyre Pagella are metric-compatible with
Calibri, Cambria and Palatino Linotype respectively. `qa_layout.py` measures text with
them, so its overflow findings match what PowerPoint renders. They are never written
into a deliverable. They are bundled because relying on the host system to provide them
is not safe: without Carlito the checker falls back to DejaVu Sans, which is far wider,
and reports clean documents as overflowing by 33 to 100 per cent.

| Font | Licence | Source |
|---|---|---|
| Open Sans | Apache License 2.0 | https://github.com/googlefonts/opensans |
| Carlito | SIL Open Font License 1.1 | Google, via the Chrome OS core fonts |
| Caladea | SIL Open Font License 1.1 | Google, via the Chrome OS core fonts |
| TeX Gyre Pagella | GUST Font License (LPPL-style) | GUST e-foundry |

Full licence texts accompany each upstream distribution at the sources above. None of
these licences restricts redistribution within an organisation, and none requires a fee.

**Museo Sans 500 is NOT bundled.** It is a commercially licensed face used by the
credentials pack. The skill names it so that machines which have it render correctly;
machines without it will substitute, and `qa_layout.py` labels every finding on a Museo
Sans shape as approximate. Anyone producing final client artwork should have it
installed under DCP's own licence.

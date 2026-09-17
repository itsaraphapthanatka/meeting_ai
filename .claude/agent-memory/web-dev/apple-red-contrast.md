---
name: apple-red-contrast
description: Apple-style "soft red" (#FF453A) fails WCAG AA on white text — check contrast before wiring a design-spec hex straight into a filled button
metadata:
  type: feedback
---

When a design spec hands you a literal hex for a semantic color (e.g. "recording/mute = Soft Red
`#FF453A`", Apple's dark-mode systemRed), do not assume it is safe for `color: #fff` on a solid
filled button just because it "is the brand color". Computed relative luminance of `#FF453A` gives
only ~3.5:1 contrast against white text — below WCAG AA's 4.5:1 for normal-size button labels.
This is not specific to that one hex: Apple's light-mode systemRed (`#FF3B30`) computes to almost
the same ratio, because any near-max-red/low-green/low-blue color caps out around there regardless
of the exact shade.

**Why it matters:** meeting_ai's original `.btn-rec`/`.btn-danger` used `#dc2626` (~4.83:1, passes),
so blindly swapping in the spec's `#FF453A` would have been a real accessibility regression.

**How to apply:** keep the spec's exact hex as the semantic token (dots, borders, small text on a
surface background — those already clear AA in both directions), but for filled buttons with white
text, derive a darker variant via `color-mix(in srgb, var(--rec) 80%, black)` (~5:1) and use that
for the button background specifically. Do this calculation (or a quick luminance check) any time a
design spec gives you a saturated red/orange meant to sit behind white text — don't skip it just
because "it's what the spec says."

See also [[job-id-vs-meeting-id]] — from the same 2026-09-16 mobile-first redesign task
(`meeting_ai/web/static/`).

# Plan 009 (spike) — findings: authoring aid for device profiles from a diagnostics dump

Prototype: `scripts/propose_device_profile.py` (offline, stdlib only, never imported by
`custom_components/`, never writes to `configs/*.py`, no network access).
Corpus: `plans/notes/fixtures-009/*.json` (3 dumps, each with a `_test_notes` block
documenting provenance and expected CLI invocation/outcome).

## 1. Corpus results

| Fixture | Anchors | Outcome | Matches plan's "Vérifier" expectation? |
|---|---|---|---|
| `a_tecnolc2_standard.json` (cc25051112, Issue #80 numbers) | ph=7.5, orp=663, temperature=21.3, salinity=3.7 | Proposes `_standard_tecnolc2(...)`, all 4 anchors land on 165/170/172/174 at 0% error | Yes — standard layout proposed |
| `b_tecnolc2_temperature_vs_ph_trap.json` (cc25016001 + cc25052635 numbers) | temperature=29.0, ph=7.3, orp=640 | `temperature → 172` (0% error); `ph → 165` (0% error), **not** 172 (÷100 of 290 = 2.90, 60% error, correctly rejected); ORP trap (c177) flagged | Yes — exactly the two assertions the plan's Step 2 verification asks for |
| `c_non_standard_legacy_control.json` (cc24018506, Issue #129 numbers) | orp=597, temperature=31.5 (2 anchors) | **Wrongly** proposes `_standard_tecnolc2(...)` — both anchors land on standard slots, but the real device (per chlorinators.py) uses c4/c164 for chlorination, not c10 | Not requested by the plan, but included as a stress test |
| same, + chlorination_level=60 (3 anchors) | Correctly falls back to the explicit `DeviceConfig(...)` block — `chlorination_level → 4`, which contradicts the standard slot 10, so the tool refuses the terse call | — | Demonstrates the tie-break/contradiction guard working |

Run transcripts are reproducible: see the exact commands in each fixture's `_test_notes`.
1338 baseline tests unaffected — nothing under `custom_components/` was touched.

**Success rate on this corpus: 2/2 required cases pass as specified (a, b). The bonus
case (c) shows the heuristic's designed failure mode exactly once, and shows it is
avoidable given the right anchor — which is itself the spike's main finding (§2).**

### Unresolved / not attempted

- No non-tecnoLC2 device family was tested (no heat pump, pump, or EXO chlorinator dump
  in the corpus — the plan scoped this spike to the tecnoLC2 chlorinator lineup, the
  single most repeated request per the "why this matters" section).
- Ties between *sensor* and *setpoint* readings at different scales are real (fixture a:
  c20=750 and c165=750 both score 7.50 at ÷100 — both numbers are cited verbatim from the
  cc25051112 comment, not a fixture artifact). A tie-break toward the standard slot fixes
  the common case but is naive: if a device's *actual* mapping is non-standard on both
  legs of a tie, the tie-break still guesses the standard slot. The evidence block always
  lists the tied alternate at its true error (0.0%), so a human reviewing the printed
  evidence — not just the generated code block — will catch it. This is exactly why the
  tool's contract is "propose for review," not "propose for merge."
- Component-role ties among *non-standard* candidates (fixture c: chlorination candidates
  4 and 164 both score 0%) resolve by dict insertion order — arbitrary, but harmless here
  since the explicit-block path already forces a human review comment.

## 2. Central question: how much of the work stays human?

Concretely, per the tecnoLC2 pipeline this tool targets:

1. **Scale/slot arithmetic (÷100, ÷10, raw mV) — tool does this.** This is genuinely the
   repetitive, error-prone part: `grep -c "as pH" chlorinators.py` finds **13 separate
   comment mentions across roughly 10 profiles** of the exact same bug — the generic
   profile (or an earlier misconfigured one) reading c172 (water temperature) as pH
   ("→ 2.9", "2.13", "2.46", "2.88", "2.54", "2.63", "3.07", "3.16", "4.27", ...). The tool
   removes this specific, repeatedly-hit mistake by construction (the temperature anchor
   claims c172 first and pH is scored against the *actual* pH candidate).
2. **Deciding terse-call vs. explicit-block — tool does this, with a caveat.** The
   heuristic (§ "≥2 anchors match the standard slots ⇒ terse call") is exactly what the
   plan specified, and fixture c shows it is *only as good as the anchors supplied*. With
   2 anchors it is wrong; with 3 (including a control-plane anchor) it self-corrects. **A
   human still has to know to ask for one non-sensor anchor** (chlorination %, or a mode
   value) when the reporter's issue only mentions pH/ORP/temperature — which, reading the
   existing issues (#73, #85, #104, #116, #117, #121, #125, #129, #138), is the overwhelmingly
   common case. This is a process recommendation, not just a tool limitation (§4).
3. **`identifier_patterns` (the real device serial) — tool CANNOT do this, structurally.**
   `diagnostics.py:148-181` (`_redact_devices_data`) always replaces `device_id` with
   `**REDACTED**`, and separately `name`/`family`/`model` are the same generic
   `"Chlorinator"`/`"Chlorinators"` string across the entire tecnoLC2 lineup (confirmed by
   the cc25052635_chlorinator comment: "the Fluidra API exposes no model field... comp7 is
   empty"). **There is no way to recover the serial pattern from a diagnostics dump alone,
   by construction** — this is the STOP condition the plan called out in advance, and it
   is real, not a hypothetical. The tool surfaces this explicitly (prints a NOTE and
   requires `--serial-pattern` sourced from the issue text/app, never from the dump) and
   makes **no attempt to weaken the redaction** — that boundary is out of scope for a
   local authoring tool and would be a separate, much bigger discussion (diagnostics.py's
   redaction is deliberate and load-bearing for user privacy).
4. **Provenance comments (issue #, reporter handle, "confirmed by X against the live app")
   — 100% human.** Every existing profile in `chlorinators.py` carries this kind of
   comment; the tool has no way to know who reported what, and shouldn't guess.
5. **Confirming the "app says X" anchor values themselves — 100% human**, and this is the
   one step chlorinators.py explicitly documents as sometimes requiring a live session
   with the reporter (comment at `chlorinators.py:108-114`, cc25052635_chlorinator: "Mapping
   confirmed by several users" — several back-and-forths over multiple issues before the
   final mapping stuck, e.g. CC26028741 in #116 still needed a second diagnostics pass
   after initially falling back to the generic profile).
6. **Ambiguity resolution when the evidence block prints multiple 0%-error candidates for
   one anchor (the pH/ORP-setpoint tie in fixture a) — human**, informed by the tool's
   printed evidence rather than starting from raw component dumps.

**Estimate: the tool collapses steps that used to take several issue round-trips (spot
the c172-as-pH bug, compute the right divisor, decide standard-vs-custom layout) into one
command, but it does not remove the need for a human to (a) supply the real serial,
(b) confirm the app-displayed anchors are accurate, (c) ask for one control-plane anchor
when only sensor values were reported, and (d) write the provenance comment. Rough split:
roughly the arithmetic/layout-decision half of the work moves to the tool; the
identity/provenance/confirmation half stays human — closer to a "one round-trip saved"
tool than a "close the issue unattended" tool.**

## 3. Recommendation: GO

Ship it as a maintainer-facing triage aid, not a user-facing one. Rationale:

- It is genuinely faster than manual arithmetic for the single most common contribution
  type (~32 commits mentioning "profile"), and it removes the specific, repeatedly-hit
  c172-read-as-pH class of bug (13 independent comment mentions of the same mistake,
  across ~10 profiles, in `chlorinators.py`'s own comments).
- It is risk-free to adopt: no runtime code path touches it, output is a review-first
  candidate block, and it structurally cannot leak anything the diagnostics redaction
  already protects (it never receives or needs the un-redacted serial to *analyze* the
  dump — only to *label* the final block, and only if the maintainer chooses to pass it).
- The failure mode found in fixture c is real but self-limiting: it only misfires when
  handed exactly 2 anchors that happen to both be "clean" standard-layout sensors while
  the control plane is non-standard — and the fix is a process one (ask for one more
  value), not a tool rewrite.

**Where to document it (if GO is acted on):**

- README.md § "🆕 Adding New Equipment" (`README.md:101-116`): add a 4th bullet —
  "maintainers: run `python scripts/propose_device_profile.py` on the attached diagnostics
  dump + the 2-3 values above to get a candidate profile" — kept as a maintainer-facing
  aside, not asking end users to run Python locally.
- A GitHub issue template (`.github/ISSUE_TEMPLATE/`) for new-equipment reports,
  structured to request exactly what the tool needs: (1) the diagnostics dump file
  (unmodified — do not hand-edit it), (2) 2-3 app-displayed values *including at least one
  non-sensor value* (chlorination %, mode, or setpoint) per §2 point 2, (3) equipment
  model/serial prefix as displayed in the app or on the unit (since the dump alone cannot
  supply it, per §2 point 3). This directly targets the recurring failure pattern.

## 4. Open questions

- **Should the issue template be a structured GitHub form (`.yml`) rather than free text?**
  Given how consistently the same 2 fields (dump + app values) are what maintainers had to
  chase in the ~9 profile issues reviewed for this spike, a structured form with those
  fields marked required (plus a "control-plane value, e.g. chlorination %" field
  specifically) would likely cut a full round-trip per issue. Not built in this spike
  (issue templates are out of this spike's file scope) — worth a follow-up.
- **Should the tool accept raw (non-diagnostics) pastes, e.g. a screenshot-derived table or
  a hand-typed component list?** The prototype's `load_devices()` already accepts a bare
  device dict or a `{"devices": [...]}` list for flexibility, so a maintainer could
  hand-build a minimal JSON from a user's pasted logs without the full diagnostics
  envelope. Full support for parsing raw debug-log text (not JSON) was out of scope for
  the time-box and would need real log samples to design against — flagged, not built.
- **Should the standard/explicit decision also weigh `boost_mode`/`free_chlorine`
  anchors, not just the 4 sensors + 3 control slots?** Not needed by the corpus tested
  here; `_standard_tecnolc2()` already accepts these as optional kwargs so the terse-call
  path degrades gracefully either way — low priority.

## 5. What was intentionally NOT built (per plan's out-of-scope)

- No write access to `configs/*.py` — output is stdout only, as required.
- No network calls to the Fluidra cloud.
- No attempt to change `diagnostics.py`'s redaction to make `identifier_patterns` easier
  to derive — that boundary is treated as fixed, per the plan's STOP condition.

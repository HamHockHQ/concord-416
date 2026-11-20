# Design Critique: 416 Concord shared dashboard (interactive rebuild)

- **Date**: 2026-11-20
- **Method**: Impeccable critique process (`~/workspace/design/impeccable-trial/skill/reference/critique.md` + `critique-rubric.md`), applied to the rebuilt `index.html` during the build. Assessment snapshot taken from the built UI structure and its rendered states (signed-out, Matt, Carole, preview slices).
- **Scope note**: single-deal tracker for the people on this purchase. No portfolio or scale features (Matt's scope guard, 2026-11-20).

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Preview state is banner-labeled; sign-in request gives inline status. No loading skeleton on first data fetch. |
| 2 | Match System / Real World | 3 | Plain-language labels (Deadlines, Open items, Flags). "Flags" is new vocabulary but explained in-panel. |
| 3 | User Control and Freedom | 3 | Tabs are freely switchable; preview has an obvious exit. No undo concept needed for a read-only tracker. |
| 4 | Consistency and Standards | 3 | V29 tokens, chips, and card pattern reused from the verified build; tab pattern follows platform conventions. |
| 5 | Error Prevention | 4 | Money cannot leak by construction: non-Matt payloads never contain it (server-side slicing + publish guard), and the client scrubs defensively. Preview reuses the same slicing rule. |
| 6 | Recognition Rather Than Recall | 3 | Deadline detail pulls the matching step's status inline; nobody has to cross-reference tabs from memory. |
| 7 | Flexibility and Efficiency | 2 | Timeline, list, and calendar views of the same deadlines; no keyboard shortcuts beyond the tab pattern, no saved view preference. |
| 8 | Aesthetic and Minimalist Design | 3 | One section in view at a time; the long scroll is gone. Lender panel is dense by necessity. |
| 9 | Error Recovery | 2 | A failed data load shows a plain message; there is no retry button. |
| 10 | Help and Documentation | 2 | Panel intros explain Flags and the lender figures; no broader help surface. |
| **Total** | | **28/40** | **Good. Fix the priority issues below in the next iteration.** |

## Persona Check (Casey, distracted mobile user, primary)

- One-handed phone use: on phone widths the tab bar is fixed to the bottom of the screen with 52px targets (thumb zone), short labels (Docs, Items) so nothing wraps or clips.
- Interruptions: tab state and the selected deadline survive view switches; nothing is lost mid-task.
- Cognitive load: the shell shows one section at a time (working-memory rule from the critique process). The old page stacked every section in one scroll; the rebuild's panel switching is the direct fix.
- Emotional journey: the timeline runs past to future with a Today marker, so "where are we" reads in one glance; settlement is the final point and a KPI (peak-end). The Flags empty state is a positive confirmation ("Everything cross-checks") instead of a blank.

## Priority Issues (and what the build did about each)

1. **[P1] Navigation unreachable in the thumb zone on phones.** The first shell draft kept the tab bar at the top on all widths. Fix applied: at 760px and below the tab bar becomes a fixed bottom bar, 52px targets, safe-area padding.
2. **[P1] Preview state could be mistaken for the real account.** Fix applied: a persistent coral banner names who is being previewed, states nothing was sent, and carries an Exit preview button; the Lenders tab disappears in preview.
3. **[P2] Deadline detail forced cross-referencing.** Fix applied: expanding a deadline (timeline point, list row, or calendar date) shows its note plus the matching step's status and due note from the same slice.
4. **[P2] Counts were invisible until a section was opened.** Fix applied: count pills on the Open items and Flags tabs.
5. **[P3] 200% zoom resilience.** The horizontal timeline scrolls inside its own region rather than widening the page; calendar months stack. (The family cabin dashboard's 200% clipping finding is the cautionary example; this layout avoids full-width fixed rows.)

## Minor Observations

- The lender comparison table scrolls horizontally inside its card on narrow phones; acceptable for a Matt-only analysis surface, not for the shared sections.
- `.sr-only` utility added for the table's measure column header.
- Reduced-motion users: chevron and dot transitions are disabled under `prefers-reduced-motion`.

## Provocative Questions

- Should the Flags tab badge turn coral only for conflict-severity flags, so a quiet info flag does not read as an alarm?
- Is the calendar earning its place next to the timeline, or is it a second visual of the same six dates? (Kept per Matt's explicit request; revisit after real use.)

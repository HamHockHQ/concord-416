# Walkthrough feedback — Matt, 2026-11-20 evening (voice review of the live dashboard)

Matt walked the live external dashboard (https://hamhockhq.github.io/concord-416/) persona by persona and gave the notes below. These are build directions, in his words in substance.

## Fixes for the current build

1. **Timeline text overlaps.** The horizontal timeline idea is right, but labels collide. Fix label layout (stagger, abbreviate, or expand-on-tap) so no text overlaps at phone or desktop width.
2. **Carole's view is too thin.** Her steps only show "final walkthrough and settlement." She is the one keeping Matt on track, so she should see when everything else happens: deposit, lender decision, inspection, application, appraisal, commitment, settlement. Give Carole the full deadline/step timeline. Longer term (his words): a filter Matt controls for what each person sees. For now, err on showing Carole the full schedule.
3. **Lender-name leak in Sam's view.** The mortgage application step in Sam's view says the signed documents name Movement Mortgage. Fine today because Sam already knows, but it will not always be true that the other lender knows. Rule: a lender's view must never name the competing lender or the contract lender by name; use neutral wording ("the lender named in the signed agreement") in any non-Matt, non-Movement view. Apply the same scrub both directions.
4. **Seller timeline needs the inspection.** The sellers are not being sent access, but if they were, the inspection results belong on their timeline because repair negotiation will be based on them. Add an inspection-results step to the seller slice.
5. **Stale open item.** "Seller execution confirmed" still sits under open items. It is done, not open. Remove it from open items in the source data and republish.
6. **Documents access is too broad (noted, not urgent).** Matt loves that documents are one tap away, but everyone having access to all documents is not right long term. Needs a secure document host with per-person access (Cloudflare or GitHub, since the dashboard already lives there). Fine for now; record as an open item.

## Lender breakdown (Matt-only) — add a negotiation summary

Matt loves the breakdown and the earlier-quotes history. Add a negotiation tab/summary in his view only, stating the trade-offs plainly. Current verified figures (Nov 20 reruns, 3% down):

- Univest Worksheet 1: 7.375%, $2,699.94/mo, $16,237.65 cash to close, lifetime interest $444,806.60. Lowest monthly.
- Univest Worksheet 2: 7.500%, $2,725.49/mo ($25.55/mo more than W1), $14,758.94 cash to close ($1,478.71 less than W1), lifetime interest $454,004.60 ($9,198 more than W1 over 30 years).
- Movement: 7.625%, $2,738.71/mo, $17,217.70 cash to close, lifetime interest $463,249.40. No credits on the sheet. Replacement assistance program still unnamed/unpriced.
- The summary should say, in substance: W1 wins monthly; W2 trades about $26/mo for about $1,500 less cash now at about $10k more over the life of the loan; and the leverage note: go back to Movement and ask whether they can beat these numbers one more time before Monday's decision. (Draft wording only; nothing is sent to any lender from the dashboard.)

## Direction for the next version (accentuate this most)

- **AI analysis layer is the point, not decoration.** A dashboard that only displays things is fine; what Matt actually wants is analysis on top: anomalies flagged, connections between disparate pieces surfaced, and help visualizing lots of numbers, deadlines, and people moving at once. Closing a house is exactly that kind of mess. The automatic flagger (already in scope) is the first piece; the next version should lead with analysis: what changed, what disagrees, what is stale, what it means, and what to do next, per person where relevant and Matt-only where money is involved.
- Steps should feel tangible and interactive from the first screen: draggable elements, more graphs. (Build judgment on specifics; keep the accessibility bar at zero axe violations.)

## Explicitly unchanged

- Money stays Matt-only; the dollar scrub on shared payloads stays.
- No sends to anyone from this feedback. Carole already has her link; lender links still wait for Monday's pick unless Matt says otherwise.
- Agent portfolio / all-deals view stays version 2.

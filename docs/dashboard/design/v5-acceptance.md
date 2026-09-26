# V5 rendering refinement acceptance

Scope: interactive design demo only. V4 remains preserved. No production project code or Hermes configuration was changed.

## Goal-to-evidence audit

| Requirement | Final evidence |
|---|---|
| Replace appearance dropdown with sun/moon controls | Browser shows labeled sun and moon buttons. Both light and dark changes passed. Initial mode follows system appearance until manually selected. |
| Configuration cog | Gear button opens Configuration; verified visible configuration panel. |
| Simplify first-page alignment | Top and bottom panels share column proportions. Model usage has a stable title and stable control arrangement. Repeated toggle measurement gave identical chart top and 166 px chart height. |
| Remove repeated rows beneath savings graph | Exactly one compact default/routed cost row is visible. Details opens the hidden calculation and baseline section; both opening and closing verified. |
| Explain estimated savings and break-even | Clickable info controls use full sentences for the default comparison, cumulative zero line, incomplete coverage and unmeasured quality. |
| Explain reliability metrics | Confidence gate and execution/evidence help opened and read in browser. Existing detailed gate explanation and drilldown are retained. |
| Make date picker dismiss correctly | Outside heading click, period selection, view navigation, Escape, Cancel and successful Apply all closed the date panel. Invalid reversed range remained open with an error. |
| Preserve useful economics | Embedded JSON fixture exactly matches v4. Monthly values remain USD 16.55 net scenario difference, USD 74.30 default, USD 57.75 routed including assumed Jev. Pricing source remains inside Details and the global source disclosure. |
| Preserve Jev cost and Decisions | Both render, Jev info explicitly distinguishes the arbitrary demo assumption from published pricing; configuration and decisions retain their previous content. |
| Verify rendering and controls | Browser console warnings/errors empty. Light desktop screenshot inspected at 1024 px viewport. At 352 px viewport, product width and scroll width both 318 px; no controls or chart text outside bounds. Help panel fits within the product width. Viewport reset after tests. |

Final preview: [temporary loopback preview, no longer a delivery URL].

Final state: Savings, Month, All projects, Details closed, dates closed, Dark appearance. Browser tab marked deliverable.

No new activity, prices, charges, model reasoning, or quality results were fabricated by this refinement. The activity is still explicitly synthetic, with same-usage price comparisons and unavailable real billing/quality evidence.

Final HTML SHA-256: `0591954845e5820e953181d90401e4b7e1c34590f759f6e7598c1a254e1254b6`

Bytes: 877055

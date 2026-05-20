# iSite2 Conclusion Rules

Default conclusion outputs:
- Evidence Status: Verified, Supported, Indicative, Insufficient.
- Value Class: scene-specific value tier from configured enums.
- Action Class: Direct Recommend, Survey First, Review Queue, Monitor.
- Recommended Solution: pRRU, hRRU, or Unknown.

Do not output a unified global score unless explicitly requested and implemented as a display-only derived view.

Recommendation policy:
- Confirm value first through scene evidence.
- Confirm or explicitly leave unknown the build-status chain.
- Select solution by scene form only after value/action class is supported: indoor high-value defaults to pRRU; semi-open high-value defaults to hRRU.
- If evidence is weak or conflicted, downgrade action or move to Review Queue.

Reason-to-recommend must cite property-specific evidence and avoid generic formulas, policy text, or unsupported claims.

# Cvent Next.js image proxy URL incident

## Impact

The UAE hotel expansion parsed some Cvent card images from the HTML `src` attribute as
relative `/_next/image?url=...` values. The image itself was real and property-specific,
but the retained URL was not independently usable outside the Cvent page. The image gate
therefore attempted unnecessary replacement searches before active publication.

## Root cause

The reusable Cvent parser supported direct absolute image URLs but did not unwrap the
encoded upstream URL in a Next.js image proxy query parameter.

## Corrective action

- `run_cvent_apac_candidate_expansion.py` now unwraps `/_next/image?url=...` values.
- Existing UAE overlay records were mechanically normalized, with a timestamped backup
  and `outputs/uae_mall_expansion_20260819/cvent_image_proxy_fix.json` audit artifact.
- The HTML parser regression test uses a relative Next.js proxy fixture and asserts that
  the durable absolute image URL is retained.

## Preventive control

`QA-IMAGE-004` blocks reusable directory imports from retaining relative or provider-bound
image proxy URLs when an absolute upstream source URL is encoded in the proxy request.

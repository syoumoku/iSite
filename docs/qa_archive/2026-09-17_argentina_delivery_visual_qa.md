# Argentina report delivery visual QA (2026-09-17)

## Controls

- QA-OUTPUT-002: inspect rendered PPT and Excel previews, not only numerical audit results.
- QA-OUTPUT-001: preserve the frozen payload and reconcile bilingual recommendation totals.

## Findings and delivery fixes

The eight-scene English PPT airport metric label wrapped into the threshold line. The delivered PPT uses the shorter display label `Annual passengers`, retaining the canonical value 11,203,687 and the original evidence chain.

The standard Excel generator omitted explicit column widths, text wrapping and row sizing. Both delivery workbooks were formatted with readable columns, wrapped text, fitted row heights, frozen headers and dark-blue header rows. Recommendation-only pale-yellow fills were retained; alternating row fills were not introduced.

Every nonblank workbook cell was compared with the pre-formatting baseline. All values were preserved; the renderer/exporter serialized some empty cells as empty strings rather than absent cells. Both still represent missing data, not zero.

## Retained artifacts

Delivery root: `outputs/argentina_standard_report_delivery_20260917/`.

- `render_en/slide-1.png` and `render_zh/slide-1.png`: final PPT previews.
- `excel_preview_en.png` and `excel_preview_zh.png`: formatted recommendation previews.
- `excel_data_baseline.json`: pre-formatting cell values.
- `qa_final_formatted.json`: final rules and GPT OAuth audit, passed.

The delivery contains 88 export-ready candidates, eight scenes and 27 threshold recommendations. No active database, source evidence or public report activation was changed by these presentation-only corrections. Reusable generator layout remediation remains a separate follow-up; future deliveries must still perform this manual gate.

## Later 127-candidate snapshot delivery

The user subsequently requested the completed third-round snapshot: 127 local latest candidates, of which 95 pass the standard export gate and 28 pass scene recommendation thresholds. The earlier 88-row report predates that completion. The new delivery explicitly distinguishes source-pool counts from qualified report counts and does not enable `include_blocked_quality` to force 127 output rows (QA-PUBLISH-002, QA-OUTPUT-001).

The hospital-image preflight rejected genuine reviewed WebP assets because the existing PPT normalizer does not accept WebP. The reviewed `publish_image_review/3.image` and `4.image` artifacts were losslessly converted into PNG-format PPT cache entries, preserving pixels and original source URLs. Source files, DB image URLs and the evidence chain were not changed. This resolved the delivery compatibility gate without substituting photographs (QA-IMAGE-003, QA-OUTPUT-002).

Final delivery artifacts and bilingual previews are in `outputs/argentina_standard_report_delivery_pool127_20260917/`; `excel_data_baseline.json` records the original cells, and `qa_final_formatted.json` records the post-formatting OAuth audit. Future version-specific sends must verify the requested source-pool size in the frozen generation summary before sending.

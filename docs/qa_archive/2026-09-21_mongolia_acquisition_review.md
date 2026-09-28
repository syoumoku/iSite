# Mongolia Acquisition Review

## QA-COMPLIANCE-001

During Nomin Makro image verification a cached robots audit read and image request were submitted in the same orchestration call. The audit returned403, but the image download had already started. It was interrupted as soon as that result was inspected; the partial image was not accepted into overlay, active or public data. This was an orchestration ordering error, not permission to bypass access controls.

The existing manual gate applies: robots results must be evaluated before submitting a body request. Unknown robots status holds acquisition. Evidence of remediation and the three independent image attempts is retained in `.web_evidence/mongolia_deep_scan_20260921/finalize/compliance_review.json` and `malls/missing_image_three_attempts_parent.json`. Nomin Makro remains explicitly without an image under the user-approved exception. No new scraper or country-specific code was introduced.

## QA-IMAGE-004

Grand Hill's operator booking site supplied a Next.js image URL. Before publication it was decoded to the original Horecasoft PNG, separately verified against robots404 and HTTP200 image content, and synchronized as an image-only change. Evidence metrics and unchanged properties were not recalculated for this correction. Audit: `hotels/grandhill_original_image_update.json` and `grandhill_image_fix_audit.json` in the same run directory.

## QA-EVIDENCE-001 / QA-PIPELINE-001

Final review caught copied source-scope text in two Cvent corroborations: room counts were correct, but a parenthetical2025 tourism-guide description belonged to the other source. The Cvent values now explicitly identify undated property inventory and retrieval date. Room counts did not change.

The legacy pipeline assigns stadiums a scene-level Semi-open form. Three Mongolia enclosed arenas were corrected to Indoor with separate physical-form evidence and scoped derived/Traffic/localization refresh. This is a property-data correction, not a general pipeline fix: future reimport must verify physical form rather than assume the generic scene default. The current run did not change this global behavior. A temporary non-enum cross-check label introduced during correction was rejected by Pydantic verification and replaced with Partial Cross-check before publication. All affected packets were then reloaded through the domain models. Audit: `finalize/qa_corrections.json` and `finalize/corrected_packets_checkpoint.json`.

## QA-LOCAL-001 / QA-LOCAL-002

Three formula-bearing inference translations for Monnis Tower and Pro One remained unusable after batch and single-item retries. Their Chinese cache text was manually reviewed, preserving every numeric value and metric key. Only localization cache entries changed. A subsequent scoped dry-run found 313 cache hits, 307 same-locale skips and zero errors. Audit: `finalize/manual_localization_review.json` and `finalize/localization_final_gate.json`.

## QA-PUBLISH-003 / QA-REPORT-001 / QA-ENTITY-002

The property-scoped publication dry-run stopped before remote writes. All seven historical Mongolia property IDs differ between the local database and the public database. Furthermore, the historical local airport uses unmapped Khushig Valley and is not export-ready under the current city gate. Thus the expected remote post-delta state has29 properties while the local export-ready report state has28, with seven remote IDs missing and six local historical IDs extra. The22 new Ulaanbaatar properties passed scoped localization and preaggregation, but no new data or report was activated remotely. Audit: `finalize/publish_dry_run.log`.

Do not fix this by disabling report fingerprint validation, assigning the airport to Ulaanbaatar without evidence, or replacing the entire country. Reconcile historical identities against a retained remote snapshot and verify the airport administrative locality first; then regenerate the full-country report against the exact post-delta state and retry the same22-property delta. Existing remote data remains unchanged.

### Follow-up Resolution

The official parliamentary gazette annex to resolution2024/76 publishes110 ordered boundary coordinates on PDF pages139-141 for Shine Zuunmod, renamed Hunnu by resolution2025/26. The complete closed polygon was extracted from the public document and checked using Shapely: valid polygon; terminal point106.8215825,47.6512559 strictly inside. The original airport locality remains Khushig Valley, with Sergelen and Tov preserved as administrative context. Only the two known local/public airport IDs receive this reviewed property override, constrained to the verified terminal vicinity; neither all valley names nor airports serving Ulaanbaatar are automatically reassigned. Artifacts: `finalize/hunnu_boundary.json`, official resolution at `https://legalinfo.mn/mn/detail?lawId=17140841120651`, and gazette at `https://www.parliament.mn/files/e1850d794d7745eaa3ccc55328f52811/?d=1`. PDF screenshots timed out; the full ordered coordinate text, ring closure, and point-in-polygon result were retained instead.

All seven historical local/public pairs were checked by exact canonical name, scene, country and coordinates. A separate publication source combines the remote historical rows with the22 new local properties. This preserves six untouched remote historical properties and the original airport evidence while correcting its city. The publication scope is23 IDs, not a country replacement. The original local historical rows and an explicit identity crosswalk are retained; this is not a global identity migration. Future deltas must use the reconciled publication state, not silently revert to local historical IDs. Artifacts: `finalize/remote_mongolia_rows.json`, `historical_identity_crosswalk.json`, `publication_source.db`, and `publication_city_audit.json`.

City normalization tests:41 passed. Reconciled publication dry-run passed city, identity, localization, preaggregation, country report fingerprint and bilingual report audits with29 export-ready properties. Both18-sheet workbooks were rendered; sampled main, recommendation, evidence, inference and office pages passed layout review, with no formula error matches. Some Chinese template cells intentionally retain original evidence/inference wording; the UI localization cache is separately verified. See `finalize/report_visual_review.json`. Actual activation remains subject to the publication manifest and post-deployment verification, not this dry-run result.

Activation completed successfully as `country_delta_20260921T092121Z`. Live public API returned29 candidates and29 display points; airport city is Hunnu City. The six unselected historical public packets compare exactly equal to the pre-release snapshot. Hunnu Shopping Mall name search returns the correct new property. Both live Excel downloads contain29 Mongolia rows and18 sheets, and their SHA-256 hashes equal the audited release files. Country count remains124. Tests additionally passed:16 public delta/report tests and4 QA-control tests. Browser UI inspection timed out before an interactive check; no browser visual pass is claimed. Final evidence: `finalize/post_publish_verification.json`, `search_smoke.json`, and the release manifest. No unchanged-property derived refresh or cloud Firecrawl request was made in this follow-up.

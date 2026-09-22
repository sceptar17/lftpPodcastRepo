# Prototype architecture review

## Verdict

The architecture is ready for a three-episode **live accuracy trial**, not an archive run. The
mechanical contract is strong: JSON is canonical, provider data is retained, evidence is validated,
WordPress is a draft-only projection, and reruns reuse normalized transcription checkpoints.

## What worked

- Three representative fixture episodes completed end to end.
- Every generated claim/topic/summary carries timestamps and transcript segment IDs.
- Unknown segment IDs and out-of-range timestamp pointers fail validation.
- Related links include human-readable reasons rather than an unexplained score.
- A WordPress post ID changes create behavior into update behavior.
- Fixture limitations propagate into `needs-review` instead of being hidden.

## Current failure modes

1. Feed discovery is confirmed at `https://livefromthepath.org/feed/podcast/` (466 items at the
   time of this review), but feed descriptions are excerpts and cannot replace transcription.
2. Full episodes will commonly exceed a transcription provider's single-file limit; production
   needs ffmpeg-based compression/chunking with timestamp offsets and boundary overlap.
3. Speaker diarization and precise segment timestamps may require different models or a two-pass
   transcription strategy.
4. Proper names, book titles, and Scripture references need a show-specific vocabulary and a
   reviewer correction loop.
5. Model schema compliance does not establish factual support; deterministic evidence validation
   catches missing pointers, while a second verification pass must assess whether excerpts truly
   support each conclusion.
6. Similarity is lexical in the prototype. Embeddings should be tested only after topic hygiene is
   reliable, and relationship thresholds need reviewer feedback.
7. Updating an existing WordPress draft is idempotent only after its post ID has been saved. A crash
   between remote creation and local save is the remaining duplicate risk; add an episode-ID custom
   field and search-before-create before production.

## Cost model

The bundled fixture run costs $0. No paid-provider call was made, so this prototype does not claim an
observed production cost. A reasonable planning range for a 90-minute episode is **$1.50–$3.00**:

- transcription: roughly $0.27–$0.54 at current comparable API rates of $0.003–$0.006/minute;
- structured analysis and verification: roughly $0.75–$2.00, depending on how many passes repeat
  the transcript and the selected model;
- embeddings/storage/WordPress: pennies at this volume.

The timestamped `whisper-1` adapter should be price-verified before the live run because the current
pricing table emphasizes newer transcription models. Estimate each live episode as:

`audio minutes × transcription price/minute + analysis input tokens × input price/token + analysis output tokens × output price/token`

Record usage returned by every provider call in `model_runs`. After three live episodes, report
median and worst-case cost instead of extrapolating from list prices alone. A useful test set is one
clean studio recording, one crosstalk-heavy recording, and one episode rich in names/Scripture.

## Transcription quality

Not yet measured: fixtures are description-derived excerpts, not audio transcripts. Acceptance for
the live trial should include word error rate on a manually corrected 10-minute excerpt, name and
Scripture-reference accuracy, timestamp drift, speaker-attribution accuracy, and omission rate.

## Model usage

- Transcription default: `whisper-1`, selected because the current timestamped adapter requires
  structured segment timestamps.
- Analysis default: schema-constrained OpenAI model, with canonical Pydantic validation after the
  API response.
- Tests: deterministic rules only.

Use multiple focused analysis calls before archive expansion: section boundaries, extraction,
section synthesis, episode synthesis, and evidence verification. The prototype exposes the boundary
but keeps one paid analysis call until prompt behavior is observed.

## Opportunities to simplify

- Keep file storage and Git; do not add a database until concurrency or query latency requires it.
- Keep WordPress rendering server-side and stateless.
- Do not add embeddings until canonical topics and reviewer corrections are stable.
- Avoid a workflow engine; normalized checkpoints plus append-only logs are enough for the pilot.

## Recommended changes before the full archive

1. Confirm the real RSS feed and select three audio episodes spanning recording conditions.
2. Add ffmpeg chunking/compression and a timestamp-offset integration test.
3. Add a second transcription provider and manually corrected reference excerpt.
4. Split AI analysis into focused stages and add evidence-entailment verification.
5. Add an immutable WordPress episode-ID custom field and search-before-create recovery.
6. Review all three reports manually; set thresholds from observed errors.
7. Only then process a 10-episode pilot. Re-review cost, error rates, topic duplication, and related
   links before authorizing the archive.

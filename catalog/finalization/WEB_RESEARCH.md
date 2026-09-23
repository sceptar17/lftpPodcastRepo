# Legacy archive research notes

Research date: 2026-09-23

## Finding

The public evidence supports the legitimacy of the pre-RSS archive. It does not
yet prove an additional missing full episode.

Apple Podcasts currently reports 465 episodes and a 2013–2026 active range.
That listing reflects the current feed, not the complete history: the official
YouTube channel contains show material from 2010 that lines up with the local
recordings.

The oldest channel description identifies the original distribution context:
Live From The Path was a `desmoinesamplified.com` radio broadcast on Mondays
from 10 p.m. to midnight. This explains why several local filenames use the
following Tuesday's date while the video title names the Monday show date.

## 2010 cross-check

| Public YouTube evidence | Published | Corresponding local master evidence |
|---|---:|---|
| [An Introduction to Live From The Path](https://www.youtube.com/watch?v=XsLLKUfeRK4) | 2010-06-16 | `2010/20100615-09.mp3` |
| [Show Segment – Homeless Tithe and the Million Dollar Mall](https://www.youtube.com/watch?v=dkSask-4Jwo) | 2010-06-16 | `2010/20100615-09.mp3` |
| [Question 6/21 – Art, Artists, and the Church](https://www.youtube.com/watch?v=nB6M7phXrYU) | 2010-06-22 | `2010/20100622-03.mp3` |
| [Show Segment – Witness @ Work](https://www.youtube.com/watch?v=2QKfjlVIFtU) | 2010-06-22 | `2010/20100622-03.mp3` |
| [06/28 Question – Atmosphere for Unbelievers](https://www.youtube.com/watch?v=SojQCJuFTiM) | 2010-06-29 | `2010/20100629-04.mp3` |
| [The Church Has No (Human) Head](https://www.youtube.com/watch?v=rFNr6tIbGNs) | 2010-06-29 | `2010/20100629-04.mp3` |
| [Grace](https://www.youtube.com/watch?v=2K6Pvw93FYM) | 2010-07-07 | `2010/20100706-03.mp3` |
| [7/5 Question](https://www.youtube.com/watch?v=huaAuEAIuvQ) | 2010-07-07 | `2010/20100706-03.mp3` |

The one-day offset is consistent with a show/recording date followed by a
YouTube upload. These are corroborating fragments, not separate episodes.

## Sources checked

- [Official YouTube channel](https://www.youtube.com/@livefromthepath/videos):
  approximately 2,000 videos, with 2010 show segments visible among the oldest
  uploads.
- [Current Apple Podcasts listing](https://podcasts.apple.com/us/podcast/live-from-the-path/id634981162):
  current-feed inventory only; it should not be used to reject older local
  episodes.
- [Official podcast archive](https://livefromthepath.org/podcasts/live-from-the-path/):
  useful for current WordPress/RSS records.
- Targeted searches for Pathway Church, Blip.tv, Des Moines Amplified, and the
  Internet Archive. No independent, enumerated legacy episode archive was
  discoverable through indexed search results in this pass.
- Targeted Vimeo searches for the show name, Des Moines Amplified, and Pathway
  Church returned no attributable result. Vimeo remains a historical lead; this
  is not an absence finding because old videos may be unindexed, retitled, or
  attached to an account name we have not recovered.

## Legacy source map for the next archive pass

| Source | What to recover | Status |
|---|---|---|
| `desmoinesamplified.com` | 2010–era show pages, schedule, player/embed URLs, archived audio links | Confirmed as the original radio host by the oldest official YouTube description; query Wayback snapshots |
| FeedBurner | Old feed XML, original source-feed URL, enclosure URLs, GUIDs | User-confirmed historical lead; feed slug still unresolved |
| Blip.tv | Show/channel pages, video IDs, descriptions, upload dates | User previously found Wayback evidence; query archived Blip show and user pages |
| Pathway Church | Program pages, announcements, embedded players, host names | Lead; exact historical domain/path unresolved |
| Official YouTube | Video IDs, exact publication dates, descriptions, durations, transcript availability | Confirmed, public, roughly 2,000 videos |
| Vimeo | Video IDs, account identity, exact publication dates, descriptions, durations | User-confirmed possible host; no indexed LFTP result yet |
| Current WordPress/RSS | 2013–present feed records and current enclosure references | Already inventoried |

Wayback's CDX endpoint was not reachable from the current research environment,
so the absence of indexed search results is not an absence finding. A direct
CDX export should be run for each historical domain and any FeedBurner/Blip URLs
found inside those snapshots.

## Gap candidates—not presumed missing episodes

Weekly-cadence gaps worth checking against a complete YouTube export or other
legacy index:

- 2010: weeks around June 1 and December 6.
- 2011: weeks around January 10; February 7/14; March 14/28; May 9/23;
  July 4/11; August 8; and November 14.
- 2012: weeks around February 6; April 16/23; May 14; June 18; July 23;
  and August 6.

These dates are leads only. Scheduled breaks, special events, and multi-part
recordings can create the same pattern.

## Completed public-media inventory

The official YouTube export recovered 862 public video records with durations.
Conservative matching currently classifies 355 as likely existing/supporting
sources, 488 as short supporting clips, 15 as long-form records needing better
date evidence, and 4 as possible missing long-form media items. The last group
represents as many as three broadcasts: two parts from the same 2011 Judgment
Day show, plus full-length 2017 and 2018 shows. These are recovery candidates,
not automatic catalog additions; see
`research/MEDIA_RECONCILIATION.md`.

The Des Moines Amplified snapshot recovered 34 named broadcasts from March 20
through November 15, 2010. Twenty-two match one current master by date, one maps
to multiple local candidates, and 11 are source-only candidates. Ten of those
are clear early/gap records; `Tyson vs. Holyfield` (October 11) must first be
compared with the oddly dated October 15 local recording. See
`research/DES_MOINES_RECONCILIATION.md`.

The user-provided Des Moines Amplified snapshot is now a fixed research seed:
`2010-11-23 / show.asp?showid=56`. Run `Run Legacy Research.cmd` from the project
folder to preserve that page, query archive indexes, inventory YouTube and
Vimeo, and write `catalog/finalization/research/media-candidates.csv`. The
result is evidence for reconciliation, not an automatic change to the master
catalog.

`research/master-catalog-proposal.json` applies the 22 unambiguous historical
matches, preserves the one-to-many July match for review, adds all 11
source-confirmed episodes with missing/unmatched audio, and renumbers the annual
sequence. It contains 677 episode candidates versus the 666-record audio/RSS
baseline; it does not collapse the unresolved July files or the October 11/15
possibility.

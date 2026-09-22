from __future__ import annotations

import json
import os
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .archive_sync import (
    create_metadata_job,
    create_sync_job,
    execute_metadata_job,
    execute_sync_job,
    latest_sync_job,
)
from .benchmark import create_run, execute_run, load_runs, load_transcript
from .catalog import build_episode_ledger, load_ledger, load_reconstruction
from .config import Settings
from .inventory import (
    AUDIO_EXTENSIONS,
    InventorySnapshot,
    build_inventory,
    load_discovered,
    provider_profiles,
)
from .job_status import ACTIVE_STATUSES, duration_label, job_view
from .models import DiscoveredEpisode, Episode, ProcessingStatus, ReconstructionReport
from .render import episode_markdown, review_report, timestamp, wordpress_html
from .repository import Repository
from .rss import fetch_rss

PACKAGE_ROOT = Path(__file__).parent


def _duplicate_review_queue(
    reconstruction: ReconstructionReport | None, decisions: dict, show_reviewed: bool
) -> tuple[list, int]:
    if reconstruction is None:
        return [], 0
    duplicate_ids = {proposal.proposal_id for proposal in reconstruction.duplicate_proposals}
    reviewed_ids = {
        proposal_id
        for proposal_id, record in decisions.items()
        if proposal_id in duplicate_ids and record.get("decision") in {"confirmed", "rejected"}
    }
    reviewed_ids.update(
        proposal.proposal_id
        for proposal in reconstruction.duplicate_proposals
        if proposal.relationship == "exact-copy"
    )
    proposals = (
        reconstruction.duplicate_proposals
        if show_reviewed
        else [
            proposal
            for proposal in reconstruction.duplicate_proposals
            if proposal.proposal_id not in reviewed_ids
        ]
    )
    return proposals, len(reviewed_ids)


def _match_review_queue(
    reconstruction: ReconstructionReport | None, decisions: dict, show_reviewed: bool
) -> tuple[list, int]:
    if reconstruction is None:
        return [], 0
    match_ids = {proposal.proposal_id for proposal in reconstruction.match_proposals}
    completed_ids = {
        proposal_id
        for proposal_id, record in decisions.items()
        if proposal_id in match_ids and record.get("decision") in {"confirmed", "rejected"}
    }
    completed_ids.update(
        proposal.proposal_id
        for proposal in reconstruction.match_proposals
        if proposal.recommendation == "auto-link"
    )
    proposals = (
        reconstruction.match_proposals
        if show_reviewed
        else [
            proposal
            for proposal in reconstruction.match_proposals
            if proposal.proposal_id not in completed_ids
        ]
    )
    return proposals, len(completed_ids)


def _recover_interrupted_catalog_job(repository: Repository) -> None:
    path = repository.root / "state" / "catalog-build.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if payload.get("status") not in ACTIVE_STATUSES:
        return
    now = datetime.now(UTC).isoformat()
    payload.update(
        status="interrupted",
        stage="Previous catalog scan was interrupted",
        updated_at=now,
        finished_at=now,
        error="The app stopped before this catalog scan completed. It is safe to rebuild.",
    )
    repository.atomic_json("state/catalog-build.json", payload)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = Repository(settings.root)
    _recover_interrupted_catalog_job(repository)
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
    templates.env.filters["timestamp"] = timestamp
    templates.env.filters["pretty_date"] = pretty_date
    templates.env.filters["duration"] = duration_label
    app = FastAPI(title="LFTP Knowledge Repository", version="0.2.0")
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")
    app.state.settings = settings
    app.state.repository = repository
    app.state.templates = templates

    def context(request: Request, **values):
        return {"request": request, "current_path": request.url.path, **values}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        effective = _effective_inputs(repository.root, settings)
        inventory = build_inventory(repository, effective["local_audio_root"])
        episodes = sorted(inventory.processed, key=lambda item: item.publication_date, reverse=True)
        review = [
            episode for episode in episodes if episode.status == ProcessingStatus.NEEDS_REVIEW
        ]
        states = _load_states(repository.root / "state")
        jobs = _operation_jobs(repository)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            context(
                request,
                inventory=inventory,
                episodes=episodes[:6],
                review=review[:6],
                states=states,
                providers=provider_profiles(),
                jobs=jobs,
            ),
        )

    @app.get("/episodes", response_class=HTMLResponse)
    def episode_list(request: Request, status: str | None = None):
        episodes = sorted(
            repository.episodes(), key=lambda item: item.publication_date, reverse=True
        )
        if status:
            episodes = [episode for episode in episodes if episode.status.value == status]
        return templates.TemplateResponse(
            request,
            "episodes.html",
            context(
                request,
                episodes=episodes,
                selected_status=status,
            ),
        )

    @app.get("/episodes/{episode_id}", response_class=HTMLResponse)
    def episode_detail(request: Request, episode_id: str):
        episode = _episode_or_404(repository, episode_id)
        note_path = repository.root / "state" / "review-notes" / f"{episode_id}.json"
        notes = (
            json.loads(note_path.read_text(encoding="utf-8")).get("notes", "")
            if note_path.exists()
            else ""
        )
        return templates.TemplateResponse(
            request,
            "episode.html",
            context(
                request,
                episode=episode,
                notes=notes,
            ),
        )

    @app.post("/episodes/{episode_id}/notes")
    def save_notes(episode_id: str, notes: str = Form("")):
        _episode_or_404(repository, episode_id)
        repository.atomic_json(f"state/review-notes/{episode_id}.json", {"notes": notes})
        return RedirectResponse(f"/episodes/{episode_id}#review", status_code=303)

    @app.post("/episodes/{episode_id}/review-status")
    def set_review_status(episode_id: str, action: str = Form(...)):
        episode = _episode_or_404(repository, episode_id)
        if action == "approve":
            if not episode.transcript.is_complete:
                repository.atomic_json(
                    f"state/review-notes/{episode_id}.json",
                    {"notes": "Approval blocked: the transcript is marked incomplete."},
                )
            else:
                episode.status = ProcessingStatus.COMPLETE
                episode.qa.manual_review_flags = []
                repository.save_episode(episode)
        elif action == "needs-review":
            episode.status = ProcessingStatus.NEEDS_REVIEW
            repository.save_episode(episode)
        else:
            raise HTTPException(400, "Unknown review action")
        return RedirectResponse(f"/episodes/{episode_id}#review", status_code=303)

    @app.post("/episodes/{episode_id}/render")
    def rerender_episode(episode_id: str):
        episode = _episode_or_404(repository, episode_id)
        repository.atomic_text(f"episodes/{episode_id}.md", episode_markdown(episode))
        repository.atomic_text(f"reports/{episode_id}-review.md", review_report(episode))
        repository.atomic_text(f"state/wordpress-drafts/{episode_id}.html", wordpress_html(episode))
        return RedirectResponse(f"/episodes/{episode_id}#outputs", status_code=303)

    @app.get("/topics", response_class=HTMLResponse)
    def topics(request: Request):
        records = []
        for path in sorted((repository.root / "topics").glob("*.json")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return templates.TemplateResponse(request, "topics.html", context(request, topics=records))

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog_page(
        request: Request,
        year: str | None = None,
        show_reviewed: bool = False,
        show_reviewed_links: bool = False,
    ):
        ledger = load_ledger(repository)
        reconstruction = load_reconstruction(repository)
        decision_payload = _load_json(
            repository.root / "catalog" / "reconstruction-decisions.json"
        ) or {"decisions": {}}
        decisions = decision_payload.get("decisions", {})
        duplicate_proposals, reviewed_duplicate_count = _duplicate_review_queue(
            reconstruction, decisions, show_reviewed
        )
        match_proposals, reviewed_match_count = _match_review_queue(
            reconstruction, decisions, show_reviewed_links
        )
        state_path = repository.root / "state" / "catalog-build.json"
        build_state = job_view(
            json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        )
        candidates = ledger.candidates if ledger else []
        selected_year = int(year) if year and year.isdigit() else None
        show_unknown = year == "unknown"
        if selected_year is not None:
            candidates = [
                item
                for item in candidates
                if item.publication_date and item.publication_date.startswith(str(selected_year))
            ]
        elif show_unknown:
            candidates = [item for item in candidates if not item.publication_date]
        return templates.TemplateResponse(
            request,
            "catalog.html",
            context(
                request,
                ledger=ledger,
                candidates=candidates,
                selected_year=year,
                build_state=build_state,
                reconstruction=reconstruction,
                duplicate_proposals=duplicate_proposals,
                reviewed_duplicate_count=reviewed_duplicate_count,
                show_reviewed=show_reviewed,
                match_proposals=match_proposals,
                reviewed_match_count=reviewed_match_count,
                show_reviewed_links=show_reviewed_links,
                assets=(
                    {asset.asset_id: asset for asset in reconstruction.assets}
                    if reconstruction
                    else {}
                ),
                decisions=decisions,
                rss_records={
                    episode.episode_id: episode for episode in load_discovered(repository)
                },
                rss_audio_names={
                    episode.episode_id: Path(
                        urllib.parse.unquote(urllib.parse.urlparse(episode.audio_url).path)
                    ).name
                    for episode in load_discovered(repository)
                },
            ),
        )

    @app.post("/catalog/rebuild")
    def rebuild_catalog(background_tasks: BackgroundTasks):
        effective = _effective_inputs(repository.root, settings)
        archive_root = effective["local_audio_root"]
        if archive_root is None or not archive_root.exists() or not archive_root.is_dir():
            raise HTTPException(400, "Configure an existing local archive folder first")
        state_path = repository.root / "state" / "catalog-build.json"
        current = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if current.get("status") in {"queued", "running"}:
            raise HTTPException(409, "A catalog scan is already running")
        created = datetime.now(UTC).isoformat()
        repository.atomic_json(
            "state/catalog-build.json",
            {
                "status": "queued",
                "stage": "Waiting to scan archive",
                "created_at": created,
                "started_at": None,
                "updated_at": created,
                "finished_at": None,
                "total": 0,
                "processed": 0,
                "current_label": None,
            },
        )
        background_tasks.add_task(_execute_catalog_build, repository, archive_root)
        return RedirectResponse("/catalog", status_code=303)

    @app.get("/catalog/output/{kind}")
    def catalog_output(kind: str):
        choices = {
            "json": repository.root / "catalog" / "master-ledger.json",
            "csv": repository.root / "catalog" / "master-ledger.csv",
            "reconstruction": repository.root / "catalog" / "reconstruction-report.json",
        }
        if kind not in choices or not choices[kind].exists():
            raise HTTPException(404, "Catalog output not found")
        return FileResponse(choices[kind])

    @app.get("/catalog/assets/{asset_id}/audio")
    def catalog_asset_audio(asset_id: str):
        reconstruction = load_reconstruction(repository)
        effective = _effective_inputs(repository.root, settings)
        archive_root = effective["local_audio_root"]
        asset = (
            next((item for item in reconstruction.assets if item.asset_id == asset_id), None)
            if reconstruction
            else None
        )
        if asset is None or archive_root is None:
            raise HTTPException(404, "Audio asset not found")
        target = (archive_root / asset.relative_path).resolve()
        if not target.is_relative_to(archive_root.resolve()) or not target.is_file():
            raise HTTPException(404, "Audio asset not found")
        return FileResponse(target)

    @app.post("/catalog/proposals/{proposal_id}")
    def decide_catalog_proposal(
        proposal_id: str,
        action: str = Form(...),
        notes: str = Form(""),
        preferred_asset_id: str = Form(""),
    ):
        reconstruction = load_reconstruction(repository)
        duplicate = (
            next(
                (
                    item
                    for item in reconstruction.duplicate_proposals
                    if item.proposal_id == proposal_id
                ),
                None,
            )
            if reconstruction
            else None
        )
        match = (
            next(
                (
                    item
                    for item in reconstruction.match_proposals
                    if item.proposal_id == proposal_id
                ),
                None,
            )
            if reconstruction
            else None
        )
        if duplicate is None and match is None:
            raise HTTPException(404, "Reconstruction proposal not found")
        if action not in {"confirmed", "rejected", "clear"}:
            raise HTTPException(400, "Unknown decision")
        path = repository.root / "catalog" / "reconstruction-decisions.json"
        payload = _load_json(path) or {"schema_version": "1.0.0", "decisions": {}}
        if action == "clear":
            payload["decisions"].pop(proposal_id, None)
        else:
            if preferred_asset_id and (
                duplicate is None or preferred_asset_id not in duplicate.asset_ids
            ):
                raise HTTPException(400, "Preferred asset is not part of this proposal")
            payload["decisions"][proposal_id] = {
                "decision": action,
                "notes": notes.strip(),
                "preferred_asset_id": preferred_asset_id or None,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        payload["updated_at"] = datetime.now(UTC).isoformat()
        repository.atomic_json("catalog/reconstruction-decisions.json", payload)
        return RedirectResponse("/catalog#reconstruction", status_code=303)

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory(request: Request):
        effective = _effective_inputs(repository.root, settings)
        snapshot = build_inventory(repository, effective["local_audio_root"])
        metadata_candidates = _metadata_candidates(snapshot)
        return templates.TemplateResponse(
            request,
            "inventory.html",
            context(
                request,
                inventory=snapshot,
                providers=provider_profiles(),
                settings=settings,
                effective=effective,
                sync_job=job_view(latest_sync_job(repository)),
                rss_job=job_view(_load_json(repository.root / "state" / "rss-refresh.json")),
                metadata_candidate_count=len(metadata_candidates),
            ),
        )

    @app.post("/inventory/refresh")
    def refresh_inventory(background_tasks: BackgroundTasks):
        effective = _effective_inputs(repository.root, settings)
        if not effective["rss_url"]:
            raise HTTPException(400, "LFTP_RSS_URL is not configured")
        state_path = repository.root / "state" / "rss-refresh.json"
        current = _load_json(state_path) or {}
        if current.get("status") in ACTIVE_STATUSES:
            raise HTTPException(409, "An RSS refresh is already running")
        repository.atomic_json(
            "state/rss-refresh.json",
            {
                "status": "queued",
                "stage": "Waiting to contact RSS feed",
                "created_at": datetime.now(UTC).isoformat(),
                "started_at": None,
                "updated_at": None,
                "finished_at": None,
                "total": 1,
                "processed": 0,
            },
        )
        background_tasks.add_task(
            _execute_rss_refresh, repository, effective["rss_url"], settings.http_timeout_seconds
        )
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/inventory/sync")
    def sync_archive(
        background_tasks: BackgroundTasks,
        action: str = Form("selected"),
        episode_ids: list[str] = Form(default=[]),  # noqa: B008
    ):
        effective = _effective_inputs(repository.root, settings)
        archive_root = effective["local_audio_root"]
        if archive_root is None or not archive_root.exists() or not archive_root.is_dir():
            raise HTTPException(400, "Configure an existing local archive folder first")
        snapshot = build_inventory(repository, archive_root)
        available = {episode.episode_id: episode for episode in snapshot.feed_only_audio}
        selected = (
            list(available.values())
            if action == "all"
            else [available[episode_id] for episode_id in episode_ids if episode_id in available]
        )
        if not selected:
            raise HTTPException(400, "Select at least one RSS-only episode")
        current = latest_sync_job(repository)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(409, "An archive download job is already running")
        job = create_sync_job(repository, archive_root, selected)
        background_tasks.add_task(
            execute_sync_job, repository, job["job_id"], settings.http_timeout_seconds
        )
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/inventory/sidecars")
    def generate_archive_sidecars(background_tasks: BackgroundTasks):
        effective = _effective_inputs(repository.root, settings)
        archive_root = effective["local_audio_root"]
        if archive_root is None or not archive_root.exists() or not archive_root.is_dir():
            raise HTTPException(400, "Configure an existing local archive folder first")
        current = latest_sync_job(repository)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(409, "An archive job is already running")
        candidates = _metadata_candidates(build_inventory(repository, archive_root))
        if not candidates:
            raise HTTPException(400, "No confidently matched files need metadata")
        job = create_metadata_job(repository, archive_root, candidates)
        background_tasks.add_task(execute_metadata_job, repository, job["job_id"])
        return RedirectResponse("/inventory", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        overrides = _load_overrides(repository.root)
        return templates.TemplateResponse(
            request,
            "settings.html",
            context(
                request,
                settings=settings,
                overrides=overrides,
                providers=provider_profiles(),
            ),
        )

    @app.post("/settings")
    def save_settings(
        rss_url: str = Form(""),
        local_audio_root: str = Form(""),
        transcription_provider: str = Form(""),
        analysis_provider: str = Form(""),
    ):
        repository.atomic_json(
            "state/app-settings.json",
            {
                "rss_url": rss_url.strip(),
                "local_audio_root": local_audio_root.strip(),
                "transcription_provider": transcription_provider.strip(),
                "analysis_provider": analysis_provider.strip(),
                "notice": "Non-secret UI preferences. Environment variables remain authoritative at startup.",
            },
        )
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/transcription-lab", response_class=HTMLResponse)
    def transcription_lab(request: Request, run_id: str | None = None):
        runs = [job_view(run) for run in load_runs(repository)]
        selected = next((run for run in runs if run["run_id"] == run_id), None)
        transcript = load_transcript(repository, selected) if selected else None
        return templates.TemplateResponse(
            request,
            "transcription_lab.html",
            context(
                request,
                runs=runs,
                selected=selected,
                transcript=transcript,
                local_audio_root=settings.local_audio_root,
            ),
        )

    @app.post("/transcription-lab/run")
    def run_local_benchmark(
        background_tasks: BackgroundTasks,
        audio_path: str = Form(...),
        model: str = Form("small.en"),
        sample_minutes: int = Form(10),
    ):
        source = Path(audio_path.strip().strip('"')).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise HTTPException(400, "Choose an existing audio or video file")
        if model not in {"tiny.en", "base.en", "small.en", "medium.en"}:
            raise HTTPException(400, "Unsupported benchmark model")
        if sample_minutes < 1 or sample_minutes > 30:
            raise HTTPException(400, "Sample length must be between 1 and 30 minutes")
        run = create_run(repository, source, model, sample_minutes * 60)
        background_tasks.add_task(execute_run, repository, run["run_id"])
        return RedirectResponse(f"/transcription-lab?run_id={run['run_id']}", status_code=303)

    @app.get("/outputs/{episode_id}/{kind}")
    def output_file(episode_id: str, kind: str):
        _episode_or_404(repository, episode_id)
        choices = {
            "json": repository.root / "episodes" / f"{episode_id}.json",
            "markdown": repository.root / "episodes" / f"{episode_id}.md",
            "report": repository.root / "reports" / f"{episode_id}-review.md",
            "wordpress": repository.root / "state" / "wordpress-drafts" / f"{episode_id}.html",
        }
        if kind not in choices or not choices[kind].exists():
            raise HTTPException(404, "Output not found")
        return FileResponse(choices[kind])

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "episodes": len(list(repository.episodes())),
            "feed_items": len(load_discovered(repository)),
        }

    return app


def pretty_date(value, style: str = "short") -> str:
    """Format dates without platform-specific strftime flags such as %-d."""
    month = value.strftime("%B" if style == "long" else "%b")
    return f"{month} {value.day}, {value.year}"


def _episode_or_404(repository: Repository, episode_id: str) -> Episode:
    episode = repository.load_episode(episode_id)
    if episode is None:
        raise HTTPException(404, "Episode not found")
    return episode


def _load_states(path: Path) -> list[dict]:
    result = []
    for item in sorted(path.glob("*.json")):
        if item.name in {"discovered.json", "app-settings.json"}:
            continue
        try:
            value = json.loads(item.read_text(encoding="utf-8"))
            if "episode_id" in value:
                result.append(value)
        except json.JSONDecodeError:
            continue
    return result


def _load_overrides(root: Path) -> dict:
    path = root / "state" / "app-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _effective_inputs(root: Path, settings: Settings) -> dict:
    overrides = _load_overrides(root)
    local_value = overrides.get("local_audio_root")
    local_root = (
        Path(local_value).expanduser().resolve() if local_value else settings.local_audio_root
    )
    return {
        "rss_url": overrides.get("rss_url") or settings.rss_url,
        "local_audio_root": local_root,
    }


def _metadata_candidates(
    snapshot: InventorySnapshot,
) -> list[tuple[Path, DiscoveredEpisode, str]]:
    discovered = {episode.episode_id: episode for episode in snapshot.discovered}
    candidates = []
    for item in snapshot.local_audio:
        if item.match_status != "matched" or not item.match_episode_id:
            continue
        path = Path(item.path)
        if path.with_suffix(".rss.json").exists():
            continue
        episode = discovered.get(item.match_episode_id)
        if episode:
            candidates.append((path, episode, item.match_reason or "High-confidence match"))
    return candidates


def _execute_catalog_build(repository: Repository, archive_root: Path) -> None:
    started = datetime.now(UTC)
    relative = "state/catalog-build.json"
    last_progress = {"total": 0, "processed": 0, "current_label": None}
    repository.atomic_json(
        relative,
        {
            "status": "running",
            "stage": "Scanning local filenames",
            "created_at": started.isoformat(),
            "started_at": started.isoformat(),
            "updated_at": started.isoformat(),
            "finished_at": None,
            "total": 0,
            "processed": 0,
            "current_label": None,
        },
    )
    try:
        snapshot = build_inventory(repository, archive_root)

        def report(processed: int, total: int, stage: str, label: str | None) -> None:
            last_progress.update(total=total, processed=processed, current_label=label)
            repository.atomic_json(
                relative,
                {
                    "status": "running",
                    "stage": stage,
                    "created_at": started.isoformat(),
                    "started_at": started.isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                    "finished_at": None,
                    "total": total,
                    "processed": processed,
                    "current_label": label,
                },
            )

        ledger = build_episode_ledger(repository, snapshot, archive_root, progress=report)
        repository.atomic_json(
            relative,
            {
                "status": "complete",
                "started_at": started.isoformat(),
                "created_at": started.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "stage": "Catalog complete",
                "current_label": None,
                "total": last_progress["total"],
                "processed": last_progress["total"],
                "candidate_count": ledger.candidate_count,
            },
        )
    except Exception as error:  # noqa: BLE001 - state must retain background failures
        repository.atomic_json(
            relative,
            {
                "status": "failed",
                "started_at": started.isoformat(),
                "created_at": started.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "stage": "Catalog scan failed",
                "total": last_progress["total"],
                "processed": last_progress["processed"],
                "current_label": last_progress["current_label"],
                "error": f"{type(error).__name__}: {error}",
            },
        )


def _execute_rss_refresh(repository: Repository, rss_url: str, timeout: int) -> None:
    relative = "state/rss-refresh.json"
    started = datetime.now(UTC)
    repository.atomic_json(
        relative,
        {
            "status": "running",
            "stage": "Fetching and parsing RSS feed",
            "created_at": started.isoformat(),
            "started_at": started.isoformat(),
            "updated_at": started.isoformat(),
            "finished_at": None,
            "total": 1,
            "processed": 0,
        },
    )
    try:
        body, episodes = fetch_rss(rss_url, timeout)
        repository.atomic_text("raw/rss/latest.xml", body.decode(errors="replace"))
        repository.atomic_json(
            "state/discovered.json", [episode.model_dump(mode="json") for episode in episodes]
        )
        repository.atomic_json(
            relative,
            {
                "status": "complete",
                "stage": "RSS inventory updated",
                "created_at": started.isoformat(),
                "started_at": started.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "total": 1,
                "processed": 1,
                "episode_count": len(episodes),
            },
        )
    except Exception as error:  # noqa: BLE001 - state must retain network/parser failures
        repository.atomic_json(
            relative,
            {
                "status": "failed",
                "stage": "RSS refresh failed",
                "created_at": started.isoformat(),
                "started_at": started.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "total": 1,
                "processed": 0,
                "error": f"{type(error).__name__}: {error}",
            },
        )


def _operation_jobs(repository: Repository) -> list[dict]:
    records: list[tuple[str, str, dict | None]] = [
        ("Master catalog", "/catalog", _load_json(repository.root / "state/catalog-build.json")),
        ("RSS inventory", "/inventory", _load_json(repository.root / "state/rss-refresh.json")),
        ("Archive operation", "/inventory", latest_sync_job(repository)),
    ]
    runs = load_runs(repository)
    if runs:
        records.append(
            ("Transcription test", f"/transcription-lab?run_id={runs[0]['run_id']}", runs[0])
        )
    result = []
    for name, href, record in records:
        if record:
            view = job_view(record)
            view.update(name=name, href=href)
            result.append(view)
    return sorted(
        result,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )[:4]


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "lftp_kb.web:app",
        host=os.getenv("LFTP_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("LFTP_WEB_PORT", "8080")),
        reload=False,
    )

from __future__ import annotations

import json
import os
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
from .config import Settings
from .inventory import (
    AUDIO_EXTENSIONS,
    InventorySnapshot,
    build_inventory,
    load_discovered,
    provider_profiles,
)
from .models import DiscoveredEpisode, Episode, ProcessingStatus
from .render import episode_markdown, review_report, timestamp, wordpress_html
from .repository import Repository
from .rss import fetch_rss

PACKAGE_ROOT = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = Repository(settings.root)
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
    templates.env.filters["timestamp"] = timestamp
    templates.env.filters["pretty_date"] = pretty_date
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
        review = [episode for episode in episodes if episode.status == ProcessingStatus.NEEDS_REVIEW]
        states = _load_states(repository.root / "state")
        return templates.TemplateResponse(request, "dashboard.html", context(request,
            inventory=inventory, episodes=episodes[:6], review=review[:6], states=states,
            providers=provider_profiles(),
        ))

    @app.get("/episodes", response_class=HTMLResponse)
    def episode_list(request: Request, status: str | None = None):
        episodes = sorted(repository.episodes(), key=lambda item: item.publication_date, reverse=True)
        if status:
            episodes = [episode for episode in episodes if episode.status.value == status]
        return templates.TemplateResponse(request, "episodes.html", context(request,
            episodes=episodes, selected_status=status,
        ))

    @app.get("/episodes/{episode_id}", response_class=HTMLResponse)
    def episode_detail(request: Request, episode_id: str):
        episode = _episode_or_404(repository, episode_id)
        note_path = repository.root / "state" / "review-notes" / f"{episode_id}.json"
        notes = (
            json.loads(note_path.read_text(encoding="utf-8")).get("notes", "")
            if note_path.exists()
            else ""
        )
        return templates.TemplateResponse(request, "episode.html", context(request,
            episode=episode, notes=notes,
        ))

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
                repository.atomic_json(f"state/review-notes/{episode_id}.json", {
                    "notes": "Approval blocked: the transcript is marked incomplete."
                })
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

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory(request: Request):
        effective = _effective_inputs(repository.root, settings)
        snapshot = build_inventory(repository, effective["local_audio_root"])
        metadata_candidates = _metadata_candidates(snapshot)
        return templates.TemplateResponse(request, "inventory.html", context(request,
            inventory=snapshot, providers=provider_profiles(), settings=settings,
            effective=effective, sync_job=latest_sync_job(repository),
            metadata_candidate_count=len(metadata_candidates),
        ))

    @app.post("/inventory/refresh")
    def refresh_inventory():
        effective = _effective_inputs(repository.root, settings)
        if not effective["rss_url"]:
            raise HTTPException(400, "LFTP_RSS_URL is not configured")
        body, episodes = fetch_rss(effective["rss_url"], settings.http_timeout_seconds)
        repository.atomic_text("raw/rss/latest.xml", body.decode(errors="replace"))
        repository.atomic_json("state/discovered.json", [e.model_dump(mode="json") for e in episodes])
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/inventory/rescan")
    def rescan_inventory():
        return RedirectResponse("/inventory", status_code=303)

    @app.post("/inventory/sync")
    def sync_archive(background_tasks: BackgroundTasks, action: str = Form("selected"),
                     episode_ids: list[str] = Form(default=[])):  # noqa: B008
        effective = _effective_inputs(repository.root, settings)
        archive_root = effective["local_audio_root"]
        if archive_root is None or not archive_root.exists() or not archive_root.is_dir():
            raise HTTPException(400, "Configure an existing local archive folder first")
        snapshot = build_inventory(repository, archive_root)
        available = {episode.episode_id: episode for episode in snapshot.feed_only_audio}
        selected = list(available.values()) if action == "all" else [
            available[episode_id] for episode_id in episode_ids if episode_id in available
        ]
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
        return templates.TemplateResponse(request, "settings.html", context(request,
            settings=settings, overrides=overrides, providers=provider_profiles(),
        ))

    @app.post("/settings")
    def save_settings(rss_url: str = Form(""), local_audio_root: str = Form(""),
                      transcription_provider: str = Form(""), analysis_provider: str = Form("")):
        repository.atomic_json("state/app-settings.json", {
            "rss_url": rss_url.strip(), "local_audio_root": local_audio_root.strip(),
            "transcription_provider": transcription_provider.strip(),
            "analysis_provider": analysis_provider.strip(),
            "notice": "Non-secret UI preferences. Environment variables remain authoritative at startup.",
        })
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/transcription-lab", response_class=HTMLResponse)
    def transcription_lab(request: Request, run_id: str | None = None):
        runs = load_runs(repository)
        selected = next((run for run in runs if run["run_id"] == run_id), None)
        transcript = load_transcript(repository, selected) if selected else None
        return templates.TemplateResponse(request, "transcription_lab.html", context(request,
            runs=runs, selected=selected, transcript=transcript,
            local_audio_root=settings.local_audio_root,
        ))

    @app.post("/transcription-lab/run")
    def run_local_benchmark(background_tasks: BackgroundTasks,
                            audio_path: str = Form(...), model: str = Form("small.en"),
                            sample_minutes: int = Form(10)):
        source = Path(audio_path.strip().strip('"')).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise HTTPException(400, "Choose an existing audio or video file")
        if model not in {"tiny.en", "base.en", "small.en", "medium.en"}:
            raise HTTPException(400, "Unsupported benchmark model")
        if sample_minutes < 1 or sample_minutes > 30:
            raise HTTPException(400, "Sample length must be between 1 and 30 minutes")
        run = create_run(repository, source, model, sample_minutes * 60)
        background_tasks.add_task(execute_run, repository, run["run_id"])
        return RedirectResponse(
            f"/transcription-lab?run_id={run['run_id']}", status_code=303
        )

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
        return {"status": "ok", "episodes": len(list(repository.episodes())),
                "feed_items": len(load_discovered(repository))}

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


def _effective_inputs(root: Path, settings: Settings) -> dict:
    overrides = _load_overrides(root)
    local_value = overrides.get("local_audio_root")
    local_root = Path(local_value).expanduser().resolve() if local_value else settings.local_audio_root
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


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run("lftp_kb.web:app", host=os.getenv("LFTP_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("LFTP_WEB_PORT", "8080")), reload=False)

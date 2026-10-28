from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.api import set_session_cookie
from app.deps import get_db, optional_user, require_user
from app.domain import (
    add_pattern_source,
    add_source,
    a3_for_problem,
    ask_assistant,
    build_prompt_use,
    approve_summary,
    confirm_subscription,
    create_campaign,
    create_document,
    create_ingestion_job,
    create_exercise,
    create_learning_tool,
    create_manual_summary,
    create_user,
    customer_dashboard,
    customer_prompt,
    customer_thread,
    delete_pattern_source,
    delete_source,
    generate_summary_draft,
    diagram_view,
    exercise_note,
    exercise_report,
    grade_practice_exercise,
    get_assistant_thread,
    get_campaign,
    get_document,
    get_exercise,
    get_managed_user,
    get_email_settings,
    get_publication_settings,
    get_public_summary,
    get_pattern_concern,
    get_prompt_use,
    get_shared_pattern,
    get_system_problem,
    get_summary_row,
    is_favorite,
    label_for,
    list_assistant_threads,
    list_audit,
    list_campaigns,
    list_deliveries,
    list_documents,
    list_pattern_tools,
    list_tool_sections,
    list_exercises,
    is_saved,
    list_dashboard_favorites,
    list_ingestion_jobs,
    list_outbox,
    list_recent_searches,
    list_public_summaries,
    record_search,
    relate_problem_to_pattern,
    list_pattern_concerns,
    list_exercise_catalog,
    list_pattern_exercises,
    list_pattern_sources,
    list_prompt_uses,
    list_pattern_groups,
    list_pattern_categories,
    list_related_patterns,
    list_related_problems,
    list_system_problems,
    list_sent_emails,
    list_shared_patterns,
    list_started_exercises,
    list_subscribers,
    list_summaries,
    list_users,
    login_user,
    logout_session,
    practice_view,
    preview_campaign,
    process_ingestion_jobs,
    pattern_has_concerns,
    published_version,
    render_concern,
    retry_delivery,
    search_documents,
    save_exercise_note,
    start_exercise,
    saved_ids,
    toggle_favorite,
    toggle_saved,
    send_campaign,
    set_document_publication,
    submit_summary,
    subscribe_address,
    suppress_subscriber,
    unpublish_summary,
    unsubscribe_address,
    update_document,
    update_email_settings,
    update_publication_settings,
    update_summary,
    update_user,
    document_sources,
    campaign_items,
    category_by_slug,
)
from app.errors import AppError, BadRequest, Forbidden, NotFound
from app.markdown import render_markdown
from app.models import User
from app.permissions import permission_map
from app.prompts import list_prompts, prompt_by_slug
from app.security import client_ip

router = APIRouter()
env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=select_autoescape(["html"]),
)
env.filters["md"] = render_markdown
env.filters["label"] = label_for

NOTICES = {
    "saved": "Saved.",
    "published": "Published for signed-in readers.",
    "unpublished": "Returned to draft.",
    "summary": "Public summary draft created. It is not public until a super admin approves it.",
    "submitted": "Submitted for review.",
    "approved": "Approved for public sharing.",
    "withdrawn": "Withdrawn from public sharing.",
    "queued": "The email was queued.",
    "settings": "Settings saved.",
    "user": "User saved.",
    "subscribed": "Check your inbox for a confirmation link.",
    "confirmed": "Your subscription is confirmed.",
    "unsubscribed": "You are unsubscribed.",
}


def render_page(name: str, status_code: int = 200, **context) -> HTMLResponse:
    html = env.get_template(name).render(**context)
    return HTMLResponse(html, status_code=status_code)


def shell(request: Request, db: Session, user, **extra):
    publication = get_publication_settings(db)
    session_row = getattr(request.state, "session", None)
    context = {
        "user": user,
        "csrf": "" if session_row is None else session_row.csrf_token,
        "organization": publication.organization_name,
        "notice": NOTICES.get(request.query_params.get("notice", ""), ""),
        "error": "",
        "can": {} if user is None else permission_map(user),
        "public_page": False,
        "path": request.url.path,
        "categories": list_pattern_categories(db),
    }
    context.update(extra)
    return context


def viewer_context(db: Session, user: User, summary):
    document = get_document(db, user, summary.document_id)
    version = published_version(db, summary)
    approver = db.get(User, summary.approved_by) if summary.approved_by else None
    return {
        "summary": summary,
        "document": document,
        "sources": document_sources(db, document.id),
        "snapshot": version,
        "approver": None if approver is None else approver.email,
    }


@router.get("/login")
def login_page(request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return render_page("login.html", **shell(request, db, None))


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        _user, raw, _csrf = login_user(
            db,
            request.app.state.settings,
            request.app.state.limiter,
            client_ip(request),
            str(form.get("email") or ""),
            str(form.get("password") or ""),
        )
    except AppError as exc:
        return render_page("login.html", status_code=exc.status_code, **shell(request, db, None, error=exc.detail))
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, request, raw)
    return response


@router.post("/logout")
def logout(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    logout_session(db, request.state.session)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("adp_session", path="/")
    return response


@router.get("/forgot-password")
def forgot_page(request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    return render_page("forgot.html", **shell(request, db, user))


@router.post("/forgot-password")
async def forgot_submit(request: Request, db: Session = Depends(get_db)):
    from app.domain import request_password_reset

    form = await request.form()
    request_password_reset(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        request.app.state.limiter,
        client_ip(request),
        str(form.get("email") or ""),
    )
    return render_page(
        "forgot.html",
        **shell(request, db, None, notice="If an account exists, a reset link has been sent."),
    )


@router.get("/reset-password/{token}")
def reset_page(token: str, request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    return render_page("reset.html", **shell(request, db, user, token=token))


@router.post("/reset-password/{token}")
async def reset_submit(token: str, request: Request, db: Session = Depends(get_db)):
    from app.domain import reset_password

    form = await request.form()
    try:
        reset_password(db, token, str(form.get("password") or ""))
    except AppError as exc:
        return render_page("reset.html", status_code=exc.status_code, **shell(request, db, None, token=token, error=exc.detail))
    return RedirectResponse("/login?notice=saved", status_code=303)


@router.get("/")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    pending = []
    outdated = []
    if permission_map(user)["edit"]:
        pending = list_summaries(db, user, "ready_for_review")
        outdated = [row for row in list_summaries(db, user) if row.outdated]
    return render_page(
        "dashboard.html",
        **shell(
            request,
            db,
            user,
            pending=pending,
            outdated=outdated,
            searches=list_recent_searches(db, user),
            favorites=list_dashboard_favorites(db, user),
            sent_emails=list_sent_emails(db, user),
            started_exercises=list_started_exercises(db, user),
            prompts=list_prompts(),
            threads=list_assistant_threads(db, user, 20),
        ),
    )


@router.post("/documents")
async def create_doc(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    document = create_document(
        db,
        user,
        str(form.get("title") or ""),
        str(form.get("body") or ""),
        False,
        str(form.get("category") or ""),
    )
    return RedirectResponse(f"/documents/{document.id}?notice=saved", status_code=303)


@router.get("/documents/{document_id}")
def document_page(document_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    document = get_document(db, user, document_id)
    summaries = []
    if permission_map(user)["edit"]:
        from app.domain import list_document_summaries

        summaries = list_document_summaries(db, user, document.id)
    return render_page(
        "document.html",
        **shell(
            request,
            db,
            user,
            document=document,
            sources=document_sources(db, document.id),
            summaries=summaries,
            favorite=is_favorite(db, user, document.id),
            category=category_by_slug(db, document.category or "architectural-patterns"),
        ),
    )


@router.post("/documents/{document_id}/favorite")
def favorite_document(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    toggle_favorite(db, user, document_id)
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@router.post("/documents/{document_id}/save")
async def save_document(document_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    update_document(
        db,
        user,
        document_id,
        str(form.get("title") or ""),
        str(form.get("body") or ""),
        str(form.get("category") or ""),
    )
    return RedirectResponse(f"/documents/{document_id}?notice=saved", status_code=303)


@router.post("/documents/{document_id}/publish")
def publish_doc(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    set_document_publication(db, user, document_id, True)
    return RedirectResponse(f"/documents/{document_id}?notice=published", status_code=303)


@router.post("/documents/{document_id}/unpublish")
def unpublish_doc(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    set_document_publication(db, user, document_id, False)
    return RedirectResponse(f"/documents/{document_id}?notice=unpublished", status_code=303)


@router.post("/documents/{document_id}/sources")
async def create_source(document_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    add_source(db, user, document_id, str(form.get("label") or ""), str(form.get("url") or ""))
    return RedirectResponse(f"/documents/{document_id}?notice=saved", status_code=303)


@router.post("/documents/{document_id}/summaries/manual")
def manual_summary(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = create_manual_summary(db, user, document_id, "", "", None)
    return RedirectResponse(f"/summaries/{summary.id}?notice=summary", status_code=303)


@router.post("/documents/{document_id}/summaries/generate")
def generated_summary(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = generate_summary_draft(db, user, document_id)
    return RedirectResponse(f"/summaries/{summary.id}?notice=summary", status_code=303)


@router.get("/summaries/{summary_id}")
def summary_page(summary_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = get_summary_row(db, summary_id)
    if not permission_map(user)["edit"]:
        from app.errors import Forbidden

        raise Forbidden()
    return render_page("summary.html", **shell(request, db, user, **viewer_context(db, user, summary)))


@router.post("/summaries/{summary_id}/save")
async def save_summary(summary_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    asset = str(form.get("public_asset_url") or "")
    update_summary(db, user, summary_id, str(form.get("title") or ""), str(form.get("body") or ""), asset, True)
    return RedirectResponse(f"/summaries/{summary_id}?notice=saved", status_code=303)


@router.post("/summaries/{summary_id}/submit")
def submit_summary_page(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    submit_summary(db, user, summary_id)
    return RedirectResponse(f"/summaries/{summary_id}?notice=submitted", status_code=303)


@router.post("/summaries/{summary_id}/approve")
def approve_summary_page(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    approve_summary(db, user, summary_id)
    return RedirectResponse(f"/summaries/{summary_id}?notice=approved", status_code=303)


@router.post("/summaries/{summary_id}/unpublish")
def unpublish_summary_page(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    unpublish_summary(db, user, summary_id)
    return RedirectResponse(f"/summaries/{summary_id}?notice=withdrawn", status_code=303)


@router.get("/approvals")
def approvals(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = list_summaries(db, user, "ready_for_review")
    return render_page("approvals.html", **shell(request, db, user, summaries=rows))


@router.get("/patterns")
def patterns_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("patterns.html", **shell(request, db, user))


@router.get("/patterns/{slug}")
def pattern_category(slug: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    category = category_by_slug(db, slug)
    return render_page(
        "pattern_category.html",
        **shell(
            request,
            db,
            user,
            category=category,
            patterns=list_shared_patterns(db, category["slug"]),
            documents=list_documents(db, user, category["slug"]),
        ),
    )


@router.get("/patterns/{slug}/{pattern_id}")
def shared_pattern_page(
    slug: str,
    pattern_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    category = category_by_slug(db, slug)
    pattern = get_shared_pattern(db, pattern_id, category["slug"])
    return render_page(
        "pattern.html",
        **shell(
            request,
            db,
            user,
            category=category,
            pattern=pattern,
            tool_sections=list_tool_sections(db),
            tools_by_section=list_pattern_tools(db, pattern.id),
            related_problems=list_related_problems(db, pattern.id),
            pattern_exercises=list_pattern_exercises(db, pattern.id),
            show_concerns=pattern_has_concerns(pattern),
            design_concerns=list_pattern_concerns(db, pattern.id),
            pattern_sources=list_pattern_sources(db, pattern.id),
            favorite=is_saved(db, user, "pattern", pattern.id),
        ),
    )


@router.post("/patterns/{slug}/{pattern_id}/sources")
async def pattern_source_add(
    slug: str,
    pattern_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    pattern = get_shared_pattern(db, pattern_id, category_by_slug(db, slug)["slug"])
    form = await request.form()
    add_pattern_source(db, user, pattern, str(form.get("label") or ""), str(form.get("url") or ""))
    return RedirectResponse(f"/patterns/{pattern.category_slug}/{pattern.id}#pattern-sources", status_code=303)


@router.post("/patterns/{slug}/{pattern_id}/sources/{source_id}/delete")
def pattern_source_delete(
    slug: str,
    pattern_id: int,
    source_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    pattern = get_shared_pattern(db, pattern_id, category_by_slug(db, slug)["slug"])
    delete_pattern_source(db, user, pattern, source_id)
    return RedirectResponse(f"/patterns/{pattern.category_slug}/{pattern.id}#pattern-sources", status_code=303)


@router.post("/patterns/{slug}/{pattern_id}/favorite")
def favorite_pattern(slug: str, pattern_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    pattern = get_shared_pattern(db, pattern_id, category_by_slug(db, slug)["slug"])
    toggle_saved(db, user, "pattern", pattern.id)
    return RedirectResponse(f"/patterns/{pattern.category_slug}/{pattern.id}", status_code=303)


@router.get("/patterns/{slug}/{pattern_id}/concerns/{concern_id}")
def pattern_concern_page(
    slug: str,
    pattern_id: int,
    concern_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    category = category_by_slug(db, slug)
    pattern = get_shared_pattern(db, pattern_id, category["slug"])
    if not pattern_has_concerns(pattern):
        raise NotFound()
    concern = get_pattern_concern(db, pattern.id, concern_id)
    return render_page(
        "pattern_concern.html",
        **shell(
            request,
            db,
            user,
            category=category,
            pattern=pattern,
            concern=concern,
            concern_html=render_concern(db, concern),
        ),
    )


@router.post("/patterns/{slug}/{pattern_id}/tools")
async def add_pattern_tool(
    slug: str,
    pattern_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    category = category_by_slug(db, slug)
    pattern = get_shared_pattern(db, pattern_id, category["slug"])
    form = await request.form()
    create_learning_tool(
        db,
        user,
        pattern,
        str(form.get("section") or ""),
        str(form.get("title") or ""),
        str(form.get("summary") or ""),
    )
    return RedirectResponse(f"/patterns/{slug}/{pattern_id}", status_code=303)


@router.get("/problems")
def problems_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page(
        "problems.html",
        **shell(request, db, user, problems=list_system_problems(db)),
    )


@router.get("/problems/{problem_id}")
def problem_page(
    problem_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    problem = get_system_problem(db, problem_id)
    sheet_title, a3_boxes = a3_for_problem(db, problem)
    return render_page(
        "problem.html",
        **shell(
            request,
            db,
            user,
            problem=problem,
            sheet_title=sheet_title,
            a3_boxes=a3_boxes,
            related_patterns=list_related_patterns(db, problem.id),
            pattern_groups=list_pattern_groups(db) if permission_map(user)["edit"] else [],
            favorite=is_saved(db, user, "problem", problem.id),
        ),
    )


@router.post("/problems/{problem_id}/favorite")
def favorite_problem(problem_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    problem = get_system_problem(db, problem_id)
    toggle_saved(db, user, "problem", problem.id)
    return RedirectResponse(f"/problems/{problem.id}", status_code=303)


@router.post("/problems/{problem_id}/patterns")
async def relate_problem_page(
    problem_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    problem = get_system_problem(db, problem_id)
    form = await request.form()
    raw = str(form.get("pattern_id") or "").strip()
    if not raw.isdigit():
        raise BadRequest("Choose a pattern.")
    relate_problem_to_pattern(db, user, problem, int(raw))
    return RedirectResponse(f"/problems/{problem.id}", status_code=303)


@router.get("/exercises")
def exercises_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page(
        "exercises.html",
        **shell(request, db, user, exercises=list_exercise_catalog(db, user)),
    )


@router.post("/exercises")
async def exercises_create(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    exercise = create_exercise(db, user, str(form.get("title") or ""), str(form.get("prompt") or ""))
    return RedirectResponse(f"/exercises/{exercise.id}", status_code=303)


@router.get("/exercises/{exercise_id}")
def exercise_page(exercise_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exercise = get_exercise(db, exercise_id)
    attempt = next((row for item, row in list_exercises(db, user) if item.id == exercise.id), None)
    return render_page(
        "exercise.html",
        **shell(
            request,
            db,
            user,
            exercise=exercise,
            attempt=attempt,
            practice=practice_view(exercise),
            diagram=diagram_view(exercise),
            favorite=is_saved(db, user, "exercise", exercise.id),
            note=exercise_note(db, user, exercise.id),
            report=exercise_report(db, user, exercise),
        ),
    )


@router.post("/exercises/{exercise_id}/favorite")
def favorite_exercise(exercise_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exercise = get_exercise(db, exercise_id)
    toggle_saved(db, user, "exercise", exercise.id)
    return RedirectResponse(f"/exercises/{exercise.id}", status_code=303)


@router.post("/exercises/{exercise_id}/check")
async def exercise_check(exercise_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exercise = get_exercise(db, exercise_id)
    payload = await request.json()
    result = grade_practice_exercise(
        db,
        user,
        exercise,
        payload.get("links") if isinstance(payload, dict) else None,
        payload.get("answers") if isinstance(payload, dict) else None,
    )
    return JSONResponse(result)


@router.post("/exercises/{exercise_id}/notes")
async def exercise_notes(exercise_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exercise = get_exercise(db, exercise_id)
    form = await request.form()
    save_exercise_note(db, user, exercise, str(form.get("body") or ""))
    return RedirectResponse(f"/exercises/{exercise.id}#exercise-notes", status_code=303)


@router.get("/exercises/{exercise_id}/result.pdf")
def exercise_result_pdf(exercise_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exercise = get_exercise(db, exercise_id)
    from app.result_pdf import render_result_pdf

    report = exercise_report(db, user, exercise)
    payload = render_result_pdf(report)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="exercise-{exercise.id}-result.pdf"'},
    )


@router.post("/exercises/{exercise_id}/start")
def exercise_start(exercise_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    start_exercise(db, user, exercise_id)
    return RedirectResponse(f"/exercises/{exercise_id}", status_code=303)


@router.get("/prompts")
def prompts_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page(
        "prompts.html",
        **shell(request, db, user, prompts=list_prompts(), uses=list_prompt_uses(db, user)),
    )


@router.get("/prompts/runs/{use_id}")
def prompt_run_page(use_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    use, pattern = get_prompt_use(db, user, use_id)
    spec = prompt_by_slug(use.slug)
    if spec is None:
        raise NotFound()
    return render_page(
        "prompt_run.html",
        **shell(request, db, user, use=use, pattern=pattern, prompt=spec),
    )


@router.get("/prompts/{slug}")
def prompt_page(slug: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    spec = prompt_by_slug(slug)
    if spec is None:
        raise NotFound()
    categories = list_pattern_categories(db)
    selected = request.query_params.get("category") or ""
    if not any(item["slug"] == selected for item in categories):
        selected = categories[0]["slug"] if categories else ""
    return render_page(
        "prompt.html",
        **shell(
            request,
            db,
            user,
            prompt=spec,
            selected=selected,
            patterns=list_shared_patterns(db, selected) if selected else [],
            uses=list_prompt_uses(db, user, slug),
        ),
    )


@router.post("/prompts/{slug}")
async def prompt_build(slug: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    raw = str(form.get("pattern_id") or "").strip()
    if not raw.isdigit():
        raise BadRequest("Choose a pattern.")
    use = build_prompt_use(db, user, slug, int(raw), str(form.get("focus") or ""), str(form.get("code") or ""))
    return RedirectResponse(f"/prompts/runs/{use.id}", status_code=303)


@router.get("/ask")
def ask_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page(
        "ask.html",
        **shell(request, db, user, threads=list_assistant_threads(db, user, 20)),
    )


@router.post("/ask")
async def ask_create(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    thread = ask_assistant(db, user, str(form.get("question") or ""))
    return RedirectResponse(f"/ask/{thread.id}", status_code=303)


@router.get("/ask/{thread_id}")
def ask_thread(thread_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    thread, messages = get_assistant_thread(db, user, thread_id)
    return render_page(
        "ask_thread.html",
        **shell(
            request,
            db,
            user,
            thread=thread,
            messages=messages,
            favorite_answers=saved_ids(db, user, "answer"),
        ),
    )


@router.post("/ask/{thread_id}/messages/{message_id}/favorite")
def favorite_answer(
    thread_id: int,
    message_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    thread, messages = get_assistant_thread(db, user, thread_id)
    message = next((item for item in messages if item.id == message_id and item.role == "assistant"), None)
    if message is None:
        raise NotFound()
    toggle_saved(db, user, "answer", message.id)
    return RedirectResponse(f"/ask/{thread.id}#message-{message.id}", status_code=303)


@router.post("/ask/{thread_id}")
async def ask_follow_up(thread_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    thread = ask_assistant(db, user, str(form.get("question") or ""), thread_id)
    return RedirectResponse(f"/ask/{thread.id}", status_code=303)


@router.get("/search")
def search_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    query = request.query_params.get("q", "")
    if query.strip():
        record_search(db, user, query)
    results = search_documents(db, user, query) if query.strip() else []
    return render_page("search.html", **shell(request, db, user, query=query, results=results))


@router.get("/users")
def users_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("users.html", **shell(request, db, user, users=list_users(db, user)))


@router.post("/users")
async def users_create(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    create_user(
        db,
        user,
        str(form.get("email") or ""),
        str(form.get("password") or ""),
        str(form.get("role") or ""),
        form.get("can_send_public_email") == "yes",
    )
    return RedirectResponse("/users?notice=user", status_code=303)


@router.get("/users/{user_id}")
def user_profile(user_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    subject = get_managed_user(db, user, user_id)
    return render_page(
        "user.html",
        **shell(
            request,
            db,
            user,
            subject=subject,
            activity=customer_dashboard(db, user, subject),
        ),
    )


@router.get("/users/{user_id}/threads/{thread_id}")
def user_thread(
    user_id: int,
    thread_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    subject, thread, messages = customer_thread(db, user, user_id, thread_id)
    return render_page(
        "user_thread.html",
        **shell(request, db, user, subject=subject, thread=thread, messages=messages),
    )


@router.get("/users/{user_id}/prompts/{use_id}")
def user_prompt(
    user_id: int,
    use_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    subject, use, pattern, prompt = customer_prompt(db, user, user_id, use_id)
    return render_page(
        "user_prompt.html",
        **shell(request, db, user, subject=subject, use=use, pattern=pattern, prompt=prompt),
    )


@router.post("/users/{user_id}")
async def users_update(user_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    changes = {
        "role": str(form.get("role") or ""),
        "is_active": form.get("is_active") == "yes",
        "can_send_public_email": form.get("can_send_public_email") == "yes",
    }
    password = str(form.get("password") or "")
    if password:
        changes["password"] = password
    update_user(db, user, user_id, changes)
    return RedirectResponse(f"/users/{user_id}?notice=user", status_code=303)


@router.get("/campaigns")
def campaigns_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("campaigns.html", **shell(request, db, user, campaigns=list_campaigns(db, user)))


@router.get("/campaigns/new")
def campaign_new(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    approved = [row for row in list_summaries(db, user) if row.publicly_listed and row.published_version_id]
    weekly = request.query_params.get("weekly") == "1"
    return render_page("campaign_new.html", **shell(request, db, user, summaries=approved, weekly=weekly))


@router.post("/campaigns")
async def campaign_create(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    summary_ids = [int(value) for value in form.getlist("summary_ids")]
    recipients = [line.strip() for line in str(form.get("recipients") or "").splitlines() if line.strip()]
    audience = "all_subscribed" if form.get("audience") == "all_subscribed" else None
    campaign = create_campaign(
        db,
        user,
        str(form.get("subject") or ""),
        str(form.get("email_type") or ""),
        summary_ids,
        recipients,
        audience,
    )
    return RedirectResponse(f"/campaigns/{campaign.id}", status_code=303)


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    campaign = get_campaign(db, user, campaign_id)
    deliveries = list_deliveries(db, user, campaign.id) if campaign.status != "draft" else []
    try:
        items = campaign_items(db, campaign)
    except AppError:
        items = []
    return render_page(
        "campaign_detail.html",
        **shell(request, db, user, campaign=campaign, deliveries=deliveries, items=items, preview=None),
    )


@router.post("/campaigns/{campaign_id}/preview")
def campaign_preview(campaign_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    preview = preview_campaign(db, user, campaign_id)
    campaign = get_campaign(db, user, campaign_id)
    return render_page(
        "campaign_detail.html",
        **shell(
            request,
            db,
            user,
            campaign=campaign,
            deliveries=[],
            items=campaign_items(db, campaign),
            preview=preview,
        ),
    )


@router.post("/campaigns/{campaign_id}/send")
async def campaign_send(campaign_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    send_campaign(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        user,
        campaign_id,
        form.get("confirm") == "yes",
    )
    return RedirectResponse(f"/campaigns/{campaign_id}?notice=queued", status_code=303)


@router.post("/campaigns/{campaign_id}/deliveries/{delivery_id}/retry")
def campaign_retry(campaign_id: int, delivery_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    retry_delivery(db, request.app.state.settings, request.app.state.mail_state, user, campaign_id, delivery_id)
    return RedirectResponse(f"/campaigns/{campaign_id}?notice=queued", status_code=303)


@router.get("/subscribers")
def subscribers_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("subscribers.html", **shell(request, db, user, subscribers=list_subscribers(db, user)))


@router.post("/subscribers/{subscriber_id}/suppress")
async def suppress(subscriber_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    suppress_subscriber(db, user, subscriber_id, str(form.get("reason") or ""))
    return RedirectResponse("/subscribers?notice=saved", status_code=303)


def _require_admin(user: User) -> None:
    if not permission_map(user)["admin"]:
        raise Forbidden()


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _require_admin(user)
    return render_page(
        "settings.html",
        **shell(
            request,
            db,
            user,
            email_settings=get_email_settings(db),
            publication=get_publication_settings(db),
        ),
    )


@router.get("/settings/publication")
def settings_publication_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _require_admin(user)
    return render_page(
        "settings_publication.html",
        **shell(request, db, user, publication=get_publication_settings(db)),
    )


@router.get("/settings/email")
def settings_email_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _require_admin(user)
    return render_page(
        "settings_email.html",
        **shell(request, db, user, email_settings=get_email_settings(db)),
    )


@router.get("/settings/service")
def settings_service_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _require_admin(user)
    return render_page(
        "settings_service.html",
        **shell(request, db, user, email_settings=get_email_settings(db)),
    )


@router.post("/settings/email")
async def settings_email(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    update_email_settings(
        db,
        user,
        {
            "enabled": form.get("enabled") == "yes",
            "sender_email": str(form.get("sender_email") or ""),
            "sender_name": str(form.get("sender_name") or ""),
            "public_base_url": str(form.get("public_base_url") or ""),
        },
    )
    return RedirectResponse("/settings/email?notice=settings", status_code=303)


@router.post("/settings/service")
async def settings_service(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    update_email_settings(
        db,
        user,
        {
            "provider": str(form.get("provider") or "local"),
            "smtp_host": str(form.get("smtp_host") or ""),
            "smtp_port": int(str(form.get("smtp_port") or "587")),
            "smtp_username": str(form.get("smtp_username") or ""),
            "smtp_password": str(form.get("smtp_password") or ""),
            "smtp_use_tls": form.get("smtp_use_tls") == "yes",
        },
    )
    return RedirectResponse("/settings/service?notice=settings", status_code=303)


@router.post("/settings/publication")
async def settings_publication(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    update_publication_settings(
        db,
        user,
        form.get("public_site_enabled") == "yes",
        str(form.get("organization_name") or ""),
    )
    return RedirectResponse("/settings/publication?notice=settings", status_code=303)


@router.get("/sources")
def sources_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page(
        "sources.html",
        **shell(request, db, user, jobs=list_ingestion_jobs(db, user)),
    )


@router.post("/sources")
async def sources_ingest(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    create_ingestion_job(
        db,
        user,
        str(form.get("title") or ""),
        str(form.get("body") or ""),
        str(form.get("source_label") or ""),
        str(form.get("source_url") or ""),
        form.get("publish") == "yes",
    )
    process_ingestion_jobs(db)
    return RedirectResponse("/sources?notice=saved", status_code=303)


@router.post("/sources/{source_id}/delete")
def sources_delete(source_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    delete_source(db, user, source_id)
    return RedirectResponse("/sources?notice=saved", status_code=303)


@router.get("/audit")
def audit_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("audit.html", **shell(request, db, user, events=list_audit(db, user)))


@router.get("/outbox")
def outbox_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render_page("outbox.html", **shell(request, db, user, messages=list_outbox(db, user)))


@router.get("/public")
def public_index(request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    try:
        summaries = list_public_summaries(db)
    except AppError as exc:
        return render_page("error.html", status_code=exc.status_code, **shell(request, db, user, message=exc.detail, public_page=True))
    return render_page("public_index.html", **shell(request, db, user, summaries=summaries, public_page=True))


@router.get("/public/summaries/{slug}")
def public_summary_page(slug: str, request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    summary, version = get_public_summary(db, slug)
    return render_page(
        "public_summary.html",
        **shell(request, db, user, summary=summary, version=version, public_page=True),
    )


@router.get("/subscribe")
def subscribe_page(request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    return render_page("subscribe.html", **shell(request, db, user, public_page=True))


@router.post("/subscribe")
async def subscribe_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        subscribe_address(
            db,
            request.app.state.settings,
            request.app.state.mail_state,
            request.app.state.limiter,
            client_ip(request),
            str(form.get("email") or ""),
            form.get("consent") == "yes",
            "public_form",
        )
    except AppError as exc:
        return render_page(
            "subscribe.html",
            status_code=exc.status_code,
            **shell(request, db, None, error=exc.detail, public_page=True),
        )
    return render_page("subscribe.html", **shell(request, db, None, notice=NOTICES["subscribed"], public_page=True))


@router.get("/confirm/{token}")
def confirm_page(token: str, request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    return render_page("confirm.html", **shell(request, db, user, token=token, public_page=True))


@router.post("/confirm/{token}")
def confirm_submit(token: str, request: Request, db: Session = Depends(get_db)):
    request.app.state.limiter.hit(
        f"confirm:{client_ip(request)}",
        request.app.state.settings.public_limit,
        request.app.state.settings.public_window_seconds,
    )
    try:
        confirm_subscription(db, token)
    except AppError as exc:
        return render_page(
            "confirm.html",
            status_code=exc.status_code,
            **shell(request, db, None, token=token, error=exc.detail, public_page=True),
        )
    return render_page("confirm.html", **shell(request, db, None, token=token, notice=NOTICES["confirmed"], public_page=True))


@router.get("/unsubscribe/{token}")
def unsubscribe_page(token: str, request: Request, user=Depends(optional_user), db: Session = Depends(get_db)):
    return render_page("unsubscribe.html", **shell(request, db, user, token=token, public_page=True))


@router.post("/unsubscribe/{token}")
def unsubscribe_submit(token: str, request: Request, db: Session = Depends(get_db)):
    request.app.state.limiter.hit(
        f"unsubscribe:{client_ip(request)}",
        request.app.state.settings.public_limit,
        request.app.state.settings.public_window_seconds,
    )
    try:
        unsubscribe_address(db, token)
    except AppError as exc:
        return render_page(
            "unsubscribe.html",
            status_code=exc.status_code,
            **shell(request, db, None, token=token, error=exc.detail, public_page=True),
        )
    return render_page(
        "unsubscribe.html",
        **shell(request, db, None, token=token, notice=NOTICES["unsubscribed"], public_page=True),
    )

# Mingkong Video Card Inline Play Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a centered play button to Mingkong material-card covers so operators can load and play a video inside the current card with one click.

**Architecture:** Keep all behavior in the existing `/xuanpin/mk` template because the material library, yesterday Top100, and Mingkong detail modal already share the same card preview markup and `activateMkVideoTab` helper. Add a small delegated-click handler and reuse the existing lazy `data-mk-video-src` flow so MP4 files are not downloaded until the operator clicks play or the video tab.

**Tech Stack:** Python 3.12, Flask/Jinja template, browser DOM APIs, native HTML video, pytest template/route assertions.

---

## File Structure

- Modify `tests/test_mk_selection_routes.py`: add template assertions that lock the new play-button markup, delegated click hook, lazy source reuse, and autoplay rejection handling.
- Modify `web/templates/mk_selection.html`: add CSS, render play buttons in both card renderers, and extend the existing video-tab helper to optionally call `video.play()`.
- Reference `docs/superpowers/specs/2026-05-18-mingkong-video-card-inline-play-design.md`: implementation anchor.

## Task 1: Lock Expected Template Behavior

**Files:**
- Modify: `tests/test_mk_selection_routes.py`
- Reference: `docs/superpowers/specs/2026-05-18-mingkong-video-card-inline-play-design.md`

- [ ] **Step 1: Write the failing test**

Add these assertions to `test_mk_selection_video_cards_include_local_video_preview()`:

```python
    assert "mk-video-play-btn" in template
    assert "data-mk-video-play" in template
    assert "function playMkVideoFromButton(button)" in template
    assert "activateMkVideoTab(videoTab, {play: true})" in template
    assert "const playResult = video.play();" in template
    assert "playResult.catch(() => {})" in template
    assert "e.stopPropagation();" in template
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_video_cards_include_local_video_preview -q
```

Expected: FAIL because `mk-video-play-btn` or `playMkVideoFromButton` is not yet in `mk_selection.html`.

## Task 2: Add Play Button UI And One-Click Playback

**Files:**
- Modify: `web/templates/mk_selection.html`
- Test: `tests/test_mk_selection_routes.py::test_mk_selection_video_cards_include_local_video_preview`

- [ ] **Step 1: Add cover-button CSS**

In the existing `<style>` block near `.mk-video-cover-frame img`, add. Do not set
`.mk-video-cover-frame { position:relative; }`, because `.mk-video-pane` already
positions the pane and must keep `position:absolute`.

```css
.mk-video-play-btn { position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); width:56px; height:56px; border-radius:9999px; border:1px solid rgba(255,255,255,.88); background:rgba(15,23,42,.68); color:#fff; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:0 10px 22px rgba(15,23,42,.28); transition:background .15s ease, transform .15s ease, box-shadow .15s ease; }
.mk-video-play-btn::before { content:""; display:block; margin-left:4px; border-top:11px solid transparent; border-bottom:11px solid transparent; border-left:17px solid currentColor; }
.mk-video-play-btn:hover { background:rgba(15,23,42,.82); transform:translate(-50%, -50%) scale(1.04); box-shadow:0 12px 26px rgba(15,23,42,.34); }
.mk-video-play-btn:focus-visible { outline:3px solid rgba(14,165,233,.55); outline-offset:3px; }
```

- [ ] **Step 2: Render the play button in archived card markup**

Inside `renderMkVideoMaterialCard(r)`, after `posterAttr`, add:

```javascript
  const playButtonHtml = videoUrl
    ? '<button type="button" class="mk-video-play-btn" data-mk-video-play aria-label="播放视频" title="播放视频"></button>'
    : '';
```

Then change the cover pane from:

```javascript
      <div class="mk-video-pane mk-video-cover-frame active" data-pane="cover">${coverUrl ? `<img src="${safeCoverUrl}" loading="lazy" decoding="async" alt="${videoName}">` : `<span class="mk-video-media-empty">无封面</span>`}</div>
```

to:

```javascript
      <div class="mk-video-pane mk-video-cover-frame active" data-pane="cover">${coverUrl ? `<img src="${safeCoverUrl}" loading="lazy" decoding="async" alt="${videoName}">` : `<span class="mk-video-media-empty">无封面</span>`}${playButtonHtml}</div>
```

- [ ] **Step 3: Render the play button in Mingkong detail modal card markup**

Inside the `videos.forEach(v => { ... })` renderer in `openDetail(id)`, after `posterAttr`, add the same `playButtonHtml` constant:

```javascript
      const playButtonHtml = videoUrl
        ? '<button type="button" class="mk-video-play-btn" data-mk-video-play aria-label="播放视频" title="播放视频"></button>'
        : '';
```

Then apply the same cover-pane change in that renderer:

```javascript
          <div class="mk-video-pane mk-video-cover-frame active" data-pane="cover">${coverUrl ? `<img src="${safeCoverUrl}" loading="lazy" decoding="async" alt="${videoName}">` : `<span class="mk-video-media-empty">无封面</span>`}${playButtonHtml}</div>
```

- [ ] **Step 4: Extend tab activation to optionally play**

Change:

```javascript
function activateMkVideoTab(tab) {
```

to:

```javascript
function activateMkVideoTab(tab, options = {}) {
```

Inside the existing `if (target === 'video') { ... }` block, after lazy `src` assignment, add:

```javascript
    if (options.play && video && typeof video.play === 'function') {
      const playResult = video.play();
      if (playResult && typeof playResult.catch === 'function') {
        playResult.catch(() => {});
      }
    }
```

- [ ] **Step 5: Add the play-button helper**

After `activateMkVideoTab`, add:

```javascript
function playMkVideoFromButton(button) {
  const card = button?.closest('.mk-video-card');
  if (!card) return;
  const videoTab = card.querySelector('.mk-video-tab[data-tab="video"]');
  if (!videoTab || videoTab.disabled) return;
  activateMkVideoTab(videoTab, {play: true});
}
```

- [ ] **Step 6: Wire delegated clicks before other card click handling**

At the top of the existing `document.addEventListener('click', e => { ... })` callback, add:

```javascript
  const playButton = e.target.closest('[data-mk-video-play]');
  if (playButton) {
    e.preventDefault();
    e.stopPropagation();
    playMkVideoFromButton(playButton);
    return;
  }
```

- [ ] **Step 7: Run the focused test**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_video_cards_include_local_video_preview -q
```

Expected: PASS.

## Task 3: Focused Regression Verification

**Files:**
- Test: `tests/test_mk_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`
- Verify: `web/templates/mk_selection.html`

- [ ] **Step 1: Run focused template and route tests**

Run:

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect the final diff**

Run:

```bash
git diff -- web/templates/mk_selection.html tests/test_mk_selection_routes.py docs/superpowers/plans/2026-05-18-mingkong-video-card-inline-play.md
```

Expected: diff only includes the plan, template assertions, play-button CSS/markup, and the delegated playback helper.

- [ ] **Step 3: Run route smoke checks if the app imports cleanly**

Run:

```bash
python3 - <<'PY'
import os
os.environ.setdefault("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
os.environ.setdefault("AUTOVIDEOSRT_DISABLE_BACKGROUND_THREADS", "1")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
os.environ.setdefault("WTF_CSRF_ENABLED", "0")
os.environ.setdefault("TOS_ACCESS_KEY", "test-tos-ak")
os.environ.setdefault("TOS_SECRET_KEY", "test-tos-sk")
import web.app as web_app
web_app._run_startup_recovery = lambda: None
web_app.recover_all_interrupted_tasks = lambda: None
web_app.mark_interrupted_bulk_translate_tasks = lambda: None
web_app._seed_default_prompts = lambda: None
app = web_app.create_app()
client = app.test_client()
resp = client.get('/xuanpin/mk')
print(resp.status_code)
PY
```

Expected: `302`, because unauthenticated users are redirected before the template renders.

## Self-Review

- Spec coverage: Tasks 1 and 2 cover center play button, both material-card renderers, lazy `data-mk-video-src`, `video.play()`, autoplay rejection handling, no backend/API changes, and no eager MP4 preload.
- Placeholder scan: no unfinished-marker text or open-ended "add tests" step.
- Type consistency: the plan consistently uses `playMkVideoFromButton(button)`, `[data-mk-video-play]`, `.mk-video-play-btn`, `activateMkVideoTab(tab, options = {})`, and `{play: true}`.

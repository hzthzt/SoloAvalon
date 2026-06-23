# Play Layout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将对局页改为“准备态保留开局表单、游玩态隐藏开局表单”的专注布局，并为信息流和本轮总结提供独立滚动空间。

**Architecture:** `frontend/src/App.tsx` 继续负责页面状态选择；准备态使用现有三栏 `game-layout`，创建中和游玩态使用新的 `play-focus-layout`。CSS Grid 负责两栏/单列响应式布局，现有 `GameDesk`、行动按钮、事件流和后端 API 契约保持不变。

**Tech Stack:** React 18、TypeScript、Vite、Node test、CSS Grid。

---

## File Structure

- Modify: `frontend/src/App.test.ts`
  - 增加源码断言，先锁定三种渲染状态、专注布局类名、创建中状态面板、信息流高度和总结滚动。
- Modify: `frontend/src/App.tsx`
  - 新增 `showSetupState`、`showStartingState`、`showPlayState`。
  - 将现有开局表单和 `PlayableRoomList` 包进准备态。
  - 将现有 `GameDesk` 和现有行动面板包进游玩态专注布局。
  - 新增 `StartingActionPanel`，创建中时与 `StartingGameDesk` 一起显示。
- Modify: `frontend/src/styles.css`
  - 新增 `play-focus-layout`。
  - 调整 `information-feed-list` 高度约束。
  - 调整 `action-summary-panel` 独立滚动。
  - 让 `play-focus-layout` 在中小屏降为单列。

---

### Task 1: Lock Layout Behavior With Tests

**Files:**
- Modify: `frontend/src/App.test.ts`

- [ ] **Step 1: Add failing tests**

Append these tests after the existing `information feed occupies the full center column` test in `frontend/src/App.test.ts`:

```ts
test("game tab separates setup, starting, and play states", () => {
  assert.equal(source.includes("const showSetupState = tab === \"game\" && !game && !startingGame;"), true);
  assert.equal(source.includes("const showStartingState = tab === \"game\" && startingGame;"), true);
  assert.equal(source.includes("const showPlayState = tab === \"game\" && Boolean(game);"), true);
  assert.equal(source.includes("{showSetupState && ("), true);
  assert.equal(source.includes("{showStartingState && ("), true);
  assert.equal(source.includes("{showPlayState && game && ("), true);
});

test("setup controls render only in setup layout", () => {
  assert.equal(source.includes("<section className=\"game-layout setup-layout\">"), true);
  assert.equal(source.includes("<section className=\"play-focus-layout\">"), true);
  assert.equal(
    source.indexOf("<section className=\"panel setup-panel\">") <
      source.indexOf("{showStartingState && ("),
    true
  );
  assert.equal(
    source.indexOf("<section className=\"panel setup-panel\">") <
      source.indexOf("{showPlayState && game && ("),
    true
  );
});

test("starting game uses focused layout with a lightweight action panel", () => {
  assert.equal(source.includes("<StartingGameDesk playerCount={playerCount} />"), true);
  assert.equal(source.includes("<StartingActionPanel />"), true);
  assert.equal(source.includes("正在创建房间"), true);
});

test("focused play layout gives feed and summaries independent scroll areas", () => {
  assert.equal(styleSource.includes(".play-focus-layout {\n  align-items: start;"), true);
  assert.equal(styleSource.includes("grid-template-columns: minmax(0, 1fr) 320px;"), true);
  assert.equal(styleSource.includes("max-height: clamp(520px, calc(100vh - 360px), 760px);"), true);
  assert.equal(styleSource.includes(".action-summary-panel {\n  margin-top: 12px;\n  max-height: clamp(180px, calc(100vh - 520px), 360px);\n  overflow: auto;"), true);
});
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
cd frontend
npm test
```

Expected: FAIL in `App.test.js`. Missing snippets should include at least `showSetupState`, `play-focus-layout`, `StartingActionPanel`, or the new CSS height strings.

- [ ] **Step 3: Commit failing tests**

Run:

```powershell
git add frontend/src/App.test.ts
git commit -m "test(frontend): 覆盖游玩专注布局预期"
```

Expected: commit succeeds with `frontend/src/App.test.ts`.

---

### Task 2: Split Game Tab Rendering States

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.ts`

- [ ] **Step 1: Add explicit state flags**

In `frontend/src/App.tsx`, replace:

```ts
  const activeAction = game?.next_human_action;
```

with:

```ts
  const activeAction = game?.next_human_action;
  const showSetupState = tab === "game" && !game && !startingGame;
  const showStartingState = tab === "game" && startingGame;
  const showPlayState = tab === "game" && Boolean(game);
```

- [ ] **Step 2: Change the setup wrapper**

Find the current game tab opening:

```tsx
      {tab === "game" && (
        <section className="game-layout">
```

Replace only those two lines with:

```tsx
      {showSetupState && (
        <section className="game-layout setup-layout">
```

Then keep the existing setup panel section, from `<section className="panel setup-panel">` through its matching closing `</section>`, and keep the existing `PlayableRoomList` call inside this wrapper.

- [ ] **Step 3: Close setup state before play state**

Find the code immediately after the existing `PlayableRoomList` block:

```tsx
          {game && (
            <>
```

Replace it with:

```tsx
        </section>
      )}

      {showPlayState && game && (
        <section className="play-focus-layout">
```

This removes the fragment wrapper and starts the focused play layout.

- [ ] **Step 4: Close focused play state after the action panel**

Find the end of the current play block:

```tsx
                <ActionRoundSummary summaries={activeGameReview.summaries} />
              </section>
            </>
          )}
          {!game && startingGame && <StartingGameDesk playerCount={playerCount} />}
        </section>
      )}
```

Replace it with:

```tsx
                <ActionRoundSummary summaries={activeGameReview.summaries} />
              </section>
        </section>
      )}

      {showStartingState && (
        <section className="play-focus-layout">
          <StartingGameDesk playerCount={playerCount} />
          <StartingActionPanel />
        </section>
      )}
```

Do not change any action button condition, `sendAction` payload, option name, or state setter inside the moved action panel.

- [ ] **Step 5: Add starting action status component**

In `frontend/src/App.tsx`, after `StartingGameDesk`, add:

```tsx
function StartingActionPanel() {
  return (
    <section className="panel action-panel starting-action-panel" aria-live="polite">
      <div className="section-title">
        <Shield size={18} />
        <h2>行动</h2>
      </div>
      <div className="empty-state">正在创建房间</div>
    </section>
  );
}
```

- [ ] **Step 6: Run tests**

Run:

```powershell
cd frontend
npm test
```

Expected: state-splitting tests pass. CSS-specific assertions may still fail until Task 3.

- [ ] **Step 7: Commit App rendering changes**

Run:

```powershell
git add frontend/src/App.tsx
git commit -m "feat(frontend): 拆分对局准备态和游玩态"
```

Expected: commit succeeds with `frontend/src/App.tsx`.

---

### Task 3: Add Focused Layout And Scroll Styles

**Files:**
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.ts`

- [ ] **Step 1: Add focused layout CSS**

In `frontend/src/styles.css`, after the existing `.game-layout` rule, add:

```css
.play-focus-layout {
  align-items: start;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1fr) 320px;
}
```

- [ ] **Step 2: Increase information feed vertical space**

Replace the current `.information-feed-list` rule:

```css
.information-feed-list {
  display: grid;
  gap: 8px;
  max-height: 620px;
  overflow: auto;
}
```

with:

```css
.information-feed-list {
  display: grid;
  gap: 8px;
  max-height: clamp(520px, calc(100vh - 360px), 760px);
  overflow: auto;
}
```

- [ ] **Step 3: Add independent summary scrolling**

Replace the current `.action-summary-panel` rule:

```css
.action-summary-panel {
  margin-top: 12px;
}
```

with:

```css
.action-summary-panel {
  margin-top: 12px;
  max-height: clamp(180px, calc(100vh - 520px), 360px);
  overflow: auto;
}
```

- [ ] **Step 4: Collapse focused layout on narrower screens**

Replace the current `@media (max-width: 1100px)` opening block:

```css
@media (max-width: 1100px) {
  .game-layout,
  .two-column {
    grid-template-columns: 1fr;
  }
```

with:

```css
@media (max-width: 1100px) {
  .game-layout,
  .play-focus-layout,
  .two-column {
    grid-template-columns: 1fr;
  }
```

- [ ] **Step 5: Run tests**

Run:

```powershell
cd frontend
npm test
```

Expected: PASS for `flowReview.test.js` and `App.test.js`.

- [ ] **Step 6: Commit style changes**

Run:

```powershell
git add frontend/src/styles.css
git commit -m "style(frontend): 优化游玩态布局和滚动区域"
```

Expected: commit succeeds with `frontend/src/styles.css`.

---

### Task 4: Final Verification

**Files:**
- Verify: `frontend/src/App.tsx`
- Verify: `frontend/src/styles.css`
- Verify: `frontend/src/App.test.ts`

- [ ] **Step 1: Run frontend tests**

Run:

```powershell
cd frontend
npm test
```

Expected: PASS for both compiled frontend test files.

- [ ] **Step 2: Run production build**

Run:

```powershell
cd frontend
npm run build
```

Expected: TypeScript emits no errors and Vite completes the production build.

- [ ] **Step 3: Inspect git status**

Run:

```powershell
git status --short
```

Expected: source files are clean after the task commits. `.superpowers/` may remain untracked from the visual companion and should not be staged.

---

## Self-Review

- Spec coverage: Task 2 preserves preparation state, hides setup controls in starting/play states, and keeps return-to-room behavior untouched. Task 3 gives the information feed a larger viewport-based scroll area and makes `action-summary-panel` scroll independently. Task 4 verifies tests and build.
- Scope: The plan touches only frontend JSX, CSS, and frontend source assertions. It does not alter backend APIs, game rules, AI prompts, event ordering, or room lifecycle behavior.
- Type consistency: Names are consistent across tasks: `showSetupState`, `showStartingState`, `showPlayState`, `play-focus-layout`, `setup-layout`, `StartingActionPanel`.

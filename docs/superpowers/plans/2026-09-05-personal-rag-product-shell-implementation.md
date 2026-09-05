# Personal RAG Product Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Chinese-first Vue product frontend backed by local Mock API/SSE, plus a MySQL-aware Gin control-plane skeleton whose business and Agent endpoints intentionally return `501`.

**Architecture:** Create `apps/web` as an independent Vite/Vue application. Its typed API client calls same-origin root routes and is intercepted by MSW in development and tests. Create `backend/go-api` as an independent Go module that owns only control-plane schema and boundary interfaces; it never accesses Python RAG tables or calls Python gRPC in this iteration.

**Tech Stack:** Vue 3, TypeScript strict, Vite, Vue Router, Pinia, Vitest, Vue Test Utils, MSW, Gin, Go `database/sql`, MySQL driver, Goose SQL migrations.

**Spec:** `docs/superpowers/specs/2026-09-05-personal-rag-product-shell-design.md`

## Global Constraints

- Keep existing `src/rag_mvp/`, `migrations/`, `proto/`, `tests/`, Compose, Earthfile and Python gRPC contract unchanged.
- Create new artifacts only in `apps/web`, `backend/go-api`, and narrowly scoped root/docs files named below.
- Public control-plane routes have no `/api/v1` prefix.
- Python remains the only owner of RAG state; Go may store only control-plane records and `dataset_id → user_id` mappings.
- The frontend is Chinese by default and supports `zh-CN` and `en-US`.
- JWT lifetime is 24 hours; production transport is `HttpOnly`, `Secure`, `SameSite=Lax` Cookie with CSRF protection. The local Mock must not claim production-grade secret protection.
- Password hashes use Argon2id and model API keys are encrypted only in the future Go business implementation; no secret values may enter fixture files, browser logs or API read DTOs.
- Chat selects exactly one ready Dataset. Citation expansion renders the complete retrieved evidence chunk, not an original-document download.
- Go business handlers, Python gRPC client and Agent loop are intentional stubs returning `501 Not Implemented`.
- New Web/Go tests stay under their own applications; do not alter `tests/TEST.md` unless Python `tests/` is changed.
- Each completed task is its own Conventional Commit; stage only its files and never push.

---

## File Structure

```text
apps/web/
├─ src/api/                 # typed HTTP client, DTOs, API functions
├─ src/mocks/               # MSW fixtures, handlers and mock SSE stream
├─ src/components/          # application shell and reusable UI parts
├─ src/views/               # login, overview, datasets, chat and settings
├─ src/stores/              # auth, locale, dataset and chat state
├─ src/router/              # routes and authenticated guard
├─ src/i18n/                # zh-CN/en-US message dictionaries
├─ src/styles/              # tokens and global styles
└─ tests/                   # Vitest setup and browser-independent tests

backend/go-api/
├─ cmd/api/main.go          # dependency wiring and HTTP start
├─ cmd/migrate/main.go      # control-plane migration command
├─ internal/config/         # environment settings
├─ internal/database/       # MySQL pool and readiness probe
├─ internal/domain/         # Go-owned DTOs
├─ internal/repository/     # control-plane repository interfaces
├─ internal/application/    # service interfaces and 501 stubs
├─ internal/agent/          # future runtime/model/tool interfaces only
├─ internal/http/           # router, handlers, errors and middleware interfaces
└─ migrations/00001_control_plane.sql
```

### Task 1: Establish the Vue application foundation

**Files:**
- Create: `apps/web/package.json`, `apps/web/vite.config.ts`, `apps/web/tsconfig.json`, `apps/web/index.html`
- Create: `apps/web/src/main.ts`, `apps/web/src/App.vue`, `apps/web/src/styles/tokens.css`, `apps/web/src/styles/global.css`
- Create: `apps/web/src/router/index.ts`, `apps/web/src/stores/auth.ts`, `apps/web/src/stores/locale.ts`, `apps/web/src/i18n/messages.ts`
- Create: `apps/web/src/components/AppShell.vue`, `apps/web/src/components/LocaleSwitcher.vue`, `apps/web/src/views/LoginView.vue`, `apps/web/src/views/OverviewView.vue`
- Test: `apps/web/tests/setup.ts`, `apps/web/tests/router.spec.ts`, `apps/web/tests/locale.spec.ts`

**Interfaces:**
- Produces `useAuthStore(): { isAuthenticated: boolean; restore(): Promise<void>; signOut(): Promise<void> }`.
- Produces `useLocaleStore(): { value: 'zh-CN' | 'en-US'; set(value: Locale): void }`.

- [ ] **Step 1: Write failing route and locale tests.**

```ts
it('redirects an anonymous visitor from datasets to login', async () => {
  await router.push('/datasets')
  await router.isReady()
  expect(router.currentRoute.value.name).toBe('login')
})
```

- [ ] **Step 2: Run `pnpm --dir apps/web test --run tests/router.spec.ts tests/locale.spec.ts`; expect failure because the package does not exist.**

- [ ] **Step 3: Create the strict Vite/Vue package with Vue Router, Pinia, Vitest and Vue Test Utils. Implement a low-density white application shell with Overview, Datasets, Chat and Settings navigation.**

```ts
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.restore()
  return to.meta.requiresAuth && !auth.isAuthenticated ? { name: 'login' } : true
})
```

- [ ] **Step 4: Add `zh-CN` and `en-US` dictionaries, defaulting to Chinese. Persist only locale; never persist a token in localStorage or sessionStorage.**

- [ ] **Step 5: Run `pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web test --run tests/router.spec.ts tests/locale.spec.ts && pnpm --dir apps/web build`; expect pass.**

- [ ] **Step 6: Commit.**

```bash
git add apps/web
git commit -m "feat(web): 建立个人知识库前端基础"
```

### Task 2: Define typed root-route contracts and local Mock API

**Files:**
- Create: `apps/web/src/api/contracts.ts`, `apps/web/src/api/http.ts`, `apps/web/src/api/auth.ts`, `apps/web/src/api/datasets.ts`, `apps/web/src/api/settings.ts`, `apps/web/src/api/chat.ts`
- Create: `apps/web/src/mocks/data.ts`, `apps/web/src/mocks/handlers.ts`, `apps/web/src/mocks/browser.ts`, `apps/web/src/mocks/server.ts`, `apps/web/src/mocks/sse.ts`
- Test: `apps/web/tests/api-contracts.spec.ts`, `apps/web/tests/mock-auth.spec.ts`

**Interfaces:**
- Produces `ApiError { status: number; code: string; message: string }`, `request<T>(path: string, init?: RequestInit): Promise<T>` and the `Evidence`, `Citation`, `ChatEvent`, Dataset, Job and Settings DTOs.
- Produces `streamChat(request: ChatRequest): { events: AsyncIterable<ChatEvent>; cancel(): void }`.

- [ ] **Step 1: Write failing secret-redaction and route tests.**

```ts
it('never exposes a model API key in a settings response', () => {
  expect(JSON.stringify(mockSettings.chat)).not.toContain('sk-')
  expect(mockSettings.chat.apiKeyConfigured).toBe(true)
})
```

- [ ] **Step 2: Run `pnpm --dir apps/web test --run tests/api-contracts.spec.ts tests/mock-auth.spec.ts`; expect failure.**

- [ ] **Step 3: Define exactly these root paths: `/auth/register`, `/auth/login`, `/auth/logout`, `/me`, `/datasets`, `/datasets/:id`, `/datasets/:id/documents`, `/datasets/:id/jobs`, `/jobs/:id/cancel`, `/jobs/:id/retry`, `/documents/:id`, `/settings`, `/settings/models/chat`, `/settings/models/embedding`, `/settings/models/rerank`, `/settings/agent`, `/chat/stream`. Do not add `/api/v1`.**

- [ ] **Step 4: Implement MSW in-memory state: one Chinese user, ready/processing datasets, a retryable failed Job, redacted Chat/Embedding/Rerank responses, and `rerankEnabled: false`. Login produces a mock 24-hour cookie; `/me` returns `401 AUTH_EXPIRED` after expiry. Upload returns a Job whose progress advances on later polling.**

- [ ] **Step 5: Implement cancellable Mock SSE.**

```ts
export type ChatEvent =
  | { type: 'retrieval'; hits: Evidence[] }
  | { type: 'token'; text: string }
  | { type: 'final'; answer: string; citations: Citation[] }
  | { type: 'error'; code: string; message: string }
```

Emit `retrieval → token* → final | error`. Every citation points to a retrieval hit and contains the hit's full evidence `content`.

- [ ] **Step 6: Run `pnpm --dir apps/web test --run tests/api-contracts.spec.ts tests/mock-auth.spec.ts && pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web build`; expect pass.**

- [ ] **Step 7: Commit.**

```bash
git add apps/web
git commit -m "feat(web): 增加本地 Mock API 与流式契约"
```

### Task 3: Implement login, shell, overview and Dataset/Job experience

**Files:**
- Modify: `apps/web/src/stores/auth.ts`, `apps/web/src/router/index.ts`, `apps/web/src/views/LoginView.vue`, `apps/web/src/views/OverviewView.vue`
- Create: `apps/web/src/stores/datasets.ts`, `apps/web/src/views/DatasetsView.vue`, `apps/web/src/views/DatasetDetailView.vue`
- Create: `apps/web/src/components/DatasetCard.vue`, `apps/web/src/components/UploadPanel.vue`, `apps/web/src/components/JobTable.vue`, `apps/web/src/components/ConfirmDialog.vue`, `apps/web/src/components/StatusBadge.vue`, `apps/web/src/components/EmptyState.vue`, `apps/web/src/components/ErrorNotice.vue`
- Test: `apps/web/tests/auth-flow.spec.ts`, `apps/web/tests/dataset-jobs.spec.ts`, `apps/web/tests/upload-panel.spec.ts`

**Interfaces:**
- Consumes API functions from Task 2.
- Produces `useDatasetStore().readyDatasets` for Task 5.

- [ ] **Step 1: Write failing status-gate tests.**

```ts
it('shows retry only for a retryable failed job', () => {
  const wrapper = mount(JobTable, { props: { jobs: [retryableFailedJob, runningJob] } })
  expect(wrapper.get('[data-job="failed"]').text()).toContain('重试')
  expect(wrapper.get('[data-job="running"]').text()).not.toContain('重试')
})
```

- [ ] **Step 2: Run `pnpm --dir apps/web test --run tests/auth-flow.spec.ts tests/dataset-jobs.spec.ts tests/upload-panel.spec.ts`; expect failure.**

- [ ] **Step 3: Implement login/register/logout/restore. Validate email and password locally, do not echo credentials on errors, and redirect after successful login. On `AUTH_EXPIRED`, clear user state and show an expiration notice.**

- [ ] **Step 4: Implement Overview, Dataset list/detail and create/upload flow. Dataset cards show name, state, document count and last activity. Include loading, empty and retryable network-error states.**

- [ ] **Step 5: Implement Job polling and legal actions.**

```ts
const terminal = new Set<JobStatus>(['SUCCEEDED', 'FAILED', 'CANCELLED'])
```

After upload returns `jobId`, poll that Dataset until terminal; stop on unmount, keep the last state when a poll fails, confirm before deletion, and expose cancel/retry only in legal states.

- [ ] **Step 6: Run `pnpm --dir apps/web test --run tests/auth-flow.spec.ts tests/dataset-jobs.spec.ts tests/upload-panel.spec.ts && pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web build`; expect pass.**

- [ ] **Step 7: Commit.**

```bash
git add apps/web
git commit -m "feat(web): 实现知识库上传与任务追踪"
```

### Task 4: Implement settings and the three model configuration cards

**Files:**
- Create: `apps/web/src/stores/settings.ts`, `apps/web/src/views/SettingsView.vue`
- Create: `apps/web/src/components/ModelConfigCard.vue`, `apps/web/src/components/AgentSettingsCard.vue`, `apps/web/src/components/PasswordInput.vue`
- Modify: `apps/web/src/router/index.ts`
- Test: `apps/web/tests/settings.spec.ts`

**Interfaces:**
- Consumes Task 2 Settings DTOs; read responses never contain `encryptedApiKey` or raw API key values.

- [ ] **Step 1: Write the failing secret and pending-Rerank test.**

```ts
it('shows a redacted key and pending Rerank warning', async () => {
  const wrapper = mount(SettingsView)
  await flushPromises()
  expect(wrapper.text()).toContain('****8Kp2')
  expect(wrapper.text()).toContain('已保存，待后端接入')
})
```

- [ ] **Step 2: Run `pnpm --dir apps/web test --run tests/settings.spec.ts`; expect failure.**

- [ ] **Step 3: Implement three focused forms. Chat: base URL, model name, optional replacement key, timeout, thinking mode. Embedding: base URL, model name, optional replacement key, timeout, default Top-K, dimension default 1024. Rerank: base URL, model name, optional replacement key, timeout, Top-N.**

- [ ] **Step 4: Implement `rerankEnabled` Agent setting, default false, with “已保存，待后端接入”. It must not claim to change Python retrieval.**

- [ ] **Step 5: Run `pnpm --dir apps/web test --run tests/settings.spec.ts && pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web build`; expect pass.**

- [ ] **Step 6: Commit.**

```bash
git add apps/web
git commit -m "feat(web): 增加模型与 Agent 设置页面"
```

### Task 5: Implement single-Dataset Chat, stream state and complete evidence cards

**Files:**
- Create: `apps/web/src/stores/chat.ts`, `apps/web/src/views/ChatView.vue`
- Create: `apps/web/src/components/DatasetSelector.vue`, `apps/web/src/components/ChatComposer.vue`, `apps/web/src/components/MessageBubble.vue`, `apps/web/src/components/CitationCard.vue`, `apps/web/src/components/RetrievalStatus.vue`
- Modify: `apps/web/src/router/index.ts`, `apps/web/src/api/chat.ts`
- Test: `apps/web/tests/chat-stream.spec.ts`, `apps/web/tests/citation-card.spec.ts`

**Interfaces:**
- Consumes `readyDatasets` from Task 3 and `streamChat` from Task 2.
- Produces `ChatRunState { phase: 'idle' | 'retrieving' | 'streaming' | 'done' | 'error'; answer: string; citations: Citation[] }`.

- [ ] **Step 1: Write failing complete-evidence citation test.**

```ts
it('renders complete evidence content after a citation click', async () => {
  const wrapper = mount(CitationCard, { props: { citation } })
  await wrapper.get('button').trigger('click')
  expect(wrapper.text()).toContain(citation.evidence.content)
})
```

- [ ] **Step 2: Run `pnpm --dir apps/web test --run tests/chat-stream.spec.ts tests/citation-card.spec.ts`; expect failure.**

- [ ] **Step 3: Implement a single ready-Dataset selector and guard. Disable the composer with a readable empty state until one Dataset is selected. Do not expose `datasetIds` or multiple-selection UI.**

- [ ] **Step 4: Implement the ordered stream reducer and Stop action.**

```ts
for await (const event of stream.events) {
  if (event.type === 'token') state.answer += event.text
  if (event.type === 'final') state.citations = event.citations
}
```

Reject token events before retrieval, ignore events after a terminal event, and call `cancel()` on Stop/unmount. Error events show a readable failure, never a fake final answer.

- [ ] **Step 5: Implement an accessible citation disclosure card with source, locator, scores and full evidence chunk only; never fetch original-document content.**

- [ ] **Step 6: Run `pnpm --dir apps/web test --run tests/chat-stream.spec.ts tests/citation-card.spec.ts && pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web build`; expect pass.**

- [ ] **Step 7: Commit.**

```bash
git add apps/web
git commit -m "feat(web): 实现单知识库对话与证据引用"
```

### Task 6: Establish the MySQL-aware Gin control-plane skeleton

**Files:**
- Create: `backend/go-api/go.mod`, `backend/go-api/.env.example`, `backend/go-api/README.md`
- Create: `backend/go-api/cmd/api/main.go`, `backend/go-api/cmd/migrate/main.go`
- Create: `backend/go-api/internal/config/settings.go`, `backend/go-api/internal/database/mysql.go`, `backend/go-api/internal/domain/models.go`
- Create: `backend/go-api/internal/repository/interfaces.go`, `backend/go-api/internal/application/interfaces.go`, `backend/go-api/internal/application/not_implemented.go`
- Create: `backend/go-api/internal/agent/interfaces.go`, `backend/go-api/internal/http/router.go`, `backend/go-api/internal/http/handlers.go`, `backend/go-api/internal/http/errors.go`, `backend/go-api/internal/http/middleware.go`
- Create: `backend/go-api/migrations/00001_control_plane.sql`
- Test: `backend/go-api/internal/http/router_test.go`, `backend/go-api/internal/database/mysql_test.go`, `backend/go-api/internal/application/not_implemented_test.go`

**Interfaces:**
- Produces `NewRouter(readiness Readiness, services Services) *gin.Engine`.
- Produces repository interfaces for Go-owned control-plane tables only.
- Produces `AgentRuntime`, `ModelGateway`, `RetrieveKnowledgeTool` and `RagClient` interfaces, without concrete implementations.

- [ ] **Step 1: Write the failing stable-501 router test.**

```go
func TestNotImplementedDatasetRouteReturnsStableProblem(t *testing.T) {
  recorder := httptest.NewRecorder()
  NewRouter(readyStub{ready: true}, newStubServices()).ServeHTTP(
    recorder, httptest.NewRequest(http.MethodGet, "/datasets", nil),
  )
  require.Equal(t, http.StatusNotImplemented, recorder.Code)
  require.JSONEq(t, `{"code":"NOT_IMPLEMENTED"}`, recorder.Body.String())
}
```

- [ ] **Step 2: Run `cd backend/go-api && go test ./internal/http ./internal/database ./internal/application`; expect failure because the module does not exist.**

- [ ] **Step 3: Create settings and the MySQL pool.**

```go
type Settings struct { Address string; MySQLDSN string }
func OpenMySQL(ctx context.Context, dsn string) (*sql.DB, error) {
  db, err := sql.Open("mysql", dsn)
  if err != nil { return nil, err }
  if err := db.PingContext(ctx); err != nil { db.Close(); return nil, err }
  return db, nil
}
```

`/healthz` returns 200 without dependencies. `/readyz` uses a `Readiness` interface backed by `PingContext`; missing/unreachable MySQL returns controlled 503, never panic.

- [ ] **Step 4: Add the initial Goose migration. Create only `users`, `jwt_token_revocations`, `chat_model_configs`, `embedding_model_configs`, `rerank_model_configs`, `agent_settings`, `dataset_ownership`, `conversations`, `conversation_messages`. Use InnoDB/UTF-8, unique email, per-user unique model config rows, unique `dataset_ownership.dataset_id`, indexed owner IDs, embedding dimension default 1024 and rerank disabled by default. Do not use foreign keys into Python RAG tables.**

- [ ] **Step 5: Define interfaces and 501 handlers.**

```go
type RagClient interface {
  Retrieve(ctx context.Context, request RetrieveRequest) (RetrieveResult, error)
}
type AgentRuntime interface {
  Run(ctx context.Context, input ChatInput, emit func(ChatEvent) error) error
}
func notImplemented(c *gin.Context) { c.JSON(http.StatusNotImplemented, gin.H{"code": "NOT_IMPLEMENTED"}) }
```

Register every approved root route. All business endpoints must use the stable 501 response. Do not issue JWTs, write user/config data, call a model, call gRPC or emit real SSE.

- [ ] **Step 6: Add `cmd/migrate`, using `MYSQL_DSN` and only `backend/go-api/migrations`. Document `HTTP_ADDR`, `MYSQL_DSN` and future-only `CONTROL_PLANE_ENCRYPTION_KEY`; no real keys in examples.**

- [ ] **Step 7: Run `cd backend/go-api && gofmt -w . && go test ./... && go vet ./...`; expect pass. If a real MySQL instance is unavailable, record that only fake-readiness tests ran.**

- [ ] **Step 8: Commit.**

```bash
git add backend/go-api
git commit -m "feat(control-plane): 建立 MySQL 与 Gin 骨架"
```

### Task 7: Document entry points and run final combined verification

**Files:**
- Modify: `README.md`
- Create: `docs/development/product-shell.md`
- Test: `apps/web/tests/**/*.spec.ts`, `backend/go-api/**/*.go`

**Interfaces:**
- Consumes all Web and Go artifacts.
- Produces instructions distinguishing Mock frontend from unimplemented Go business endpoints.

- [ ] **Step 1: Add a manual acceptance checklist: Web dev server completes Login, Dataset/Job, Settings and Chat with Mock only; `go run ./cmd/api` serves health checks while business routes return 501.**

- [ ] **Step 2: Document runtime commands, root route list, Mock limitations, model-secret rules, 24-hour JWT production contract and explicit non-goals. Never claim real auth, persistence, Agent, gRPC integration, SSE or Rerank is implemented.**

- [ ] **Step 3: Run final checks.**

```bash
pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web test --run && pnpm --dir apps/web build
cd backend/go-api && go test ./... && go vet ./...
git diff --check
uv run pytest tests/contract/test_build_entrypoints.py -q
```

Expected: all commands pass. The Python contract command runs from repository root.

- [ ] **Step 4: Commit.**

```bash
git add README.md docs/development/product-shell.md
git commit -m "docs: 说明个人 RAG 产品壳开发入口"
```

## Plan Self-Review

- Spec coverage: Tasks 1–5 cover every Web requirement: Chinese-first locale, Mock API/SSE, authentication UX, Dataset/Job, three model cards, Rerank warning, single-Dataset Chat and full evidence cards. Task 6 covers MySQL, migrations, health checks and only Go/Agent interfaces. Task 7 documents and validates the boundary.
- Scope control: no task modifies Python source, RAG tables, protobuf, Docker, Earthfile or `tests/`. Multi-Dataset retrieval and real Agent execution are intentionally excluded.
- Type consistency: Task 2 defines the DTOs and stream interfaces used by Tasks 3–5; Task 6 declares the future Go interfaces before routes depend on services.
- Placeholder scan: no unresolved work remains within the defined scope; statements about future functionality are explicit non-goals.

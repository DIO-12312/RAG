<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { getMetrics, getTrace, getTraces, type MetricsResponse, type Panel, type Point, type Service, type Span, type TraceDetail, type TraceList, type Window } from "@/api/observability";
import { ApiError } from "@/api/http";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const auth = useAuthStore();
const windowValue = ref<Window>("1h");
const service = ref<Service>("rag-go-api");
const metrics = ref<MetricsResponse | null>(null);
const traces = ref<TraceList | null>(null);
const selectedTraceId = ref<string | null>(null);
const selectedTrace = computed(() => traces.value?.traces.find((item) => item.traceId === selectedTraceId.value));
const targetPanel = computed(() => metrics.value?.panels.find((panel) => panel.key === "telemetry_targets"));
const stagePanels = computed(() => metrics.value?.panels.filter((panel) => ["ingestion_stage_p95", "retrieval_stage_p95", "agent_model_calls"].includes(panel.key)) ?? []);
const overviewPanels = computed(() => metrics.value?.panels.filter((panel) => !["telemetry_targets", "ingestion_stage_p95", "retrieval_stage_p95", "agent_model_calls"].includes(panel.key)) ?? []);
const timeline = computed(() => {
  const spans = detail.value?.spans ?? [];
  const valid = spans.map((span) => ({ span, start: Date.parse(span.startedAt) })).filter((item) => Number.isFinite(item.start));
  if (!valid.length) return [];
  const first = Math.min(...valid.map((item) => item.start));
  const last = Math.max(...valid.map((item) => item.start + item.span.durationMs));
  const width = Math.max(last - first, 1);
  return valid.sort((a, b) => a.start - b.start).map(({ span, start }) => ({
    ...span,
    offset: Math.max(0, ((start - first) / width) * 100),
    barWidth: Math.max(0.5, (span.durationMs / width) * 100),
    relativeStart: start - first,
  }));
});
const closeButton = ref<HTMLButtonElement | null>(null);
const detail = ref<TraceDetail | null>(null);
const metricsError = ref("");
const tracesError = ref("");
const detailError = ref("");
const detailLoading = ref(false);
const loading = ref(false);
let requestVersion = 0;
let detailRequestVersion = 0;
let returnFocus: HTMLElement | null = null;

watch(selectedTraceId, (traceId, _previous, onCleanup) => {
  if (!traceId) return;
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  onCleanup(() => { document.body.style.overflow = previousOverflow; });
});

const panelNames: Record<string, string> = {
  chat_throughput: "对话吞吐量 / 秒", chat_error_rate: "对话错误率", chat_p50: "对话 P50 / 秒",
  chat_p95: "对话 P95 / 秒", retrieval_p95: "检索 P95 / 秒",
  grpc_p95: "gRPC P95 / 秒", ingestion_throughput: "摄取吞吐量 / 秒",
  ingestion_results: "摄取结果 / 秒", outbox_results: "Outbox 发布 / 秒",
  ingestion_stage_p95: "摄取阶段 P95 / 秒", retrieval_stage_p95: "检索阶段 P95 / 秒",
  agent_model_calls: "Agent 模型调用 / 秒",
};

const targetNames: Record<string, string> = {
  "otel-collector": "Collector 指标出口", "observability-retention": "Trace 接收", tempo: "Tempo 链路存储",
};
const targetKeys = ["otel-collector", "observability-retention", "tempo"];

function lastValue(points: Point[]): number | null { return points.at(-1)?.value ?? null; }
function targetState(job: string): string {
  const value = lastValue(targetPanel.value?.series.find((series) => series.name === job)?.points ?? []);
  return value === null ? "暂无抓取状态" : value === 1 ? "可抓取" : "抓取失败";
}
function stageValue(panel: Panel, name: string): string {
  const value = lastValue(panel.series.find((series) => series.name === name)?.points ?? []);
  return value === null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}
function spanName(span: Span): string { return `${span.service} · ${span.stage}`; }

function line(points: Point[]): string {
  const values = points.filter((point) => Number.isFinite(point.value));
  if (!values.length) return "";
  const min = Math.min(...values.map((point) => point.value));
  const max = Math.max(...values.map((point) => point.value));
  const span = max - min || 1;
  return values.map((point, index) => `${(index / Math.max(values.length - 1, 1)) * 100},${38 - ((point.value - min) / span) * 30}`).join(" ");
}

function latest(panel: Panel): string {
  const point = panel.series[0]?.points.at(-1);
  if (!point) return "—";
  return point.value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}

async function accessError(error: unknown): Promise<boolean> {
  if (!(error instanceof ApiError) || (error.status !== 401 && error.status !== 403)) return false;
  try { await auth.refresh(); } catch { /* The router handles an expired session. */ }
  await router.replace(auth.isAuthenticated ? "/" : "/login");
  return true;
}

async function refresh(): Promise<void> {
  const version = ++requestVersion;
  ++detailRequestVersion;
  loading.value = true;
  selectedTraceId.value = null;
  returnFocus = null;
  detail.value = null;
  detailLoading.value = false;
  detailError.value = "";
  metricsError.value = "";
  tracesError.value = "";
  const [metricResult, traceResult] = await Promise.allSettled([
    getMetrics(windowValue.value), getTraces(service.value, windowValue.value),
  ]);
  if (version !== requestVersion) return;
  if (metricResult.status === "fulfilled") metrics.value = metricResult.value;
  else {
    metrics.value = null;
    if (await accessError(metricResult.reason)) return;
    metricsError.value = "指标服务暂不可用，请稍后重试。";
  }
  if (traceResult.status === "fulfilled") traces.value = traceResult.value;
  else {
    traces.value = null;
    if (await accessError(traceResult.reason)) return;
    tracesError.value = "链路服务暂不可用，请稍后重试。";
  }
  loading.value = false;
}

function closeTrace(): void {
  ++detailRequestVersion;
  selectedTraceId.value = null;
  detail.value = null;
  detailError.value = "";
  detailLoading.value = false;
  const target = returnFocus;
  returnFocus = null;
  void nextTick(() => { if (target?.isConnected) target.focus(); });
}

async function openTrace(traceId: string, event: MouseEvent): Promise<void> {
  const version = ++detailRequestVersion;
  returnFocus = event.currentTarget as HTMLElement;
  selectedTraceId.value = traceId;
  detail.value = null;
  detailError.value = "";
  detailLoading.value = true;
  await nextTick();
  closeButton.value?.focus();
  try {
    const result = await getTrace(traceId);
    if (version === detailRequestVersion) detail.value = result;
  }
  catch (error) {
    if (version !== detailRequestVersion) return;
    if (await accessError(error)) return;
    detailError.value = "链路详情暂不可用，请稍后重试。";
  }
  finally {
    if (version === detailRequestVersion) detailLoading.value = false;
  }
}

onMounted(() => { void refresh(); });
</script>

<template>
  <main class="obs-page">
    <header class="obs-header">
      <div>
        <p class="obs-eyebrow">
          SYSTEM OBSERVABILITY
        </p><h1>系统观测</h1><p>查看近期指标与脱敏链路。数据来自观测服务，任务状态仍以知识库为准。</p>
      </div>
      <button
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '加载中…' : '刷新' }}
      </button>
    </header>
    <section
      class="obs-filters"
      aria-label="观测筛选"
    >
      <label>时间范围<select
        v-model="windowValue"
        @change="refresh"
      ><option value="15m">最近 15 分钟</option><option value="1h">最近 1 小时</option><option value="6h">最近 6 小时</option><option value="24h">最近 24 小时</option></select></label>
      <label>链路服务<select
        v-model="service"
        @change="refresh"
      ><option value="rag-go-api">Go API</option><option value="rag-python-server">Python RPC</option><option value="rag-python-worker">Worker</option><option value="rag-python-outbox">Outbox</option></select></label>
    </section>
    <section aria-labelledby="health-heading">
      <h2 id="health-heading">
        采集链路健康
      </h2>
      <p class="obs-help">
        Prometheus 最近一次抓取结果；正常仅表示指标端点可抓取，不代表业务请求成功。指标在全部服务间汇总，链路服务筛选仅用于下方链路列表。
      </p>
      <p
        v-if="metricsError || !targetPanel || targetPanel.status === 'unavailable'"
        role="status"
      >
        采集状态暂不可用。
      </p>
      <p
        v-else-if="targetPanel.status === 'empty'"
        role="status"
      >
        暂无抓取状态。
      </p>
      <div
        v-else
        class="obs-targets"
      >
        <div
          v-for="job in targetKeys"
          :key="job"
          class="obs-target"
          :class="{ 'obs-target-down': targetState(job) === '抓取失败', 'obs-target-unknown': targetState(job) === '暂无抓取状态' }"
        >
          <strong>{{ targetNames[job] }}</strong><span>{{ targetState(job) }}</span>
        </div>
      </div>
    </section>
    <section aria-labelledby="metrics-heading">
      <h2 id="metrics-heading">
        指标
      </h2>
      <p
        v-if="metricsError"
        role="status"
      >
        {{ metricsError }}
      </p>
      <p
        v-else-if="metrics?.status === 'empty'"
        role="status"
      >
        所选时间范围暂无指标。
      </p>
      <p
        v-else-if="metrics?.status === 'partial'"
        role="status"
      >
        部分指标暂不可用，已显示可用结果。
      </p>
      <div
        v-if="metrics"
        class="obs-panels"
      >
        <article
          v-for="panel in overviewPanels"
          :key="panel.key"
          class="obs-panel"
        >
          <h3>{{ panelNames[panel.key] ?? panel.key }}</h3>
          <p v-if="panel.status === 'unavailable'">
            查询失败
          </p>
          <p v-else-if="panel.status === 'empty'">
            暂无数据
          </p>
          <template v-else>
            <strong>{{ latest(panel) }}</strong><svg
              v-for="series in panel.series"
              :key="series.name"
              viewBox="0 0 100 42"
              preserveAspectRatio="none"
              role="img"
              :aria-label="`${series.name} 趋势`"
            ><polyline
              :points="line(series.points)"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
            /></svg><small v-if="panel.series.length > 1">{{ panel.series.map((series) => series.name).join(' · ') }}</small>
          </template>
        </article>
      </div>
    </section>
    <section aria-labelledby="stages-heading">
      <h2 id="stages-heading">
        RAG 阶段
      </h2>
      <p class="obs-help">
        P95 使用最近 5 分钟的样本计算；无样本时显示暂无数据。模型调用为每秒次数。
      </p>
      <div
        v-if="metrics"
        class="obs-panels"
      >
        <article
          v-for="panel in stagePanels"
          :key="panel.key"
          class="obs-panel obs-stage-panel"
        >
          <h3>{{ panelNames[panel.key] }}</h3>
          <p v-if="panel.status === 'unavailable'">
            查询失败
          </p>
          <p v-else-if="panel.status === 'empty'">
            暂无数据
          </p>
          <div
            v-else
            class="obs-stage-list"
          >
            <div
              v-for="series in panel.series"
              :key="series.name"
            >
              <span>{{ series.name }}</span><strong>{{ stageValue(panel, series.name) }}</strong>
            </div>
          </div>
        </article>
      </div>
    </section>
    <section aria-labelledby="traces-heading">
      <h2 id="traces-heading">
        链路
      </h2>
      <p
        v-if="tracesError"
        role="status"
      >
        {{ tracesError }}
      </p>
      <p
        v-else-if="traces?.status === 'empty'"
        role="status"
      >
        所选服务和时间范围暂无链路。
      </p>
      <div
        v-if="traces?.traces.length"
        class="obs-table-wrap"
      >
        <table>
          <thead><tr><th>开始时间</th><th>Trace ID</th><th>耗时</th><th>操作</th></tr></thead><tbody>
            <tr
              v-for="trace in traces.traces"
              :key="trace.traceId"
            >
              <td>{{ trace.startedAt }}</td><td><code>{{ trace.traceId }}</code></td><td>{{ trace.durationMs.toFixed(1) }} ms</td><td>
                <button
                  type="button"
                  @click="openTrace(trace.traceId, $event)"
                >
                  查看
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
  <Teleport to="body">
    <div
      v-if="selectedTraceId"
      class="obs-trace-overlay"
      @click.self="closeTrace"
    >
      <section
        class="obs-trace-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trace-dialog-heading"
        @keydown.esc.stop.prevent="closeTrace"
        @keydown.tab.prevent="closeButton?.focus()"
      >
        <div class="obs-trace-card-header">
          <div>
            <p class="obs-trace-eyebrow">
              TRACE DETAILS
            </p>
            <h2 id="trace-dialog-heading">
              完整链路
            </h2>
            <code>{{ selectedTraceId }}</code>
          </div>
          <button
            ref="closeButton"
            type="button"
            aria-label="关闭链路详情"
            @click="closeTrace"
          >
            关闭
          </button>
        </div>
        <div
          v-if="selectedTrace"
          class="obs-trace-summary"
        >
          <span>开始时间：{{ selectedTrace.startedAt }}</span>
          <span>总耗时：{{ selectedTrace.durationMs.toFixed(1) }} ms</span>
          <span v-if="detail">Span：{{ detail.spans.length }}</span>
        </div>
        <p
          v-if="detailLoading"
          role="status"
        >
          正在加载链路详情…
        </p>
        <p
          v-else-if="detailError"
          role="status"
        >
          {{ detailError }}
        </p>
        <p
          v-else-if="detail?.status === 'empty'"
          role="status"
        >
          未找到这条链路。
        </p>
        <template v-else-if="detail">
          <p
            v-if="detail.truncated"
            role="status"
          >
            链路较长，仅显示前 200 个 Span。
          </p>
          <p
            v-if="detail.spans.length === 0"
            role="status"
          >
            这条链路没有可显示的 Span。
          </p>
          <div
            v-else-if="timeline.length"
            class="obs-waterfall"
            aria-label="链路时间轴"
          >
            <div
              v-for="span in timeline"
              :key="span.spanId"
              class="obs-waterfall-row"
            >
              <div
                class="obs-waterfall-name"
                :title="spanName(span)"
              >
                {{ spanName(span) }}
              </div>
              <div class="obs-waterfall-track">
                <div
                  class="obs-waterfall-bar"
                  :class="{ 'obs-waterfall-error': span.outcome === 'failed' }"
                  :style="{ left: `${span.offset}%`, width: `${Math.min(span.barWidth, 100 - span.offset)}%` }"
                />
              </div>
              <span class="obs-waterfall-time">+{{ span.relativeStart.toFixed(1) }} / {{ span.durationMs.toFixed(1) }} ms</span>
            </div>
          </div>
          <div
            v-if="detail.spans.length"
            class="obs-detail-table"
          >
            <table>
              <thead><tr><th>阶段</th><th>服务</th><th>耗时</th><th>结果</th><th>关联 ID</th></tr></thead><tbody>
                <tr
                  v-for="span in detail.spans"
                  :key="span.spanId"
                >
                  <td>{{ span.stage }}</td><td>{{ span.service }}</td><td>{{ span.durationMs.toFixed(1) }} ms</td><td>{{ span.outcome }} {{ span.errorCode }}</td><td>{{ span.jobId || span.taskId || span.runId || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.obs-page { max-width: 1400px; margin: 0 auto; padding: 2rem; color: #243044; }
.obs-header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.obs-header h1 { margin: .25rem 0; font-size: 2rem; }
.obs-header p { color: #617086; }
.obs-eyebrow { letter-spacing: .14em; font-size: .72rem; font-weight: 700; }
.obs-filters { display: flex; gap: 1rem; margin: 1.5rem 0 2rem; flex-wrap: wrap; }
.obs-filters label { display: grid; gap: .35rem; font-size: .85rem; }
button, select { border: 1px solid #cbd5e1; border-radius: .6rem; background: white; color: inherit; padding: .6rem .85rem; cursor: pointer; }
button:disabled { opacity: .6; cursor: wait; }
.obs-panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin: 1rem 0 2rem; }
.obs-panel { min-height: 150px; padding: 1rem; border: 1px solid #e1e8ef; border-radius: .85rem; background: #fff; }
.obs-panel h3 { font-size: .9rem; margin: 0 0 1rem; }.obs-panel strong { font-size: 1.6rem; }
.obs-panel svg { display: block; width: 100%; height: 54px; color: #4776a8; }.obs-panel small { color: #617086; }
.obs-help { color: #617086; font-size: .85rem; }
.obs-targets { display: flex; flex-wrap: wrap; gap: .75rem; margin: 1rem 0 2rem; }
.obs-target { display: flex; gap: 1rem; align-items: center; justify-content: space-between; min-width: 210px; padding: .8rem 1rem; border: 1px solid #a7dbc5; border-radius: .7rem; background: #ecf9f2; font-size: .85rem; }
.obs-target-down { border-color: #e9b6b6; background: #fff2f2; }
.obs-target-unknown { border-color: #d7dce4; background: #f6f7f9; }
.obs-stage-panel { min-height: 0; }
.obs-stage-list { display: grid; gap: .6rem; }
.obs-stage-list > div { display: flex; justify-content: space-between; gap: 1rem; border-bottom: 1px solid #e1e8ef; padding: .3rem 0; font-size: .85rem; }
.obs-stage-list strong { font-size: .9rem; font-variant-numeric: tabular-nums; }
.obs-table-wrap { overflow-x: auto; border: 1px solid #e1e8ef; border-radius: .85rem; margin: 1rem 0 2rem; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: .85rem; } th, td { text-align: left; padding: .75rem; border-bottom: 1px solid #e1e8ef; } code { font-size: .75rem; }
.obs-trace-overlay { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 1.5rem; background: #15223899; backdrop-filter: blur(7px); -webkit-backdrop-filter: blur(7px); }
.obs-trace-card { box-sizing: border-box; width: min(100%, 1100px); max-height: calc(100dvh - 3rem); overflow-y: auto; padding: 1.75rem; border: 1px solid #cbd5e1; border-radius: 1rem; background: #fff; color: #243044; box-shadow: 0 24px 80px #10182855; }
.obs-trace-card-header { display: flex; justify-content: space-between; align-items: start; gap: 1rem; margin-bottom: 1.25rem; }
.obs-trace-card-header h2 { margin: 0 0 .35rem; font-size: 1.5rem; }
.obs-trace-eyebrow { margin: 0 0 .35rem; color: #617086; font-size: .7rem; font-weight: 700; letter-spacing: .12em; }
.obs-trace-card-header code { overflow-wrap: anywhere; }
.obs-trace-summary { display: flex; flex-wrap: wrap; gap: .75rem 1.5rem; margin-bottom: 1.25rem; padding: .9rem 1rem; border-radius: .65rem; background: #f1f5f9; font-size: .85rem; }
.obs-detail-table { overflow-x: auto; border: 1px solid #e1e8ef; border-radius: .65rem; }
.obs-detail-table table { min-width: 680px; }
.obs-waterfall { display: grid; gap: .45rem; margin: 0 0 1rem; padding: .75rem; border: 1px solid #e1e8ef; border-radius: .65rem; }
.obs-waterfall-row { display: grid; grid-template-columns: minmax(130px, 220px) minmax(160px, 1fr) 135px; align-items: center; gap: .75rem; font-size: .78rem; }
.obs-waterfall-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.obs-waterfall-track { position: relative; height: 18px; border-radius: 4px; background: #f1f5f9; }
.obs-waterfall-bar { position: absolute; top: 2px; height: 14px; border-radius: 3px; background: #4776a8; }
.obs-waterfall-error { background: #c65353; }
.obs-waterfall-time { text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 650px) { .obs-page { padding: 1rem; }.obs-header { align-items: start; flex-direction: column; }.obs-trace-overlay { padding: .75rem; }.obs-trace-card { max-height: calc(100dvh - 1.5rem); padding: 1rem; } }
</style>

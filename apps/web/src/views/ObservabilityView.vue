<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { getMetrics, getTrace, getTraces, type MetricsResponse, type Panel, type Point, type Service, type TraceDetail, type TraceList, type Window } from "@/api/observability";
import { ApiError } from "@/api/http";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const auth = useAuthStore();
const windowValue = ref<Window>("1h");
const service = ref<Service>("rag-go-api");
const metrics = ref<MetricsResponse | null>(null);
const traces = ref<TraceList | null>(null);
const detail = ref<TraceDetail | null>(null);
const metricsError = ref("");
const tracesError = ref("");
const detailError = ref("");
const loading = ref(false);
let requestVersion = 0;

const panelNames: Record<string, string> = {
  chat_throughput: "对话吞吐量 / 秒", chat_error_rate: "对话错误率", chat_p50: "对话 P50 / 秒",
  chat_p95: "对话 P95 / 秒", retrieval_p95: "检索 P95 / 秒",
  grpc_p95: "gRPC P95 / 秒", ingestion_throughput: "摄取吞吐量 / 秒",
  ingestion_results: "摄取结果 / 秒", outbox_results: "Outbox 发布 / 秒",
};

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
  loading.value = true;
  detail.value = null;
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

async function openTrace(traceId: string): Promise<void> {
  detail.value = null;
  detailError.value = "";
  try { detail.value = await getTrace(traceId); }
  catch (error) {
    if (await accessError(error)) return;
    detailError.value = "链路详情暂不可用，请稍后重试。";
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
      <label>服务<select
        v-model="service"
        @change="refresh"
      ><option value="rag-go-api">Go API</option><option value="rag-python-server">Python RPC</option><option value="rag-python-worker">Worker</option><option value="rag-python-outbox">Outbox</option></select></label>
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
          v-for="panel in metrics.panels"
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
                  @click="openTrace(trace.traceId)"
                >
                  查看
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section
      v-if="detail || detailError"
      aria-labelledby="detail-heading"
    >
      <h2 id="detail-heading">
        链路详情
      </h2>
      <p
        v-if="detailError"
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
        </p><div class="obs-table-wrap">
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
  </main>
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
.obs-table-wrap { overflow-x: auto; border: 1px solid #e1e8ef; border-radius: .85rem; margin: 1rem 0 2rem; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: .85rem; } th, td { text-align: left; padding: .75rem; border-bottom: 1px solid #e1e8ef; } code { font-size: .75rem; }
@media (max-width: 650px) { .obs-page { padding: 1rem; }.obs-header { align-items: start; flex-direction: column; } }
</style>

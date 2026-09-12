/**
 * 构建时注入的 commit 版本。
 *
 * 发布镜像由 `scripts/release.py publish` 以 `--build-arg VITE_GIT_COMMIT=<sha>`
 * 构建，页面据此确认线上镜像对应哪一次推送；本地开发或缺少该参数时为
 * `unknown`，不猜测版本号。
 */
const injected = import.meta.env.VITE_GIT_COMMIT;

export const buildCommit: string =
  typeof injected === "string" && injected.trim() ? injected.trim() : "unknown";

/** 侧栏展示用短版本号；未知版本保持 `unknown`。 */
export function shortCommit(value: string = buildCommit): string {
  return value === "unknown" ? value : value.slice(0, 7);
}

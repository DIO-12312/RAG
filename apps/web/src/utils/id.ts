/**
 * 生成 RFC 4122 UUID v4。
 *
 * `crypto.randomUUID` 只在 secure context（HTTPS 或 localhost/127.0.0.1）下可用，
 * 通过明文 HTTP 的远程 IP 访问时它不存在。这里做能力探测，缺失时回退到
 * `Math.random` 实现，保证开发暴露与生产同源可用。
 */
export function randomUUID(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

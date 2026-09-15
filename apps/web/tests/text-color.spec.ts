import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

// jsdom 不计算外部样式表的层叠，因此这里锁定「样式契约」本身：
// 会话记录同样使用 <article>，弱化色规则一旦重新命中聊天记录，
// 同一段回答就会出现「段落浅、列表与标题深」的不一致（见 2026-09-16 生产实测）。
const stylesheet = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");

function declarations(selector: string): string[] {
  const pattern = new RegExp(`(^|[},])\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "gm");
  return [...stylesheet.matchAll(pattern)].map((match) => match[2] ?? "");
}

it("does not dim prose paragraphs inside chat messages", () => {
  const mutedArticleParagraph = declarations("article p").filter((body) =>
    body.includes("var(--text-muted)"),
  );
  expect(mutedArticleParagraph).toHaveLength(0);

  const scoped = declarations("article:not(.chat-message) p");
  expect(scoped).toHaveLength(1);
  expect(scoped[0]).toContain("var(--text-muted)");
});

it("pins chat Markdown prose to the body text color", () => {
  const chatProse = declarations(".chat-message .markdown-content");
  expect(chatProse).toHaveLength(1);
  expect(chatProse[0]).toContain("var(--text)");
});

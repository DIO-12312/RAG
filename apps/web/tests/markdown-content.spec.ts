import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import MarkdownContent from "../src/components/MarkdownContent.vue";
it("renders structured Markdown and updates streamed content",async()=>{
  const wrapper=mount(MarkdownContent,{props:{content:'# Title\n\n**bold**\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```go\nfmt.Println("hi")\n```'}});
  expect(wrapper.get('h1').text()).toBe('Title');expect(wrapper.get('strong').text()).toBe('bold');expect(wrapper.findAll('td')).toHaveLength(2);expect(wrapper.get('pre code').text()).toContain('fmt.Println');
  await wrapper.setProps({content:'Updated *answer*'});expect(wrapper.get('em').text()).toBe('answer');
});
it("never injects raw HTML, executable links or tracking images",()=>{
  const wrapper=mount(MarkdownContent,{props:{content:'<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))\n![tracking](https://example.test/pixel)'}});
  expect(wrapper.find('script,img,iframe').exists()).toBe(false);expect(wrapper.find('[onerror]').exists()).toBe(false);expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false);expect(wrapper.get('a').attributes('rel')).toContain('noreferrer');
});

const citations = [{ ordinal: 1, evidence: { chunkId: 'chunk-1', content: '完整 chunk 原文\n第二行 <script>不可执行</script>', sourceName: '指南.md', locator: 'L10–L20', scores: { fusionScore: 0.1 } } }];
it('renders only mapped prose citations as inline circular controls', () => {
  const wrapper = mount(MarkdownContent, { props: { content: '结论。[1][9]\n\n`[1]`\n\n```txt\n[1]\n```\n\n[1](https://example.test)', citations } });
  expect(wrapper.findAll('.citation-marker')).toHaveLength(1);
  expect(wrapper.get('.citation-marker').text()).toBe('1');
  expect(wrapper.get('p').text()).toBe('结论。1[9]');
  expect(wrapper.get('a').text()).toBe('1');
  wrapper.unmount();
});
it('previews source on hover and expands full evidence on click with Escape focus return', async () => {
  const wrapper = mount(MarkdownContent, { attachTo: document.body, props: { content: '结论。[1]', citations } });
  const marker = wrapper.get('.citation-marker');
  await marker.trigger('mouseover');
  expect(document.querySelector('.citation-preview')?.textContent).toContain('指南.md');
  expect(document.querySelector('.citation-preview')?.textContent).toContain('L10–L20');
  await marker.trigger('click');
  for (const line of citations[0]!.evidence.content.split('\n')) {
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain(line);
  }
  expect(document.querySelector('[role="dialog"] script')).toBeNull();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await wrapper.vm.$nextTick();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(marker.element);
  expect(document.querySelector(".citation-card")).toBeNull();
  await marker.trigger('click');
  document.querySelector<HTMLButtonElement>('[aria-label="关闭来源"]')!.click();
  await wrapper.vm.$nextTick();
  expect(document.querySelector('.citation-card')).toBeNull();
  await marker.trigger('click');
  document.querySelector<HTMLElement>('.citation-backdrop')!.click();
  await wrapper.vm.$nextTick();
  expect(document.querySelector('.citation-card')).toBeNull();
  wrapper.unmount();
});
it('supports keyboard opening and clears stale sources when message changes', async () => {
  const wrapper = mount(MarkdownContent, { attachTo: document.body, props: { content: '结论。[1]', citations } });
  await wrapper.get('.citation-marker').trigger('keydown', { key: 'Enter' });
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  await wrapper.setProps({ content: '另一条回答。[1]', citations: [] });
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(wrapper.find('.citation-marker').exists()).toBe(false);
  wrapper.unmount();
});
it('renders expanded source Markdown safely without turning source numbers into citations', async () => {
  const content = '# 来源标题\n\n**重要说明**\n\n- 第一项\n- 第二项\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```js\nconst value = 1;\n```\n\n来源内部编号 [1]\n\n<script>alert(1)</script>\n\n![图片](https://example.test/pixel)\n\n[危险](javascript:alert(1))';
  const wrapper = mount(MarkdownContent, { attachTo: document.body, props: {
    content: '结论。[1]', citations: [{ ...citations[0]!, evidence: { ...citations[0]!.evidence, content } }],
  } });
  try {
    await wrapper.get('.citation-marker').trigger('click');
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.querySelector('h1')?.textContent).toBe('来源标题');
    expect(dialog.querySelector('strong')?.textContent).toBe('重要说明');
    expect(dialog.querySelectorAll('li')).toHaveLength(2);
    expect(dialog.querySelectorAll('td')).toHaveLength(2);
    expect(dialog.querySelector('pre code')?.textContent).toContain('const value = 1;');
    expect(dialog.textContent).toContain('来源内部编号 [1]');
    expect(dialog.querySelector('.citation-marker')).toBeNull();
    expect(dialog.querySelector('script,img,iframe,[onerror],a[href^="javascript:"]')).toBeNull();
    expect(dialog.querySelector('a')?.getAttribute('rel')).toContain('noreferrer');
  } finally { wrapper.unmount(); }
});

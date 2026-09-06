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

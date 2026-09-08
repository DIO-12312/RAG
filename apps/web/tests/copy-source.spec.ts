import { flushPromises, mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import MarkdownContent from '../src/components/MarkdownContent.vue';

it.each(['modern', 'unavailable', 'denied', 'failed', 'throws'])(
  'copies raw source with %s clipboard support and reports the actual result', async (mode) => {
    const content = '# 原文\n\n**保留 Markdown**\n第二行';
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const commandDescriptor = Object.getOwnPropertyDescriptor(document, 'execCommand');
    const writeText = mode === 'modern' ? vi.fn().mockResolvedValue(undefined) : vi.fn().mockRejectedValue(new Error('denied'));
    let selectedText = '';
    const execCommand = vi.fn((command: string) => {
      expect(command).toBe('copy');
      const input = document.activeElement as HTMLTextAreaElement;
      expect(input.tagName).toBe('TEXTAREA');
      selectedText = input.value.slice(input.selectionStart, input.selectionEnd);
      if (mode === 'throws') throw new Error('copy blocked');
      return mode !== 'failed';
    });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: mode === 'unavailable' ? undefined : { writeText } });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand });
    const wrapper = mount(MarkdownContent, { attachTo: document.body, props: {
      content: '结论。[1]', citations: [{ ordinal: 1, evidence: { chunkId: 'c', content, sourceName: '文档.md', locator: 'L1', scores: { fusionScore: 1 } } }],
    } });
    try {
      await wrapper.get('.citation-marker').trigger('click');
      const button = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(b => b.textContent?.includes('复制原文'))!;
      button.focus();
      button.click();
      await flushPromises();
      const status = document.querySelector('[role="status"]')?.textContent;
      expect(status).toBe(['failed', 'throws'].includes(mode) ? '复制失败，请选择原文复制' : '已复制');
      if (mode === 'modern') {
        expect(writeText).toHaveBeenCalledWith(content);
        expect(execCommand).not.toHaveBeenCalled();
      } else {
        expect(selectedText).toBe(content);
      }
      expect(document.querySelector('textarea')).toBeNull();
      expect(document.activeElement).toBe(button);
      expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    } finally {
      wrapper.unmount();
      if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
      else Reflect.deleteProperty(navigator, 'clipboard');
      if (commandDescriptor) Object.defineProperty(document, 'execCommand', commandDescriptor);
      else Reflect.deleteProperty(document, 'execCommand');
    }
  },
);

import { mount, flushPromises } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import UploadPanel from "../src/components/UploadPanel.vue";

describe("batch upload", () => {
  it("continues after failure and retries only that file with the original key", async () => {
    const submitted: { name:string; key:string }[]=[];
    const wrapper=mount(UploadPanel,{props:{uploadFile:async(file,key)=>{submitted.push({name:file.name,key});if(submitted.length===1)throw new Error("network failed");}}});
    const input=wrapper.get('input[type="file"]');
    Object.defineProperty(input.element,'files',{value:[new File(['a'],'a.md'),new File(['b'],'b.txt')],configurable:true});
    await input.trigger('change');
    await wrapper.get('.upload-actions button').trigger('click');await flushPromises();
    expect(wrapper.text()).toContain('已提交 1 个');expect(wrapper.text()).toContain('network failed');
    await wrapper.get('.upload-actions button').trigger('click');await flushPromises();
    expect(submitted.map(x=>x.name)).toEqual(['a.md','b.txt','a.md']);expect(submitted[2].key).toBe(submitted[0].key);
    expect(wrapper.text()).toContain('已提交 2 个');
  });
  it("expands folder files and skips unsupported and empty files",async()=>{
    const names:string[]=[];const wrapper=mount(UploadPanel,{props:{uploadFile:async(file)=>{names.push(file.name);}}});
    const input=wrapper.get('input[webkitdirectory]');
    const file=new File(['notes'],'note.md');Object.defineProperty(file,'webkitRelativePath',{value:'folder/note.md'});
    Object.defineProperty(input.element,'files',{value:[file,new File(['x'],'image.png'),new File([],'empty.txt')]});
    await input.trigger('change');expect(wrapper.text()).toContain('folder/note.md');
    await wrapper.get('.upload-actions button').trigger('click');await flushPromises();expect(names).toEqual(['note.md']);expect(wrapper.text()).toContain('已跳过');
  });
});

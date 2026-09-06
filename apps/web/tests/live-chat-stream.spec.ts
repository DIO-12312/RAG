import {describe,it,expect} from "vitest";
import {http,HttpResponse} from "msw";
import {server} from "../src/mocks/server";
import {streamChat} from "../src/api/chat";
describe("HTTP chat stream",()=>{
 it("decodes events split across byte boundaries",async()=>{
  server.use(http.post("*/chat/stream",()=>{
   const bytes=new TextEncoder().encode('event: token\ndata: {"text":"你好"}\n\nevent: final\ndata: {"answer":"你好","citations":[],"conversationId":"c"}\n\n');
   return new HttpResponse(new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(new Uint8Array([byte]));controller.close();}}),{headers:{"Content-Type":"text/event-stream"}});
  }));const result=[];for await(const event of streamChat({datasetId:"d",question:"q"}).events)result.push(event);
  expect(result).toEqual([{type:"token",text:"你好"},{type:"final",answer:"你好",citations:[],conversationId:"c"}]);
 });
 it("rejects a truncated connection without final",async()=>{server.use(http.post("*/chat/stream",()=>new HttpResponse('event: token\ndata: {"text":"partial"}\n\n')));const run=async()=>{for await(const event of streamChat({datasetId:"d",question:"q"}).events)void event;};await expect(run()).rejects.toThrow("中断");});
});

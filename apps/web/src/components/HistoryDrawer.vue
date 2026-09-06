<script setup lang="ts">
import { ref } from "vue";
defineProps<{ items: {id:string;title:string;datasetId:string;updatedAt:string}[]; busy:boolean; selected:string }>();
const emit=defineEmits<{ select:[id:string]; new:[]; refresh:[] }>();
const dialog=ref<HTMLDialogElement>();
function open():void{ emit('refresh'); dialog.value?.showModal(); }
function select(id:string):void{emit('select',id);dialog.value?.close();}
function time(value:string):string{const date=new Date(value);return Number.isNaN(date.getTime())?'':new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(date);}
</script>
<template>
  <button
    class="button-quiet"
    aria-haspopup="dialog"
    @click="open"
  >
    历史会话
  </button>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="history-drawer"
      aria-labelledby="history-title"
    >
      <header>
        <div>
          <h2 id="history-title">
            历史会话
          </h2><small>按最近活动时间排序</small>
        </div><button
          class="button-quiet"
          aria-label="关闭历史会话"
          @click="dialog?.close()"
        >
          关闭
        </button>
      </header><button
        :disabled="busy"
        @click="emit('new');dialog?.close()"
      >
        新建会话
      </button><p
        v-if="!items.length"
        class="history-empty"
      >
        暂无会话，开始一次新的提问吧。
      </p><ol>
        <li
          v-for="item in items"
          :key="item.id"
        >
          <button
            :disabled="busy"
            :aria-current="item.id===selected?'true':undefined"
            @click="select(item.id)"
          >
            <strong>{{ item.title }}</strong><time :datetime="item.updatedAt">{{ time(item.updatedAt) }}</time>
          </button>
        </li>
      </ol>
    </dialog>
  </Teleport>
</template>

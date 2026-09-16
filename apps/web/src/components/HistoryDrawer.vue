<script setup lang="ts">
import { computed, ref } from "vue";
const props=defineProps<{ items: {id:string;title:string;datasetId:string;updatedAt:string}[]; busy:boolean; selected:string }>();
const emit=defineEmits<{ select:[id:string]; new:[]; refresh:[]; delete:[ids:string[]] }>();
const dialog=ref<HTMLDialogElement>();
const selectedIds=ref<string[]>([]);
const allSelected=computed(()=>props.items.length>0&&selectedIds.value.length===props.items.length);
function toggleSelected(id:string):void{selectedIds.value=selectedIds.value.includes(id)?selectedIds.value.filter(item=>item!==id):[...selectedIds.value,id];}
function toggleAll():void{selectedIds.value=allSelected.value?[]:props.items.map(item=>item.id);}
function deleteSelected():void{if(!selectedIds.value.length||props.busy)return;emit('delete',[...selectedIds.value]);selectedIds.value=[];}
function open():void{ emit('refresh'); dialog.value?.showModal(); }
function closeOnBackdrop(event:MouseEvent):void{if(event.target===dialog.value)dialog.value?.close();}
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
      @click="closeOnBackdrop"
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
      </header><div class="history-actions">
        <button
          :disabled="busy || !items.length"
          @click="toggleAll"
        >
          {{ allSelected ? '取消全选' : '全选' }}
        </button>
        <button
          :disabled="busy || !selectedIds.length"
          @click="deleteSelected"
        >
          删除已选{{ selectedIds.length ? `（${selectedIds.length}）` : '' }}
        </button>
        <button
          :disabled="busy"
          @click="emit('new');dialog?.close()"
        >
          新建会话
        </button>
      </div><p
        v-if="!items.length"
        class="history-empty"
      >
        暂无会话，开始一次新的提问吧。
      </p><ol>
        <li
          v-for="item in items"
          :key="item.id"
        >
          <label class="history-item">
            <input
              type="checkbox"
              :checked="selectedIds.includes(item.id)"
              :disabled="busy"
              :aria-label="`选择会话：${item.title}`"
              @click.stop
              @change="toggleSelected(item.id)"
            >
            <button
              :disabled="busy"
              :aria-current="item.id===selected?'true':undefined"
              @click="select(item.id)"
            >
              <strong>{{ item.title }}</strong><time :datetime="item.updatedAt">{{ time(item.updatedAt) }}</time>
            </button>
          </label>
        </li>
      </ol>
    </dialog>
  </Teleport>
</template>

<script setup lang="ts">import {computed,ref} from 'vue';import {useLab} from '../stores/laboratory';import {useAuth} from '../stores/auth';import {createBatch,revokeBatch,activateBatch} from '../api/recalculations';import {useTask} from '../hooks/useTask';import {batchStatuses,versionStatuses} from '../constants/enums';import {number,date} from '../utils/format';
const lab=useLab(),auth=useAuth(),{busy,error,perform}=useTask(),selected=ref<number[]>([]),form=ref({lambda_ratio:1,note:''}),message=ref('');
const candidates=computed(()=>lab.runs.filter(r=>r.status==='completed'&&r.active_version_id));
const openBatches=computed(()=>lab.batches.filter(b=>['queued','running','ready'].includes(b.status)));
const claimedBy=computed(()=>{const map:{[runId:number]:number}={};for(const batch of openBatches.value)for(const item of batch.items){if(['queued','running','ready'].includes(item.status))map[item.run_id]=batch.id;}return map;});
const runName=(id:number)=>lab.runs.find(r=>r.id===id)?.name||('#'+id);
function toggle(id:number){if(claimedBy.value[id])return;selected.value=selected.value.includes(id)?selected.value.filter(x=>x!==id):[...selected.value,id];}
function submit(){perform(async()=>{const batch=await createBatch({run_ids:selected.value,lambda_ratio:form.value.lambda_ratio,note:form.value.note});message.value=batch.reused?`同批请求已受理，沿用批次 #${batch.id}，研究不重建。`:`重算批次 #${batch.id} 已进入队列，整批算齐后才可统一启用。`;selected.value=[];await lab.load();});}
function revoke(id:number){perform(async()=>{await revokeBatch(id);message.value=`批次 #${id} 已撤销，相关研究可被新批次接替。`;await lab.load();});}
function activate(id:number){perform(async()=>{const batch=await activateBatch(id);message.value=`批次 #${batch.id} 已整批启用，各研究切换到 v${batch.items.map(i=>i.version_no).join(' / v')}。`;await lab.load();});}
</script>
<template>
<section class="recalc-panel">
  <div class="section-heading"><h2>仪器误差方差比重算</h2><span>RECALIBRATION / 版本化批次</span></div>
  <p class="input-note">实验室重测误差方差比后，选中受影响的研究并写下新的 λ。每份研究保留原启用版本供资格档案复算；新结果只有在整批算齐并统一启用后才替换当前结果。计算失败时整批不启用，各研究继续使用旧结果。</p>
  <ErrorNotice :message="error" @retry="perform(lab.load)"/>
  <form v-if="auth.canEdit" class="recalc-form" @submit.prevent="submit">
    <fieldset>
      <legend>选择研究 <small>{{selected.length}} 份已选</small></legend>
      <div class="run-picker">
        <label v-for="r in candidates" :key="r.id" :class="{claimed:!!claimedBy[r.id]}">
          <input type="checkbox" :checked="selected.includes(r.id)" :disabled="!!claimedBy[r.id]" @change="toggle(r.id)">
          <span><b>#{{r.id}} {{r.name}}</b><small>当前结果 v{{r.active_version_no}} · λ={{r.lambda_ratio}} · X {{r.range_low}}—{{r.range_high}}%</small></span>
          <em v-if="claimedBy[r.id]">批次 #{{claimedBy[r.id]}} 占用中</em>
        </label>
        <p v-if="!candidates.length" class="muted">暂无已归档研究可重算。</p>
      </div>
    </fieldset>
    <div class="form-grid two">
      <label>新的误差方差比 λ<input type="number" v-model.number="form.lambda_ratio" min=".01" max="100" step=".01" required></label>
      <label>变更依据<textarea v-model="form.note" rows="2" maxlength="600" placeholder="如：2026-11 重复性研究，候选仪器误差方差为参考的0.6倍"></textarea></label>
    </div>
    <button class="primary" :disabled="busy||!selected.length">{{busy?'正在提交…':'按新 λ 整批重算 →'}}</button>
    <p v-if="message" class="success" role="status">{{message}}</p>
  </form>
  <div class="batch-list">
    <article v-for="b in lab.batches" :key="b.id" class="batch-card">
      <div class="batch-heading">
        <div><p class="eyebrow">BATCH / {{String(b.id).padStart(3,'0')}} <small>{{b.created_by}}</small></p><h3>λ={{b.lambda_ratio}} · {{b.items.length}} 份研究</h3></div>
        <span class="status" :class="b.status">{{batchStatuses[b.status]||b.status}}</span>
      </div>
      <p v-if="b.note" class="input-note">{{b.note}}</p>
      <table>
        <thead><tr><th>研究</th><th>由版本</th><th>候选版本</th><th>项状态</th><th>指纹</th></tr></thead>
        <tbody><tr v-for="i in b.items" :key="i.id">
          <td><b>{{i.run_name}}</b></td>
          <td>v{{i.base_version_no}}</td>
          <td>v{{i.version_no}} <small>λ={{i.lambda_ratio}}</small></td>
          <td><span class="status" :class="i.status">{{versionStatuses[i.status]||i.status}}</span><small v-if="i.error">{{i.error}}</small></td>
          <td class="fingerprint-cell">{{i.fingerprint?i.fingerprint.slice(0,16)+'…':'—'}}</td>
        </tr></tbody>
      </table>
      <p v-if="b.error" class="caution">{{b.error}}</p>
      <footer>
        <small>{{date(b.created_at)}}<template v-if="b.activated_at"> · 启用 {{date(b.activated_at)}}</template><template v-if="b.revoked_at"> · 撤销 {{date(b.revoked_at)}}</template></small>
        <div v-if="auth.canEdit">
          <button v-if="['queued','running','ready'].includes(b.status)" class="outline" :disabled="busy" @click="revoke(b.id)">撤销批次</button>
          <button v-if="b.status==='ready'" class="primary" :disabled="busy" @click="activate(b.id)">整批启用新结果 ✓</button>
        </div>
      </footer>
    </article>
    <p v-if="!lab.batches.length" class="muted">尚无重算批次。选中研究并提交新 λ 后，这里会显示整批进度。</p>
  </div>
</section>
</template>

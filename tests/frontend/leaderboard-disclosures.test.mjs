import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import { createDisclosureState, runPanelKey } from '../../web/leaderboard/disclosures.mjs';
import { renderDevelopment, renderRunUpgrades } from '../../web/leaderboard/details.mjs';

function documentFor(t, html) {
  const dom = new JSDOM(html);
  t.after(() => dom.window.close());
  return dom.window.document;
}
const markup = '<details data-fold-key="a"><summary>A</summary><details data-fold-key="b"><summary>B</summary>内容</details></details>';

test('运行面板身份不随显示ID或排序变化，不同运行和模块互不串状态', () => {
  assert.equal(runPanelKey({session_id:'sid',run_id:'old'},'tools'),runPanelKey({session_id:'sid',run_id:'new'},'tools'));
  assert.notEqual(runPanelKey({session_id:'a'},'tools'),runPanelKey({session_id:'b'},'tools'));
  assert.notEqual(runPanelKey({session_id:'a'},'tools'),runPanelKey({session_id:'a'},'run'));
  assert.match(runPanelKey({run_id:'legacy'},'run'),/legacy/);
});

test('默认收起，捕获尚未派发toggle的状态后可恢复重绘', t => {
  const document=documentFor(t,markup),state=createDisclosureState();
  state.capture(document,'advanced/current');
  const [a,b]=document.querySelectorAll('details');
  a.open=true;b.open=true;
  state.capture(document,'advanced/current');
  document.body.innerHTML=markup;
  state.restore(document);
  assert.ok([...document.querySelectorAll('details')].every(e=>e.open));
  const empty=createDisclosureState();empty.restore(document);
  assert.ok([...document.querySelectorAll('details')].every(e=>!e.open));
});

test('筛选隐藏的运行保留状态，已关闭的可见面板不复活', t => {
  const document=documentFor(t,markup),state=createDisclosureState();
  state.capture(document,'advanced/current');
  for(const e of document.querySelectorAll('details')) {e.open=true;state.remember(e);}
  document.body.innerHTML='<details data-fold-key="a"><summary>A</summary></details>';
  state.capture(document,'advanced/current');
  document.body.innerHTML=markup;
  state.restore(document);
  assert.equal(document.querySelector('[data-fold-key=a]').open,false);
  assert.equal(document.querySelector('[data-fold-key=b]').open,true);
});

test('切换榜单或显式重置后不从旧DOM补回展开状态', t => {
  const document=documentFor(t,markup),state=createDisclosureState();
  state.capture(document,'advanced/current');
  document.querySelector('details').open=true;
  state.remember(document.querySelector('details'));
  state.reset();
  state.capture(document,'advanced/current');
  state.restore(document);
  assert.equal(document.querySelector('details').open,false);
  document.querySelector('details').open=true;state.remember(document.querySelector('details'));
  state.capture(document,'full/history');state.restore(document);
  assert.equal(document.querySelector('details').open,false);
});

test('旧节点排队的toggle事件不会覆盖新节点状态', t => {
  const document=documentFor(t,markup),state=createDisclosureState();
  state.capture(document,'advanced/current');
  const old=document.querySelector('details');old.open=true;state.remember(old);
  document.body.innerHTML=markup;
  old.open=false;state.remember(old);
  state.restore(document);
  assert.equal(document.querySelector('details').open,true);
});

function rendering() {
  const source=readFileSync(new URL('../../web/leaderboard/app.js',import.meta.url),'utf8');
  const labels=source.slice(source.indexOf('function toolLabel('),source.indexOf('// Tab Navigation'));
  const functions=source.slice(source.indexOf('function renderCardDetail('),source.indexOf('function rankContributorItems('));
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const context=vm.createContext({esc,fmtInt:v=>String(v),fmtRankScore:v=>v==null?null:String(v),fmtTimestamp:v=>v||'—',fmtDuration:v=>v==null?null:`${v}s`,runPanelKey,renderDevelopment,renderRunUpgrades,
    renderRankContributors:values=>values?.length?'<div class="contrib-chips">队伍贡献</div>':'',
    renderAdventurerResults:values=>values?.length?'<div class="adventurer-result-grid">累计战绩</div>':'',
    _modelNotes:{},_modelBadges:new Map(),renderBadges:()=>''});
  vm.runInContext(labels+functions,context);
  return context;
}
function run(id,score) {
  return {session_id:id,run_id:`directory-${id}`,created_at:'20261021-120000',rank_score:score,timing:{total_seconds:60},token_usage:{input_tokens:100,output_tokens:20},
    preset:'advanced',protocol_hash:`hash-${id}`,benchmark_version:'snapshot-test',game_seed:42,scoring_seed:20260529,
    tool_calls:{by_name:Object.fromEntries(Array.from({length:13},(_,i)=>[`tool-${i}`,1]))},
    game_actions:{adventurer_stats:[{adventurer_id:'a'}]},rank_score_per_adventurer:[{adventurer_id:'a'}],
    upgrades:[{name:'训练',gold_cost:100,experience_cost:80}],
    development:{challenges:{defeated:1,total:3,entries:[]},refresh:{previews:0,paid:0,failed:0,unconfirmed:0,known_gold_spent:0,unknown_cost_count:0},seals:[],events:[{type:'upgrade',turn:3,name:'训练',costs:{gold:100,experience:80,materials:{}}}],unlocated_purchases:0},
  };
}

test('运行摘要默认关闭但直接显示日期、分数、耗时和挑战进度', t => {
  const html=rendering().renderRunCard(run('one',100),1,1),document=documentFor(t,html);
  const card=document.querySelector('details.run-card');
  assert.equal(card.open,false);
  const summary=card.querySelector(':scope > summary');
  for(const value of ['20261021-120000','100','60s','挑战 1/3']) assert.ok(summary.textContent.includes(value));
  assert.equal(summary.title,'directory-one');
  assert.ok(document.querySelector('.contrib-chips'));
  assert.ok(document.querySelector('.development-summary'));
  assert.ok([...document.querySelectorAll('details')].every(e=>!e.open));
});

test('原生折叠只保留运行和模块两层，工具和天赋不继续套娃', t => {
  const document=documentFor(t,rendering().renderRunCard(run('one',100),1,1));
  assert.equal(document.querySelectorAll('.tool-chip').length,13);
  for(const detail of document.querySelectorAll('details')) {
    let depth=1,parent=detail.parentElement.closest('details');
    while(parent) {depth++;parent=parent.parentElement.closest('details');}
    assert.ok(depth<=2);
    assert.ok(detail.getAttribute('data-fold-key'));
  }
  const panels=[...document.querySelectorAll('.run-section-fold > summary')].map(e=>e.textContent);
  assert.ok(panels.some(s=>s.includes('天赋')));
  assert.ok(panels.some(s=>s.includes('工具') && s.includes('13种')));
  assert.ok(panels.some(s=>s.includes('天赋') && s.includes('1项')));
  assert.ok(panels.some(s=>s.includes('配置')));
});

test('各次运行保留自己的配置指纹，模型首层不再重复最新一局的配置', t => {
  const context=rendering();
  const html=context.renderCardDetail({model:'同一模型',rank_score:{mean:95,best:100,median:95},run_details:[run('one',100),run('two',90)]});
  const document=documentFor(t,html),cards=document.querySelectorAll('.run-card');
  assert.equal(cards.length,2);
  assert.ok(cards[0].querySelector('.run-config-grid').textContent.includes('hash-one'));
  assert.ok(cards[1].querySelector('.run-config-grid').textContent.includes('hash-two'));
  assert.ok(cards[0].querySelector('.run-config-grid').textContent.includes('20260529'));
  assert.equal(document.querySelector('.aggregate-list'),null);
});

test('摘要标识及配置文本转义，缺失耗时不冒充零', t => {
  const value=run('"><img src=x>',100);value.run_id=value.session_id;delete value.timing;
  const document=documentFor(t,rendering().renderRunCard(value,1,1));
  assert.equal(document.querySelector('img'),null);
  assert.match(document.querySelector('.run-summary-duration').textContent,/未记录/);
  assert.equal(document.querySelector('.run-card').getAttribute('data-fold-key'),runPanelKey(value,'run'));
});

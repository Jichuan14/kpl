<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { fetchDraftModel } from "./api";
import { selectedLeagueId } from "./selectedLeague";
import { finishStartupLoading } from "./startupLoader";

const activeSection = ref("relations");
const model = ref(null);
const demoStep = ref(0);
const demoPlaying = ref(false);
let demoTimer = null;

const demoActions = [
  { id: "a", label: "对方 Pick A", relation: "对方 Pick" },
  { id: "b", label: "我方 Pick B", relation: "己方 Pick" },
  { id: "c", label: "对方 Pick C", relation: "对方 Pick" },
];

const vectorCells = [0, 1, 2, 3, 4, 5, 6, 7];
const candidateScores = [
  { hero: "英雄 X", bag: 74, gru: 18, final: 78 },
  { hero: "英雄 Y", bag: 61, gru: -8, final: 59 },
  { hero: "英雄 Z", bag: 49, gru: 24, final: 55 },
];

const demoStages = [
  "输入当前已发生的三手 BP。下一手轮到我方操作。",
  "Bag 分支读取全部历史动作，并聚合为当前局面的基线表示。",
  "GRU 先读入第一手：对方 Pick A，形成 h₁。",
  "GRU 再读入第二手：我方 Pick B，形成 h₂。",
  "GRU 最后读入第三手：对方 Pick C，形成 h₃。",
  "两个 query 分别为所有当前合法英雄计算分数。",
  "Bag 分数加上缩放后的 GRU 顺序修正，得到最终排序。",
];

const demoStageText = computed(() => demoStages[demoStep.value]);

function setDemoStep(step) {
  demoStep.value = Math.max(0, Math.min(step, demoStages.length - 1));
  if (demoStep.value === demoStages.length - 1) stopDemo();
}

function stopDemo() {
  demoPlaying.value = false;
  if (demoTimer) window.clearInterval(demoTimer);
  demoTimer = null;
}

function toggleDemo() {
  if (demoPlaying.value) {
    stopDemo();
    return;
  }
  if (demoStep.value === demoStages.length - 1) demoStep.value = 0;
  demoPlaying.value = true;
  demoTimer = window.setInterval(() => {
    if (demoStep.value >= demoStages.length - 1) stopDemo();
    else demoStep.value += 1;
  }, 1200);
}

const sections = [
  ["relations", "1. 四种英雄关系统计"],
  ["bag", "2. Bag 基线分支"],
  ["gru", "3. GRU 顺序分支"],
  ["scoring", "4. 候选英雄打分"],
  ["fusion", "5. 两个分支如何合并"],
  ["training", "6. 训练、评估与线上推理"],
  ["lineup-value", "7. 完整阵容价值模型"],
  ["ban-value", "8. 专用 Ban 价值模型"],
  ["lineup-scoring", "9. 完整 5v5 阵容评分"],
];

function scrollToSection(id) {
  activeSection.value = id;
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function updateActiveSection() {
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
    activeSection.value = sections.at(-1)[0];
    return;
  }
  const current = sections
    .map(([id]) => document.getElementById(id))
    .filter(Boolean)
    .filter((element) => element.getBoundingClientRect().top <= 150)
    .at(-1);
  if (current) activeSection.value = current.id;
}

async function loadModel() {
  try {
    model.value = await fetchDraftModel(selectedLeagueId.value);
  } catch {
    model.value = null;
  } finally {
    finishStartupLoading();
  }
}

onMounted(async () => {
  await loadModel();
  await nextTick();
  const hash = window.location.hash.slice(1);
  if (hash) scrollToSection(hash === "rankings" ? "training" : hash);
  window.addEventListener("scroll", updateActiveSection, { passive: true });
  updateActiveSection();
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", updateActiveSection);
  stopDemo();
});
</script>

<template>
  <main class="methodology-page">
    <header class="page-header">
      <p class="eyebrow">方法说明</p>
      <h1>BP 预测、Ban/Pick 推荐与阵容评分</h1>
      <p>
        本页说明网站目前使用的三层方法。Bag + GRU 模型先判断职业比赛中下一手最可能出现什么；Pick 推荐再模拟后续 BP，并用完整阵容价值模型评价最终 5v5；Ban 推荐则由专用 Ban 价值模型估计限制对手的收益。完整阵容确定后，页面还可以直接给双方阵容评分和各项贡献。
      </p>
      <p v-if="model?.sequenceModel?.available" class="model-note">
        当前线上模型：{{ model.sequenceModel.name }}；隐藏维度 {{ model.sequenceModel.hiddenDim }}；残差系数 α = {{ Number(model.sequenceModel.residualAlpha).toFixed(4) }}。
      </p>

      <div class="process-demo" aria-label="Bag 加 GRU 预测过程演示">
        <div class="demo-heading">
          <p class="demo-title">过程演示</p>
          <span>示意计算，不代表当前局面的真实预测数值</span>
        </div>
        <div class="demo-controls">
          <button type="button" :disabled="demoStep === 0" @click="setDemoStep(demoStep - 1)">上一步</button>
          <button type="button" @click="toggleDemo">{{ demoPlaying ? "暂停" : "自动播放" }}</button>
          <button type="button" :disabled="demoStep === demoStages.length - 1" @click="setDemoStep(demoStep + 1)">下一步</button>
          <button type="button" @click="stopDemo(); setDemoStep(0)">重置</button>
        </div>

        <div class="model-visual" aria-live="polite">
          <div class="visual-input">
            <p class="visual-label">历史 BP token</p>
            <div class="token-stream">
              <template v-for="(action, index) in demoActions" :key="action.id">
                <div class="hero-token" :class="{ selected: demoStep >= index + 2 }"><span>t{{ index + 1 }}</span>{{ action.label }}</div>
                <span v-if="index < demoActions.length - 1" class="flow-arrow">→</span>
              </template>
              <div class="next-action">下一手：我方 Pick</div>
            </div>
          </div>

          <div class="branch-grid">
            <div class="branch bag-branch" :class="{ muted: demoStep < 1 }">
              <div class="branch-heading"><strong>Bag 分支</strong><span>不强调输入顺序</span></div>
              <div class="bag-inputs"><div v-for="action in demoActions" :key="`bag-${action.id}`" class="small-token">{{ action.relation }}</div></div>
              <div class="pool-arrow">聚合 ↓</div>
              <div class="vector-state"><span class="vector-name">p_bag</span><span v-for="cell in vectorCells" :key="`bag-cell-${cell}`" class="vector-cell" :style="{ opacity: demoStep >= 1 ? 0.36 + ((cell * 17) % 55) / 100 : 0.12 }"></span></div>
            </div>

            <div class="branch gru-branch" :class="{ muted: demoStep < 2 }">
              <div class="branch-heading"><strong>GRU 分支</strong><span>按真实 BP 顺序读取</span></div>
              <div class="gru-chain">
                <template v-for="(action, index) in demoActions" :key="`gru-${action.id}`">
                  <div class="gru-unit" :class="{ active: demoStep >= index + 2 }">
                    <div class="small-token">{{ action.label }}</div><div class="gru-cell">GRU</div>
                    <div class="hidden-vector"><span>h{{ index + 1 }}</span><i v-for="cell in vectorCells.slice(0, 5)" :key="cell" :style="{ opacity: demoStep >= index + 2 ? 0.38 + ((cell * 19 + index * 13) % 54) / 100 : 0.12 }"></i></div>
                  </div>
                  <span v-if="index < demoActions.length - 1" class="flow-arrow">→</span>
                </template>
              </div>
            </div>
          </div>

          <div class="score-flow" :class="{ muted: demoStep < 5 }">
            <div class="query-source"><span>q_bag</span><span>q_gru</span></div><span class="flow-arrow">→</span>
            <div class="score-list">
              <div v-for="candidate in candidateScores" :key="candidate.hero" class="score-row">
                <span>{{ candidate.hero }}</span><div class="score-bar"><i class="bag-score" :style="{ width: `${candidate.bag}%` }"></i></div><div class="score-bar gru-score"><i :style="{ width: `${Math.abs(candidate.gru)}%`, marginLeft: candidate.gru < 0 ? '30%' : '50%' }"></i></div>
              </div>
            </div>
            <span class="flow-arrow">→</span>
            <div class="final-rank" :class="{ ready: demoStep >= 6 }"><span>最终排序</span><strong>1. 英雄 X</strong><strong>2. 英雄 Y</strong><strong>3. 英雄 Z</strong></div>
          </div>
        </div>
        <p class="demo-description">第 {{ demoStep + 1 }} 步：{{ demoStageText }}</p>
      </div>
    </header>

    <div class="methodology-layout">
      <nav class="section-nav" aria-label="页面目录">
        <button
          v-for="[id, label] in sections"
          :key="id"
          :class="{ active: activeSection === id }"
          @click="scrollToSection(id)"
        >
          {{ label }}
        </button>
      </nav>

      <article>
        <section id="relations">
          <h2>1. 四种英雄关系统计</h2>
          <p>
            关系统计不是模型本身，而是从历史比赛中直接计算得到的可解释参考。所有统计都只在“目标英雄当时仍可选择”的局面中计数，避免把已经被 Ban 或 Pick 的英雄算进分母。
          </p>

          <pre>opportunities(B) = 英雄 B 在对应历史局面中仍然合法的次数</pre>
          <pre>selections(A → B) = 在这些局面中，随后实际选择 B 的次数</pre>
          <pre>raw_rate(A → B) = selections(A → B) / opportunities(B)</pre>

          <p>
            页面显示的不是单纯的 <code>raw_rate</code>。样本很小时，1 次命中 / 1 次机会会错误地显示为 100%。因此系统会使用平滑后的概率、全局基准概率和 Wilson 置信区间来排序；样本过少的关系不会作为强结论展示。
          </p>

          <h3>1.1 同队 Pick 协同</h3>
          <p>
            当一支队伍已经 Pick 英雄 A，随后该队伍在 B 仍合法时 Pick 英雄 B，就记为一次 <strong>A → B</strong> 的同队协同样本。它回答的是：已经有 A 时，这支队伍是否更常补出 B。
          </p>

          <h3>1.2 对方 Pick 后的反制 Pick</h3>
          <p>
            当对手先 Pick 英雄 A，而当前队伍之后在 B 仍合法时 Pick 英雄 B，就记为一次反制 Pick 样本。它回答的是：面对 A 时，B 是否更常被拿出来应对。
          </p>

          <h3>1.3 对方 Pick 后的反制 Ban</h3>
          <p>
            当对手先 Pick 英雄 A，而当前队伍之后在 B 仍合法时 Ban 英雄 B，就记为一次反制 Ban 样本。它反映的是：面对 A 后，哪些英雄更常被主动移出对局。
          </p>

          <h3>1.4 Ban 之后的后续响应</h3>
          <p>
            当英雄 A 被 Ban 后，系统继续观察后续仍合法的英雄 B：B 可能被另一方 Ban，也可能被任意一方 Pick。这个统计用于查看一个 Ban 是否常常伴随其他英雄的连锁处理。
          </p>

          <p class="plain-note">
            这些关系是历史共现和条件选择频率，不代表英雄之间存在因果关系。版本、战队、位置、赛制和样本量都会影响结果。
          </p>
        </section>

        <section id="bag">
          <h2>2. Bag 基线分支</h2>
          <p>Bag 分支的作用是先建立一个稳定的“当前 BP 局面基线判断”。</p>
          <p>
            它会读取当前已经发生的所有 Ban/Pick，但不严格区分它们的先后顺序。也就是说，它知道：
          </p>
          <blockquote>当前双方已经 Ban/Pick 了哪些英雄、这些动作属于谁、是 Ban 还是 Pick。</blockquote>
          <p>但它不会特别强调“这个英雄是两手之前选的，还是五手之前选的”。</p>
          <p>每一个历史动作先变成一个向量：</p>

          <pre>uₜ = tanh(WₛH(heroₜ) + E_action(actionₜ) + E_relation(relationₜ))</pre>

          <ul>
            <li><code>t</code>：第 <code>t</code> 个历史 BP 动作。</li>
            <li><code>uₜ</code>：该历史动作经过 Bag 分支编码后的 48 维向量。</li>
            <li><code>heroₜ</code>：第 <code>t</code> 手涉及的英雄。</li>
            <li><code>H(heroₜ)</code>：该英雄的 48 维表示，由英雄的 99 维结构化特征和英雄专属 embedding 共同生成。</li>
            <li><code>Wₛ</code>：可学习的线性投影矩阵，用于转换英雄表示。</li>
            <li><code>E_action(actionₜ)</code>：该手是 Ban 还是 Pick 的 embedding。</li>
            <li><code>actionₜ</code>：动作类型，取值为 <code>ban</code> 或 <code>pick</code>。</li>
            <li><code>E_relation(relationₜ)</code>：该动作相对于当前执行方的关系 embedding。</li>
            <li><code>relationₜ</code>：四种情况之一：己方 Pick、对方 Pick、己方 Ban、对方 Ban。</li>
            <li><code>tanh</code>：非线性函数，让模型可以表达更复杂的特征组合。</li>
          </ul>

          <p>然后把所有历史动作聚合：</p>

          <pre>p_bag = (Σₜ₌₁ᴸ uₜ) / √L</pre>

          <ul>
            <li><code>L</code>：当前已经发生的 BP 动作数量。</li>
            <li><code>Σₜ₌₁ᴸ uₜ</code>：把历史动作向量相加。</li>
            <li><code>p_bag</code>：当前局面的整体 Bag 表示。</li>
            <li><code>√L</code>：长度归一化项，防止后期 BP 因为动作更多而让向量数值过大。</li>
          </ul>

          <p>例如下面两种局面，在 Bag 分支看来会比较接近：</p>

          <pre>局面一
对方 Pick A → 我方 Pick B → 对方 Pick C</pre>

          <pre>局面二
对方 Pick C → 我方 Pick B → 对方 Pick A</pre>

          <p>因为它们包含的英雄和关系相似；这正是 GRU 分支随后要补充的部分。</p>
          <p>最后，Bag 分支结合“下一步是什么动作”生成预测 query：</p>

          <pre>q_bag = tanh(W_b p_bag + b_b + c)</pre>
          <pre>c = E_next_action + E_next_side + E_next_position + E_team_slot + E_acting_team + E_opponent_team + S_own + S_opponent</pre>

          <ul>
            <li><code>q_bag</code>：Bag 分支用来给候选英雄打分的 48 维查询向量。</li>
            <li><code>W_b</code>：将聚合局面 <code>p_bag</code> 投影到预测空间的可学习矩阵。</li>
            <li><code>b_b</code>：该投影层的 bias。</li>
            <li><code>c</code>：下一步 BP 的上下文向量。</li>
            <li><code>E_next_action</code>：下一步是 Ban 还是 Pick。</li>
            <li><code>E_next_side</code>：下一步是蓝方还是红方操作。</li>
            <li><code>E_next_position</code>：下一步是完整 BP 的第几手。</li>
            <li><code>E_team_slot</code>：该队伍第几次执行这一类动作，例如“红方第 2 次 Pick”。</li>
            <li><code>E_acting_team</code>：当前执行操作的战队 embedding。</li>
            <li><code>E_opponent_team</code>：对手战队 embedding。</li>
            <li><code>S_own</code>、<code>S_opponent</code>：同一系列赛此前小局中，当前队伍和对手已使用英雄的聚合表示。首局或单局预测中，这两项为零。</li>
          </ul>

          <p>
            最终，Bag 分支会得到一个基线判断：当前双方阵容、Ban/Pick 信息、战队偏好和下一步阶段共同表明，哪些英雄总体上更可能成为下一次选择。这个判断比较稳定；GRU 分支则在其基础上，根据严格的 BP 顺序做进一步修正。
          </p>
        </section>

        <section id="gru">
          <h2>3. GRU 顺序分支</h2>
          <p>
            GRU 分支解决的是 Bag 分支不区分顺序的问题。它把每一次历史 Ban/Pick 当作序列中的一个 token，并按真实发生顺序逐手读取。
          </p>

          <pre>xₜ = H(heroₜ) + E_action(actionₜ) + E_side(sideₜ) + E_relation(relationₜ) + E_position(t)</pre>
          <pre>hₜ = GRU(xₜ, hₜ₋₁)</pre>
          <pre>q_gru = tanh(W_g h_L + b_g + c)</pre>

          <ul>
            <li><code>xₜ</code>：第 <code>t</code> 手的输入向量。</li>
            <li><code>H(heroₜ)</code>：该英雄的 48 维表示，与 Bag 分支使用同一类英雄表示。</li>
            <li><code>E_action(actionₜ)</code>：该手是 Ban 或 Pick。</li>
            <li><code>E_side(sideₜ)</code>：执行这一手的是蓝方还是红方。</li>
            <li><code>E_relation(relationₜ)</code>：这一手相对当前执行方是己方还是对方、是 Pick 还是 Ban。</li>
            <li><code>E_position(t)</code>：这一手在完整 BP 中的位置 embedding。</li>
            <li><code>hₜ</code>：GRU 读完第 <code>t</code> 手后的隐藏状态，保存此前顺序相关的信息。</li>
            <li><code>hₜ₋₁</code>：读当前动作前的隐藏状态。</li>
            <li><code>h_L</code>：读完当前全部 <code>L</code> 手后的最终状态。</li>
            <li><code>W_g</code>、<code>b_g</code>：把最终隐藏状态映射到预测空间的可学习参数。</li>
            <li><code>c</code>：与 Bag 分支相同的下一步上下文向量。</li>
            <li><code>q_gru</code>：GRU 分支给候选英雄打分使用的 48 维 query。</li>
          </ul>

          <p>
            因此，“对方先 Pick A，两手后我方 Pick B”和“对方先 Pick C，再 Pick A，最后我方 Pick B”会产生不同的 <code>h_L</code>。GRU 能学习到 B 是紧接着对 A 的回应，还是在另一套阵容条件下出现的选择。
          </p>

          <h3>3.1 系列赛上下文</h3>
          <p>
            一场 BO 并不是完全独立的多局比赛。前一局已经出现过的英雄，常常会影响下一局的优先级、处理方式和可选阵容。为此，当前模型会分别收集双方在本系列此前完成的小局中使用过的英雄，并把两组集合编码为额外上下文。
          </p>

          <pre>S_own = (Σ E_prev_own(hero)) / √max(n_own, 1)</pre>
          <pre>S_opponent = (Σ E_prev_opponent(hero)) / √max(n_opponent, 1)</pre>

          <p>
            两组表示会加入 Bag 和 GRU 的下一手 query。它们不替代当前局的 Ban/Pick 历史，而是回答一个更具体的问题：在双方已经展示过这些英雄后，这一局接下来更可能如何延续或变化。当前局没有前序小局时，模型会自然退化为原来的单局预测方式。
          </p>
        </section>

        <section id="scoring">
          <h2>4. 候选英雄打分</h2>
          <p>模型会为每一个英雄建立候选表示，再分别与 Bag query 和 GRU query 做点积。</p>

          <pre>R(j) = W_f f(j) + e_hero(j)</pre>
          <pre>logits_bag(j) = q_bag · R(j) + b_hero(j)</pre>
          <pre>logits_gru(j) = q_gru · R(j) + b_hero(j)</pre>

          <ul>
            <li><code>j</code>：一个候选英雄。</li>
            <li><code>f(j)</code>：英雄 <code>j</code> 的 99 维结构化特征，例如职业、定位和游戏内属性。</li>
            <li><code>W_f</code>：把 99 维结构化特征投影到 48 维空间的可学习矩阵。</li>
            <li><code>e_hero(j)</code>：英雄 <code>j</code> 的专属 48 维 embedding。</li>
            <li><code>R(j)</code>：候选英雄 <code>j</code> 的最终 48 维表示。</li>
            <li><code>q_bag · R(j)</code>：Bag 当前局面与英雄 <code>j</code> 的匹配分数。</li>
            <li><code>q_gru · R(j)</code>：严格 BP 顺序与英雄 <code>j</code> 的匹配分数。</li>
            <li><code>b_hero(j)</code>：英雄 <code>j</code> 的学习到的基础偏置。</li>
          </ul>

          <p>已经被 Ban 或 Pick 的英雄会被 mask 掉：</p>

          <pre>logits(j) = −∞,  如果英雄 j 当前不合法</pre>
          <pre>P(j) = softmax(logits)(j),  只在当前合法英雄之间归一化</pre>

          <p>
            所以即使某个英雄的历史选择率很高，只要它在当前 BP 已经不可用，模型就不会把它放进预测结果。
          </p>
        </section>

        <section id="fusion">
          <h2>5. 两个分支如何合并</h2>
          <p>
            当前线上模型不是让 GRU 完全覆盖 Bag，而是让 GRU 作为对稳定基线的顺序修正。
          </p>

          <pre>μ_gru = mean(logits_gru(j)),  j ∈ 当前合法英雄</pre>
          <pre>center(logits_gru(j)) = logits_gru(j) − μ_gru</pre>
          <pre>α = sigmoid(a)</pre>
          <pre>logits_final(j) = logits_bag(j) + α · center(logits_gru(j))</pre>

          <ul>
            <li><code>logits_bag(j)</code>：Bag 分支对英雄 <code>j</code> 的稳定基线判断。</li>
            <li><code>logits_gru(j)</code>：GRU 分支对英雄 <code>j</code> 的顺序判断。</li>
            <li><code>μ_gru</code>：所有当前合法英雄的 GRU logit 平均值。</li>
            <li><code>center(...)</code>：减去平均值。这样 GRU 主要负责提高或降低英雄之间的相对排序，不会整体改变 logit 的基准。</li>
            <li><code>a</code>：训练时学习的标量参数。</li>
            <li><code>α</code>：经过 sigmoid 后的残差系数，范围在 0 到 1 之间。</li>
            <li><code>logits_final(j)</code>：最终用于 softmax 和排序的分数。</li>
          </ul>

          <p>
            <code>α</code> 由训练过程自动学习，而不是预先固定。它让模型根据数据决定顺序信息应当以多大幅度修正稳定的 Bag 基线：顺序信息有价值，但不应在样本有限时完全取代对当前局面的整体判断。
          </p>
        </section>

        <section id="training">
          <h2>6. 训练、评估与线上推理</h2>
          <p>
            训练数据中的每一个样本都是一个真实的历史 BP 前缀：输入为当时已经发生的动作和下一步上下文，标签为真实发生的下一手英雄。训练时最小化真实英雄的负对数概率：
          </p>

          <pre>loss = −log P(y | 历史 BP, 下一步上下文)</pre>

          <ul>
            <li><code>y</code>：历史比赛中真实被 Ban 或 Pick 的下一位英雄。</li>
            <li><code>P(y | ...)</code>：模型在当前合法英雄集合中分配给真实英雄 <code>y</code> 的概率。</li>
            <li><code>loss</code>：真实英雄排名越靠前，损失越低。</li>
          </ul>

          <p>
            当前模型先在按时间划分的数据上选择训练轮数：最近 10 场作为验证集，再之后的 10 场作为留出测试集。加入系列赛上下文后，899 个留出测试决策上的 Top-1 为 24.69%，Top-5 为 58.18%，负对数损失为 2.8295。相比未加入系列赛上下文的同一模型，Top-1 从 22.91% 提升到 24.69%，Top-5 从 56.73% 提升到 58.18%。
          </p>

          <p>
            留出测试并不参与梯度训练，也不用于挑选最终轮数；它只用于记录这次改动在未见比赛上的表现。完成评估后，线上发布的模型会使用同一套已确定的参数，在当前五个赛季窗口内全部 38,820 个可用决策上重新训练。这样既保留一份可比较的离线结果，也让线上模型学习到最新比赛的完整信息。
          </p>

          <p>
            结果并不表示每个 BP 阶段都同样容易。开局 Ban 和首抢的留出 Top-1 为 47.11%，而第二轮 Ban 为 11.48%。后者仍是目前最不稳定的阶段：候选空间较大，且它更容易受到阵容、版本和临场策略变化影响。因此页面展示的是条件概率和排序，不把单次预测解释为确定的赛场结论。
          </p>

          <span id="rankings" aria-hidden="true"></span>
        </section>

        <section id="lineup-value">
          <h2>7. 完整阵容价值模型</h2>
          <p>
            下一手预测回答“历史上更可能选谁”，并不等于“选谁以后阵容更强”。因此 Pick 推荐会把候选英雄带入后续 BP，模拟双方继续 Ban/Pick，等两边都形成五人阵容后，再由一个独立的阵容价值模型评价结果。
          </p>

          <h3>7.1 八组阵容特征</h3>
          <p>每个完整 5v5 会被转换成八个蓝方相对红方的差值。正值通常有利于蓝方，负值通常有利于红方。</p>

          <ul>
            <li><strong>队伍强度：</strong>双方赛前 Elo 的差值，并除以 400 统一尺度。</li>
            <li><strong>英雄熟练度：</strong>双方队伍使用各自五名英雄时的历史表现差。</li>
            <li><strong>分路覆盖：</strong>阵容能否较完整地覆盖五个位置，以及是否需要明显的错位安排。</li>
            <li><strong>机制协同：</strong>同队英雄在坦度、开团、控制、伤害类型等结构上的互补程度。</li>
            <li><strong>机制反制：</strong>双方英雄机制在对位层面的克制关系。</li>
            <li><strong>联赛组合协同：</strong>英雄两两同队出现时，在整个联赛中的历史超额表现。</li>
            <li><strong>队伍组合协同：</strong>同一英雄组合在指定队伍手中的历史超额表现。</li>
            <li><strong>历史反制优势：</strong>蓝方五名英雄对红方五名英雄的 25 组方向性历史对抗效果。</li>
          </ul>

          <p>
            “双坦克”“强开团”或“硬控较多”会进入机制描述，但不会被人工固定为加分规则。模型只会在历史数据证明该信息能提供额外预测力时给予权重。这样可以避免把直觉重复计分，也避免在版本变化后继续沿用已经失效的经验。
          </p>

          <h3>7.2 小样本收缩与最终分数</h3>
          <p>
            熟练度、组合协同和反制效果都可能遇到小样本。系统先计算相对于基础胜率的残差，再向零收缩：样本越少，效果越接近中性；样本越多，历史信号保留得越完整。
          </p>

          <pre>effect = 4 × residual_sum / (observations + prior)</pre>
          <pre>evidence = observations / (observations + prior)</pre>

          <ul>
            <li><code>residual_sum</code>：实际结果相对于该场基础预期的累计偏差。</li>
            <li><code>observations</code>：该队伍、英雄组合或对位的有效历史样本数。</li>
            <li><code>prior</code>：收缩强度。它越大，小样本越不容易产生极端结论。</li>
            <li><code>evidence</code>：证据充分度，范围为 0 到 1。</li>
          </ul>

          <p>八个特征先按训练数据中的均值和尺度标准化，再由逻辑回归合并：</p>

          <pre>contributionᵢ = ((featureᵢ − meanᵢ) / scaleᵢ) × coefficientᵢ</pre>
          <pre>logit = intercept + Σ contributionᵢ</pre>
          <pre>blue_advantage = sigmoid(logit)</pre>
          <pre>red_advantage = 1 − blue_advantage</pre>

          <p>
            页面展示的每一项贡献就是上式中的 <code>contributionᵢ</code>。贡献为正表示该项把结果推向蓝方，为负表示推向红方；绝对值越大，说明该项对本次相对排序的影响越大。它不是单独的一项胜率。
          </p>

          <h3>7.3 Pick 推荐如何使用阵容模型</h3>
          <p>
            系统先由 Bag + GRU 模型保留当前最符合真实 BP 行为的 10 个合法 Pick。对每个候选，系统强制执行这一手，再按策略模型模拟 24 条合法的后续 BP 路径，直到得到完整阵容。阵容价值模型会分别评价这 24 个终局，并转换为当前行动方的收益。
          </p>

          <pre>expected_value = 24 个模拟终局收益的平均值</pre>
          <pre>uncertainty = 24 个模拟终局收益的标准差</pre>
          <pre>robust_value = expected_value − risk_penalty × uncertainty</pre>

          <p>
            平衡模式的风险系数为 0.35，稳健模式为 0.75，进取模式为 0。也就是说，稳健模式会更明显地降低“平均结果不错、但不同后续走向差异很大”的候选；进取模式只比较平均收益。
          </p>
        </section>

        <section id="ban-value">
          <h2>8. 专用 Ban 价值模型</h2>
          <p>
            Ban 的目标不是完成我方阵容，而是减少对手下一阶段可获得的价值。用阵容模型猜测若干未来 Pick，容易把“模型想象中的未来”误当成真实 Ban 理由。因此当前 Ban 推荐使用独立模型，并只在策略模型认为行为合理的 30 个合法 Ban 中比较。
          </p>

          <h3>8.1 先把历史结果向联赛基准平滑</h3>
          <p>全局英雄表现、队伍英雄表现、对手英雄表现和历史 Ban 结果都使用相同的小样本保护：</p>

          <pre>smoothed_rate = (weighted_wins + prior × baseline) / (weighted_games + prior)</pre>
          <pre>signal = smoothed_rate − baseline</pre>
          <pre>evidence = weighted_games / (weighted_games + prior)</pre>

          <p>
            越新的赛季权重越高，向前每隔一个赛季乘以 0.65。线上工件会使用目标赛季及此前所有可用赛季，而不是固定只取最近四季；这样较早数据仍能提供稀有英雄和组合的基础信息，但影响会逐季下降。
          </p>

          <h3>8.2 Ban 价值的五个组成部分</h3>
          <p>候选英雄的原始 Ban 分数由限制对手的收益减去对我方自己的损失，再加入行为真实性修正：</p>

          <pre>opponent_denial = 0.75 × global_pick_effect
                + 1.35 × opponent_team_hero_effect
                + 0.35 × opponent_preference</pre>
          <pre>context_denial = 0.70 × synergy_with_opponent_visible_picks
               + 0.85 × counter_strength_against_our_visible_picks</pre>
          <pre>observed_ban_outcome = 0.45 × global_ban_effect
                     + 0.70 × ban_effect_against_this_opponent</pre>
          <pre>self_cost = 0.75 × acting_team_hero_effect
          + 0.25 × acting_team_preference</pre>

          <p>
            <code>opponent_denial</code> 评价候选是否是对手擅长或偏好的英雄；<code>context_denial</code> 评价它与对方已亮英雄的协同，以及对我方已亮英雄的克制；<code>observed_ban_outcome</code> 参考历史上禁掉该英雄后的关联结果；<code>self_cost</code> 防止系统优先禁掉我方自己更需要的英雄。
          </p>

          <p>模型还会保留职业 BP 的行为约束，避免推荐理论分数很高、但真实赛场几乎不会出现的 Ban：</p>

          <pre>learned_behavior = 0.65 × global_ban_frequency
                 + 0.35 × opponent_specific_ban_frequency</pre>
          <pre>combined_behavior = 0.70 × BP_policy_probability
                  + 0.30 × learned_behavior</pre>
          <pre>behavior_realism = 0.08 × log(combined_behavior / strongest_policy_probability)</pre>
          <pre>raw_ban_value = opponent_denial + context_denial
              + observed_ban_outcome − self_cost + behavior_realism</pre>
          <pre>ban_value = sigmoid(4 × raw_ban_value)</pre>

          <h3>8.3 不确定性与 Global BP</h3>
          <p>
            Ban 模型也会根据各项历史证据计算不确定性，并使用与 Pick 推荐相同的稳健分数。证据较少时，不确定性最多增加 0.08；稳健模式会更明显地惩罚这类候选。
          </p>

          <pre>uncertainty = 0.08 × (1 − average_evidence)</pre>
          <pre>robust_value = ban_value − risk_penalty × uncertainty</pre>

          <p>
            在 Global BP 中，如果对手已经在本系列使用过某英雄、因而不能再 Pick，该英雄的对手威胁和阵容上下文收益会归零；如果我方已经使用过它，自我损失也会归零。模型因此按当前系列赛真正剩余的英雄池计算，而不是照搬普通 BP 的偏好。
          </p>

          <blockquote>
            Ban value 是历史关联、对手限制价值和行为真实性的综合排序，不是“执行这一 Ban 后必然获胜”的因果概率。历史 Ban 的复现指标也只说明模型能否接近真实选择，不能证明真实选择本身就是最优策略。
          </blockquote>
        </section>

        <section id="lineup-scoring">
          <h2>9. 完整 5v5 阵容评分</h2>
          <p>
            当双方都已经有五名不重复英雄时，模拟器会自动调用同一个完整阵容价值模型，并直接计算一次精确评分。这里不会继续生成未来 BP，也不会平均多条模拟路径。
          </p>

          <pre>当前 BP 状态
├─ 下一手是 Pick → 策略模型筛选候选 → 每个候选模拟 24 个终局 → 阵容价值排序
├─ 下一手是 Ban  → 策略模型筛选候选 → 专用 Ban 价值模型排序
└─ 已完成 5v5    → 对当前两套阵容直接评分一次</pre>

          <h3>9.1 页面上的分数代表什么</h3>
          <ul>
            <li><strong>蓝方阵容分 / 红方阵容分：</strong>由 <code>sigmoid(logit)</code> 得到，双方互补为 100%。它适合比较同一赛季、同一模型下的相对阵容优势，不应直接当作经过校准的赛场胜率。</li>
            <li><strong>队伍强度：</strong>只汇总 Elo 差带来的贡献。</li>
            <li><strong>英雄熟练度：</strong>只汇总双方对所选英雄历史掌握程度的贡献。</li>
            <li><strong>阵容协同：</strong>汇总分路覆盖、机制协同、联赛组合协同和队伍组合协同。</li>
            <li><strong>英雄反制：</strong>汇总机制反制与 25 组方向性历史反制效果。</li>
          </ul>

          <pre>hero_synergy = role_coverage + mechanics_ally
             + league_pair_synergy + team_pair_synergy</pre>
          <pre>hero_counters = mechanics_counter + historical_counter</pre>

          <p>
            分组贡献为正时推动蓝方得分，为负时推动红方得分。它们是在标准化后乘以模型系数得到的 logit 贡献，因此不能直接相加为百分点，也不能脱离当前模型与基准单独解释。
          </p>

          <h3>9.2 直接评分与 Pick 推荐的区别</h3>
          <p>
            直接评分评价的是页面上已经确定的唯一一套 5v5；Pick 推荐评价的是“现在选择某英雄后，24 种可能后续 BP 的平均结果”。因此推荐值会同时受到未来路径和不确定性的影响，即使最终某一套阵容的直接评分很高，也不表示在更早的 BP 阶段一定容易走到这套阵容。
          </p>

          <p class="plain-note">
            完整阵容评分只使用现有的赛季阵容模型工件进行计算，不需要为每次评分生成新的后端数据。重新训练或切换赛季后，均值、尺度、系数和历史关系统计会随对应工件一起更新。
          </p>
        </section>
      </article>
    </div>
  </main>
</template>

<style scoped>
.methodology-page {
  max-width: 1120px;
  margin: 0 auto;
  padding: 44px 28px 80px;
  color: #20242b;
}

.page-header {
  max-width: 780px;
  border-bottom: 1px solid #d9dde3;
  padding-bottom: 28px;
  margin-bottom: 30px;
}

.eyebrow {
  margin: 0 0 8px;
  color: #687385;
  font-size: 0.88rem;
  letter-spacing: 0.08em;
}

h1, h2, h3 {
  color: #15191f;
  font-weight: 650;
  line-height: 1.3;
}

h1 {
  margin: 0 0 14px;
  font-size: clamp(1.9rem, 4vw, 2.7rem);
}

h2 {
  font-size: 1.55rem;
  margin: 0 0 18px;
  scroll-margin-top: 22px;
}

h3 {
  margin: 28px 0 8px;
  font-size: 1.08rem;
}

p, li {
  font-size: 1rem;
  line-height: 1.85;
}

p {
  margin: 0 0 14px;
}

.model-note, .plain-note {
  color: #5d6776;
  font-size: 0.94rem;
}

.process-demo {
  border-top: 1px solid #d9dde3;
  margin-top: 24px;
  padding-top: 20px;
}

.demo-heading {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.demo-title {
  color: #15191f;
  font-weight: 650;
  margin-bottom: 10px;
}

.demo-heading span {
  color: #687385;
  font-size: 0.82rem;
}

.demo-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.demo-controls button {
  background: #fff;
  border: 1px solid #bac2cd;
  border-radius: 3px;
  color: #26364f;
  cursor: pointer;
  font: inherit;
  font-size: 0.9rem;
  padding: 6px 10px;
}

.demo-controls button:hover:not(:disabled) {
  border-color: #283f72;
}

.demo-controls button:disabled {
  color: #9ba3ae;
  cursor: default;
}

.model-visual {
  border: 1px solid #d5dbe3;
  display: grid;
  gap: 17px;
  padding: 18px;
}

.visual-label, .branch-heading {
  color: #526072;
  font-size: 0.84rem;
  margin: 0 0 8px;
}

.token-stream, .bag-inputs, .gru-chain, .score-flow, .query-source {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.hero-token, .next-action, .small-token, .query-source span, .final-rank {
  border: 1px solid #cbd1d9;
  border-radius: 3px;
  color: #26364f;
  font-size: 0.8rem;
  line-height: 1.35;
  padding: 5px 7px;
}

.hero-token {
  background: #f8f9fa;
  opacity: 0.5;
  transition: border-color 180ms ease, opacity 180ms ease, transform 180ms ease;
}

.hero-token span {
  color: #7a8595;
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.71rem;
}

.hero-token.selected {
  border-color: #7185af;
  opacity: 1;
  transform: translateY(-2px);
}

.next-action { background: #f0f4f9; }
.flow-arrow, .pool-arrow { color: #738094; font-size: 0.9rem; }

.branch-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: 1fr 1fr;
}

.branch {
  border-left: 2px solid #9da9b8;
  min-height: 136px;
  padding-left: 11px;
  transition: opacity 180ms ease;
}

.branch.muted, .score-flow.muted { opacity: 0.22; }

.branch-heading {
  align-items: baseline;
  display: flex;
  gap: 9px;
}

.branch-heading strong { color: #26364f; font-weight: 650; }
.branch-heading span { color: #7a8595; font-size: 0.76rem; }

.small-token { background: #f5f6f8; }
.pool-arrow { margin: 10px 0 6px; }

.vector-state, .hidden-vector {
  align-items: center;
  display: flex;
  gap: 3px;
}

.vector-name, .hidden-vector span {
  color: #526072;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem;
  margin-right: 4px;
}

.vector-cell, .hidden-vector i {
  background: #49699f;
  display: inline-block;
  height: 17px;
  transition: opacity 240ms ease;
  width: 10px;
}

.gru-chain { align-items: stretch; gap: 5px; }
.gru-unit { display: grid; gap: 5px; justify-items: center; opacity: 0.38; transition: opacity 180ms ease; }
.gru-unit.active { animation: unit-enter 260ms ease-out; opacity: 1; }
.gru-cell { background: #eef3fb; border: 1px solid #9dadd0; border-radius: 3px; color: #304e7d; font-size: 0.78rem; padding: 4px 10px; }
.hidden-vector i { height: 12px; width: 7px; }
.hidden-vector span { margin: 0 2px 0 0; }

.score-flow { border-top: 1px solid #e0e4e9; padding-top: 15px; transition: opacity 180ms ease; }
.query-source { align-items: stretch; flex-direction: column; }
.query-source span { background: #f1f4f8; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.score-list { display: grid; gap: 5px; min-width: 190px; }
.score-row { align-items: center; display: grid; gap: 5px; grid-template-columns: 47px 1fr 1fr; }
.score-row > span { color: #526072; font-size: 0.77rem; }
.score-bar { background: #e7eaee; height: 7px; overflow: hidden; position: relative; }
.score-bar i { background: #536f9f; display: block; height: 100%; transition: width 260ms ease; }
.gru-score::before { background: #b6bdc7; content: ""; height: 100%; left: 50%; position: absolute; width: 1px; }
.gru-score i { background: #71879a; }
.final-rank { display: grid; gap: 2px; min-width: 92px; opacity: 0.4; transition: opacity 260ms ease, border-color 260ms ease; }
.final-rank.ready { border-color: #78947e; opacity: 1; }
.final-rank span { color: #526072; font-size: 0.72rem; }
.final-rank strong { color: #26364f; font-size: 0.78rem; font-weight: 550; }

.demo-description {
  color: #526072;
  font-size: 0.93rem;
  margin: 14px 0 0;
}

@keyframes unit-enter {
  from { opacity: 0; transform: translateX(-7px); }
  to { opacity: 1; transform: translateX(0); }
}

.methodology-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 760px);
  gap: 54px;
}

.section-nav {
  align-self: start;
  position: sticky;
  top: 24px;
  display: flex;
  flex-direction: column;
  border-left: 1px solid #d9dde3;
}

.section-nav button {
  appearance: none;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: #687385;
  cursor: pointer;
  font: inherit;
  font-size: 0.9rem;
  padding: 8px 12px;
  text-align: left;
}

.section-nav button:hover, .section-nav button.active {
  border-left-color: #283f72;
  color: #1c2c50;
}

section {
  padding: 12px 0 46px;
  border-bottom: 1px solid #e2e5e9;
}

section:last-child {
  border-bottom: 0;
}

blockquote {
  border-left: 3px solid #8b98ad;
  color: #394354;
  margin: 18px 0;
  padding: 2px 18px;
  line-height: 1.8;
}

pre {
  background: #f5f6f8;
  border: 1px solid #e1e4e8;
  border-radius: 4px;
  color: #1c2735;
  font: 0.91rem/1.75 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  margin: 18px 0;
  overflow-x: auto;
  padding: 15px 18px;
  white-space: pre-wrap;
}

ul {
  margin: 10px 0 18px;
  padding-left: 24px;
}

li {
  margin: 5px 0;
}

code {
  background: #f1f3f5;
  border-radius: 3px;
  color: #29394f;
  font-size: 0.91em;
  padding: 1px 4px;
}

@media (max-width: 780px) {
  .methodology-page {
    padding: 30px 18px 56px;
  }

  .methodology-layout {
    display: block;
  }

  .section-nav {
    position: static;
    margin-bottom: 28px;
  }

  .branch-grid {
    grid-template-columns: 1fr;
  }

  .score-flow {
    align-items: flex-start;
    flex-direction: column;
  }

  .gru-chain {
    align-items: center;
  }

  .gru-unit .small-token {
    max-width: 78px;
    text-align: center;
  }
}
</style>

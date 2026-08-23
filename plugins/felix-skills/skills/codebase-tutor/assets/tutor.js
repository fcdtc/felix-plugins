/* ============================================================
   codebase-tutor 共享交互脚本
   复制到 <repo>/.learn/assets/tutor.js，所有课程共用。
   三个组件全部由 HTML 上的 data-* 属性驱动，agent 只填数据，不写 JS。

   1. 执行步进器  .tutor-stepper  + data-steps='[...]'
   2. 预测题      .tutor-quiz     + data-feedback='...'
   3. 数据流动画  .tutor-flow     + data-flow-steps='[...]'
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 组件一：执行步进器 ---------- */
  // data-steps 结构：
  // [
  //   { "line": 3, "vars": { "req.method": "GET" }, "note": "中间件开始解析请求" },
  //   ...
  // ]
  // line 为代码块内 1 起始的行号；vars 为本轮快照（全量）；note 为该步解说。
  function initStepper(el) {
    var steps;
    try { steps = JSON.parse(el.getAttribute('data-steps')); }
    catch (e) { console.error('tutor stepper: data-steps JSON 解析失败', e); return; }

    var lines = el.querySelectorAll('.stepper-line');
    var varsBox = el.querySelector('.stepper-vars');
    var noteEl = el.querySelector('.stepper-note');
    var posEl = el.querySelector('.pos');
    var prevBtn = el.querySelector('[data-action="prev"]');
    var nextBtn = el.querySelector('[data-action="next"]');
    var i = -1;

    function render() {
      lines.forEach(function (ln, idx) {
        ln.classList.toggle('active', idx === (i >= 0 ? steps[i].line - 1 : -2));
        ln.classList.toggle('executed', i >= 0 && idx < steps[i].line - 1);
      });
      varsBox.innerHTML = '';
      if (i >= 0) {
        var vars = steps[i].vars || {};
        Object.keys(vars).forEach(function (name) {
          var chip = document.createElement('span');
          chip.className = 'stepper-var';
          chip.innerHTML = '<span class="var-name"></span> = <span class="var-value"></span>';
          chip.querySelector('.var-name').textContent = name;
          chip.querySelector('.var-value').textContent = vars[name];
          var prev = i > 0 ? (steps[i - 1].vars || {}) : {};
          if (!(name in prev) || String(prev[name]) !== String(vars[name])) {
            chip.classList.add('changed');
          }
          varsBox.appendChild(chip);
        });
        noteEl.textContent = steps[i].note || '';
      } else {
        noteEl.textContent = '点击「下一步」开始逐步执行 👇';
      }
      posEl.textContent = (i + 1) + ' / ' + steps.length;
      prevBtn.disabled = i < 0;
      nextBtn.disabled = i >= steps.length - 1;
      nextBtn.classList.toggle('primary', i < steps.length - 1);
    }

    prevBtn.addEventListener('click', function () { if (i >= 0) { i--; render(); } });
    nextBtn.addEventListener('click', function () { if (i < steps.length - 1) { i++; render(); } });
    render();
  }

  /* ---------- 组件二：预测题（三选一，点击即反馈） ---------- */
  // 正确项由 data-correct="true" 标记；data-feedback 为答案解说（必填）。
  function initQuiz(el) {
    var options = el.querySelectorAll('.quiz-option');
    var feedback = el.querySelector('.quiz-feedback');
    var answered = false;

    options.forEach(function (opt) {
      opt.addEventListener('click', function () {
        if (answered) return;
        answered = true;
        var isCorrect = opt.getAttribute('data-correct') === 'true';
        options.forEach(function (o) {
          o.disabled = true;
          if (o.getAttribute('data-correct') === 'true') o.classList.add('correct');
        });
        if (!isCorrect) opt.classList.add('wrong');
        feedback.classList.add('show', isCorrect ? 'correct' : 'wrong');
        feedback.querySelector('.verdict').textContent =
          isCorrect ? '✓ 答对了' : '✗ 再想想 —— 正确答案已标出';
      });
    });
  }

  /* ---------- 组件三：数据流 / 消息流动画 ---------- */
  // HTML 内按顺序放 .flow-node 与 .flow-arrow；data-flow-steps 结构：
  // [
  //   { "highlight": [0, 1], "desc": "请求从入口进入路由层" },
  //   ...
  // ]
  // highlight 为节点/箭头混合序列中的下标（节点与箭头统一按出现顺序编号）。
  function initFlow(el) {
    var steps;
    try { steps = JSON.parse(el.getAttribute('data-flow-steps')); }
    catch (e) { console.error('tutor flow: data-flow-steps JSON 解析失败', e); return; }

    var items = el.querySelectorAll('.flow-node, .flow-arrow');
    var descEl = el.querySelector('.flow-step-desc');
    var posEl = el.querySelector('.pos');
    var prevBtn = el.querySelector('[data-action="prev"]');
    var nextBtn = el.querySelector('[data-action="next"]');
    var i = -1;

    function render() {
      var lit = i >= 0 ? (steps[i].highlight || []) : [];
      items.forEach(function (n, idx) { n.classList.toggle('lit', lit.indexOf(idx) !== -1); });
      descEl.textContent = i >= 0 ? (steps[i].desc || '') : '点击「下一步」追踪一次完整旅程 👇';
      posEl.textContent = (i + 1) + ' / ' + steps.length;
      prevBtn.disabled = i < 0;
      nextBtn.disabled = i >= steps.length - 1;
      nextBtn.classList.toggle('primary', i < steps.length - 1);
    }

    prevBtn.addEventListener('click', function () { if (i >= 0) { i--; render(); } });
    nextBtn.addEventListener('click', function () { if (i < steps.length - 1) { i++; render(); } });
    render();
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.tutor-stepper').forEach(initStepper);
    document.querySelectorAll('.tutor-quiz').forEach(initQuiz);
    document.querySelectorAll('.tutor-flow').forEach(initFlow);
  });
})();

"use strict";
(() => {
  const theme = document.getElementById("theme-choice");
  theme.addEventListener("change", () => {
    if (theme.value === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme.value);
  });
  const button = document.getElementById("tool-drilldown-toggle");
  const row = document.getElementById("tool-drilldown-row");
  const label = document.getElementById("tool-drilldown-label");
  if (!button || !row || !label) return;
  button.addEventListener("click", () => {
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(expanded));
    row.hidden = !expanded;
    label.textContent = expanded ? "收起 ▴" : "展开 ▾";
  });
})();

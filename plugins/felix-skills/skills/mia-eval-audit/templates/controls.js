"use strict";
(() => {
  const theme = document.getElementById("theme-choice");
  theme.addEventListener("change", () => {
    if (theme.value === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme.value);
  });
  const search = document.getElementById("case-search");
  const status = document.getElementById("case-status");
  const table = document.getElementById("case-table");
  if (!table) return;
  const rows = Array.from(table.querySelectorAll("tbody tr"));
  const count = document.getElementById("visible-count");
  const filter = () => {
    let visible = 0;
    const needle = search.value.trim().toLocaleLowerCase();
    rows.forEach(row => {
      const matchesText = row.textContent.toLocaleLowerCase().includes(needle);
      const matchesStatus = !status.value || row.cells[2].textContent.trim() === status.value;
      row.hidden = !(matchesText && matchesStatus);
      if (!row.hidden) visible += 1;
    });
    count.textContent = "显示 " + visible + " / " + rows.length + " 个 case；筛选不改变统计分母。";
  };
  search.addEventListener("input", filter);
  status.addEventListener("change", filter);
  filter();
})();

(() => {
  const key = "meisei-bus-appearance-v1";
  const themes = ["light", "dark", "system"];
  const fonts = ["system", "sans", "serif", "rounded", "soft", "handwritten", "kaisei", "kurenaido", "klee", "yusei"];
  const webFonts = {
    rounded: "M+PLUS+Rounded+1c:wght@400;700",
    soft: "Zen+Maru+Gothic:wght@400;700",
    handwritten: "Yomogi",
    kaisei: "Kaisei+Decol:wght@400;700",
    kurenaido: "Zen+Kurenaido",
    klee: "Klee+One:wght@400;600",
    yusei: "Yusei+Magic"
  };
  const requestedFonts = new Set();
  let settings = { theme: "light", font: "system", textSize: "standard" };
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (themes.includes(saved?.theme)) settings.theme = saved.theme;
    if (fonts.includes(saved?.font)) settings.font = saved.font;
    if (["standard", "large"].includes(saved?.textSize)) settings.textSize = saved.textSize;
  } catch {}
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  function apply() {
    const dark = settings.theme === "dark" || (settings.theme === "system" && preference.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.dataset.font = settings.font;
    document.documentElement.dataset.textSize = settings.textSize;
    // Load only the chosen Japanese web font; other options remain unloaded.
    if (webFonts[settings.font] && !requestedFonts.has(settings.font)) {
      const requestedFont = settings.font;
      const stylesheet = document.createElement("link");
      stylesheet.rel = "stylesheet";
      stylesheet.href = `https://fonts.googleapis.com/css2?family=${webFonts[settings.font]}&display=swap`;
      stylesheet.addEventListener("error", () => requestedFonts.delete(requestedFont));
      document.head.append(stylesheet);
      requestedFonts.add(settings.font);
    }
    document.querySelector('meta[name="theme-color"]').content = dark ? "#111c2c" : "#f1f4f8";
  }
  function save() {
    apply();
    try { localStorage.setItem(key, JSON.stringify(settings)); }
    catch { document.getElementById("settingsNote").textContent = "設定を保存できないため、今回は開いている間だけ適用します。"; }
  }
  apply();
  preference.addEventListener("change", () => { if (settings.theme === "system") apply(); });
  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.getElementById("settingsDialog");
    document.querySelectorAll('input[name="theme"]').forEach(input => {
      input.checked = input.value === settings.theme;
      input.addEventListener("change", () => { if (input.checked) { settings.theme = input.value; save(); } });
    });
    const select = document.getElementById("fontSetting");
    select.value = settings.font;
    select.addEventListener("change", () => { settings.font = select.value; save(); });
    const textSize = document.getElementById("textSizeSetting");
    textSize.value = settings.textSize;
    textSize.addEventListener("change", () => { settings.textSize = textSize.value; save(); });
    document.getElementById("openSettings").addEventListener("click", () => dialog.showModal());
    ["closeSettings", "doneSettings"].forEach(id => document.getElementById(id).addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
    });
  });
})();
// Progressive enhancement only — all actions work without JS.
// Kept external so the CSP can stay `script-src 'self'` (no inline scripts).
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  const input = document.querySelector("input[data-host-preview]");
  const preview = document.getElementById("host-preview");
  if (input && preview) {
    const domain = input.dataset.hostPreview;
    const sync = () => {
      const value = (input.value || "name").toLowerCase();
      preview.textContent = value + "." + domain;
    };
    input.addEventListener("input", sync);
    sync();
  }
});

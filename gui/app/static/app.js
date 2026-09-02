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
  const domainSelect = document.getElementById("domain-select");
  if (input && preview) {
    const sync = () => {
      const value = (input.value || "name").toLowerCase();
      const domain = domainSelect ? domainSelect.value : input.dataset.hostPreview;
      preview.textContent = value + "." + domain;
    };
    input.addEventListener("input", sync);
    if (domainSelect) domainSelect.addEventListener("change", sync);
    sync();
  }
});

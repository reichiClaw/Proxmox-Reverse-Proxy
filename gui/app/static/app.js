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

  const nameInput = document.getElementById("name-input");
  const subdomainInput = document.getElementById("subdomain-input");
  const pathInput = document.getElementById("path-input");
  const preview = document.getElementById("host-preview");
  const domainSelect = document.getElementById("domain-select");
  if (preview && (nameInput || subdomainInput)) {
    const sync = () => {
      const name = (nameInput && nameInput.value ? nameInput.value : "name").toLowerCase();
      const label = (
        subdomainInput && subdomainInput.value.trim()
          ? subdomainInput.value
          : name
      ).toLowerCase();
      const domain = domainSelect
        ? domainSelect.value
        : (subdomainInput && subdomainInput.dataset.hostPreview) || "example.com";
      let path = pathInput ? pathInput.value.trim() : "";
      if (path && !path.startsWith("/")) path = "/" + path;
      if (path.length > 1) path = path.replace(/\/+$/, "");
      preview.textContent = "https://" + label + "." + domain + path;
    };
    [nameInput, subdomainInput, pathInput, domainSelect].forEach((el) => {
      if (el) {
        el.addEventListener("input", sync);
        el.addEventListener("change", sync);
      }
    });
    sync();
  }
});

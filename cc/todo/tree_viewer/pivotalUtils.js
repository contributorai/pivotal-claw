(function () {
  function escapeHTML(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[ch]);
  }

  function linkifyHTML(html) {
    return html.replace(/((?:https?:\/\/|file:\/\/\/)[^\s<]+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>');
  }

  function renderInlineMarkdown(text) {
    let html = escapeHTML(text || "");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    return linkifyHTML(html);
  }

  function normalizeText(text) {
    return String(text || "")
      .replace(/\bID:\s*T[-A-Za-z0-9]+\b/g, "")
      .replace(/#[A-Za-z0-9_]+\b/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  /* Story typology (SPEC §5): type derived from reserved tags, checked in order. */
  const storyTypes = [
    { type: "release", icon: "🏁", tags: ["release"] },
    { type: "bug", icon: "🐛", tags: ["bug", "fix"] },
    { type: "chore", icon: "⚙️", tags: ["chore"] }
  ];
  /* Feature renders no icon: on an all-story board the default star carries no signal. */
  const featureType = { type: "feature", icon: "" };
  const reservedTypeTags = new Set(storyTypes.flatMap((entry) => entry.tags));

  function storyTypeFor(tags) {
    const clean = (tags || []).map((tag) => String(tag).replace(/^#/, "").toLowerCase());
    return storyTypes.find((entry) => entry.tags.some((tag) => clean.includes(tag))) || featureType;
  }

  function isReservedTypeTag(tag) {
    return reservedTypeTags.has(String(tag).replace(/^#/, "").toLowerCase());
  }

  function debounce(fn, ms) {
    let timer = null;
    const debounced = (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
    debounced.cancel = () => clearTimeout(timer);
    debounced.flush = (...args) => {
      clearTimeout(timer);
      fn(...args);
    };
    return debounced;
  }

  window.PivotalUtils = { escapeHTML, linkifyHTML, renderInlineMarkdown, normalizeText, debounce, storyTypeFor, isReservedTypeTag };
})();

// Observable Framework configuration.
//
// `root` is set to "site" because the framework defaults to "src", which is
// where the Python package lives. Leaving the default would make the framework
// try to build gpa/*.py as pages.

export default {
  root: "site",
  title: "German Power Market Research",

  pages: [
    { name: "Forecast evidence", path: "/forecast" },
    { name: "Storage value", path: "/battery" },
    { name: "Methodology", path: "/methodology" },
  ],

  theme: ["air", "near-midnight"],
  toc: true,
  sidebar: true,
  pager: true,
  typographicQuotes: true,

  // An inline SVG favicon: the daily price shape this site is about, and one
  // fewer 404 on every page load.
  head: `<link rel="icon" href="data:image/svg+xml,${encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
      '<rect width="32" height="32" rx="6" fill="#0b1b2b"/>' +
      '<path d="M3 12 L9 10 L13 22 L19 6 L23 14 L29 11" fill="none" ' +
      'stroke="#F0E442" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>",
  )}">`,

  header: "",
  footer: ({ path }) =>
    `Built from primary system-operator data. ` +
    `<a href="https://github.com/Pedrods20/global-power-atlas">Source and methodology on GitHub</a>.`,

  // The site is served from a project page, so assets resolve under the
  // repository name rather than the domain root.
  base: process.env.GPA_BASE_PATH ?? "/",

  search: true,
};
